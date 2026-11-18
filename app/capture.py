"""Capture: anything you send becomes tasks. AI sorts it when available,
otherwise the rule-based parser does (one task per line)."""

from __future__ import annotations

import logging
import re

from . import parser
from .ai import AI, AIUnavailable
from .tasks import Tasks

log = logging.getLogger("ticklight.capture")


SHARE_TAG = re.compile(r"(?<!\w)#(shared|home|household)\b", re.I)
# Without AI, a line like "Garage:" files the lines under it (until a blank line) in that project.
PROJECT_HEADING = re.compile(r"^\s*(?:#+\s*)?([^:\n]{1,40}):\s*$")
NOT_PROJECTS = {"today", "tonight", "tomorrow", "this week", "next week", "this weekend", "weekend",
                "later", "someday", "soon", "todo", "to do", "to-do", "tasks", "notes", "ideas",
                "monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"}


class Capture:
    def __init__(self, tasks: Tasks, ai: AI, prefs=None):
        self.tasks = tasks
        self.ai = ai
        self.prefs = prefs

    def record(self, uid: int, text: str, source: str, project_id: int | None = None) -> int:
        """Store a capture. With project_id, everything in it goes into that project."""
        text = (text or "").strip()
        if not text:
            raise ValueError("nothing to capture")
        if len(text) > 20000:
            raise ValueError("that's too long to capture in one go")
        project_id = self.tasks._own_project(uid, project_id)
        with self.tasks.db.tx() as c:
            return c.execute("INSERT INTO captures(user_id, text, source, project_id, created_at)"
                             " VALUES(?,?,?,?,?)", (uid, text, source, project_id, self.tasks._stamp())).lastrowid

    async def process(self, uid: int, capture_id: int, from_user: int | None = None) -> dict:
        row = self.tasks.db.one("SELECT * FROM captures WHERE id=? AND user_id=?",
                                (capture_id, uid))
        if not row:
            raise ValueError("no such capture")
        text = row["text"]
        forced = row["project_id"]
        # "#home" / "#shared" on a line shares that line's task with your people
        share_home = bool(self.prefs and self.prefs.get(uid)["people"]["share_home"])
        projects: dict[str, dict] = {}  # lower-case name -> {"id", "name", "new"}
        try:
            names = [p["name"] for p in self.tasks.projects(uid)]
            items, method = await self.ai.triage(text, self.tasks.now(), names)
            pick = (lambda it: forced) if forced else self._project_picker(uid, capture_id, text, items, projects)
            created = [self._create(uid, capture_id, it, from_user=from_user, project_id=pick(it),
                                    shared=it["shared"] or (share_home and it["area"] == "home"))
                       for it in items]
        except AIUnavailable as e:
            log.info("capture %s: AI unavailable (%s), using parser", capture_id, e)
            method = "parser"
            created = self._parse_lines(uid, capture_id, text, from_user, forced, projects)
        except Exception:
            log.exception("capture %s failed", capture_id)
            with self.tasks.db.tx() as c:
                c.execute("UPDATE captures SET status='failed' WHERE id=?", (capture_id,))
            self.tasks._changed()
            raise
        with self.tasks.db.tx() as c:
            c.execute("UPDATE captures SET status='done', method=? WHERE id=?",
                      (method, capture_id))
        self.tasks._changed()
        return {"capture_id": capture_id, "method": method, "tasks": created,
                "projects": list(projects.values())}

    def _project(self, uid: int, capture_id: int, name: str, projects: dict) -> int:
        key = " ".join(name.split()).lower()
        if key not in projects:
            existing = self.tasks.find_project(uid, name)
            p = existing or self.tasks.create_project(uid, name, capture_id=capture_id)
            projects[key] = {"id": p["id"], "name": p["name"], "new": not existing}
        return projects[key]["id"]

    def _project_picker(self, uid: int, capture_id: int, text: str, items: list[dict], projects: dict):
        """Map the AI's project names to ids. A new project is only started when 2+ tasks
        share it or the person said its name; otherwise the task stays loose."""
        counts: dict[str, int] = {}
        for it in items:
            if it["project"]:
                k = it["project"].lower()
                counts[k] = counts.get(k, 0) + 1
        lowered = text.lower()

        def pick(it: dict) -> int | None:
            name = it["project"]
            if not name:
                return None
            if self.tasks.find_project(uid, name) or counts[name.lower()] >= 2 or name.lower() in lowered:
                return self._project(uid, capture_id, name, projects)
            return None
        return pick

    async def run(self, uid: int, text: str, source: str, from_user: int | None = None) -> dict:
        return await self.process(uid, self.record(uid, text, source), from_user)

    def _blocks(self, text: str) -> list[tuple[str | None, str]]:
        """Split text into (project heading or None, lines) blocks."""
        blocks: list[tuple[str | None, list[str]]] = [(None, [])]
        for raw in text.splitlines():
            m = PROJECT_HEADING.match(raw)
            if m and not raw[:1].isspace() and m.group(1).strip().lower() not in NOT_PROJECTS:
                blocks.append((m.group(1).strip(), []))
            elif not raw.strip() and blocks[-1][0]:
                blocks.append((None, []))  # a blank line ends a project block
            else:
                blocks[-1][1].append(raw)
        return [(name, "\n".join(lines)) for name, lines in blocks if any(x.strip() for x in lines)]

    def _parse_lines(self, uid: int, capture_id: int, text: str, from_user: int | None,
                     forced: int | None = None, projects: dict | None = None) -> list[dict]:
        """No AI: one task per line (v1 quick-add syntax), indented/"+" lines are steps,
        and a "Name:" line files the lines under it in that project."""
        blocks = [(name, parser.split_bulk(body)) for name, body in self._blocks(text)]
        if sum(len(items) for _, items in blocks) > parser.MAX_BULK:
            raise ValueError(f"too many tasks at once (max {parser.MAX_BULK})")
        projects = {} if projects is None else projects
        return [t for name, items in blocks
                for t in self._parse_block(uid, capture_id, items, from_user,
                                           forced or (self._project(uid, capture_id, name, projects) if name else None))]

    def _parse_block(self, uid: int, capture_id: int, items, from_user: int | None,
                     project_id: int | None) -> list[dict]:
        out = []
        for line, subs in items:
            shared = bool(SHARE_TAG.search(line))
            p = parser.parse(SHARE_TAG.sub(" ", line).strip() or line, self.tasks.today())
            t = self.tasks.create(uid, p.title, priority=p.priority, due_date=p.due_date, due_time=p.due_time,
                                  recurrence_rule=p.recurrence, steps=[SHARE_TAG.sub("", x).strip() for x in subs],
                                  capture_id=capture_id, shared=shared, from_user=from_user,
                                  project_id=project_id)
            t["parsed_summary"] = p.summary()
            out.append(t)
        return out

    def _create(self, uid: int, capture_id: int, it: dict, **extra) -> dict:
        p = parser.parse("x " + it["when"], self.tasks.today()) if it["when"] else None
        t = self.tasks.create(
            uid, SHARE_TAG.sub("", it["title"]).strip() or it["title"], priority=it["priority"],
            due_date=p.due_date if p else None, due_time=p.due_time if p else None,
            recurrence_rule=p.recurrence if p else None,
            area=it["area"], energy=it["energy"], minutes=it["minutes"], steps=it["steps"],
            capture_id=capture_id, status="someday" if it["someday"] else "open", **extra)
        t["parsed_summary"] = p.summary() if p else ""
        return t

    def undo(self, uid: int, capture_id: int) -> int:
        """Delete every task a capture created (e.g. the AI misread it)."""
        ids = [r["id"] for r in self.tasks.db.query(
            "SELECT id FROM tasks WHERE capture_id=? AND user_id=? AND status!='deleted'",
            (capture_id, uid))]
        for tid in ids:
            self.tasks.delete(uid, tid)
        # Projects this capture started go too, unless something else was added to them since
        for r in self.tasks.db.query("SELECT id FROM projects WHERE capture_id=? AND user_id=?", (capture_id, uid)):
            if not self.tasks.db.one("SELECT 1 FROM tasks WHERE project_id=? AND status!='deleted'", (r["id"],)):
                self.tasks.delete_project(uid, r["id"])
        return len(ids)

    def recent(self, uid: int, limit: int = 10) -> list[dict]:
        rows = self.tasks.db.query(
            "SELECT c.*, (SELECT COUNT(*) FROM tasks t WHERE t.capture_id=c.id"
            " AND t.status!='deleted') AS task_count FROM captures c WHERE c.user_id=?"
            " ORDER BY c.id DESC LIMIT ?", (uid, limit))
        return [dict(r) for r in rows]
