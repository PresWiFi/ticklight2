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


class Capture:
    def __init__(self, tasks: Tasks, ai: AI, prefs=None):
        self.tasks = tasks
        self.ai = ai
        self.prefs = prefs

    def record(self, uid: int, text: str, source: str) -> int:
        text = (text or "").strip()
        if not text:
            raise ValueError("nothing to capture")
        if len(text) > 20000:
            raise ValueError("that's too long to capture in one go")
        with self.tasks.db.tx() as c:
            return c.execute("INSERT INTO captures(user_id, text, source, created_at)"
                             " VALUES(?,?,?,?)", (uid, text, source, self.tasks._stamp())).lastrowid

    async def process(self, uid: int, capture_id: int, from_user: int | None = None) -> dict:
        row = self.tasks.db.one("SELECT * FROM captures WHERE id=? AND user_id=?",
                                (capture_id, uid))
        if not row:
            raise ValueError("no such capture")
        text = row["text"]
        # "#home" / "#shared" on a line shares that line's task with your people
        share_home = bool(self.prefs and self.prefs.get(uid)["people"]["share_home"])
        try:
            items, method = await self.ai.triage(text, self.tasks.now())
            created = [self._create(uid, capture_id, it, from_user=from_user,
                                    shared=it["shared"] or (share_home and it["area"] == "home"))
                       for it in items]
        except AIUnavailable as e:
            log.info("capture %s: AI unavailable (%s), using parser", capture_id, e)
            method = "parser"
            created = self._parse_lines(uid, capture_id, text, from_user)
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
        return {"capture_id": capture_id, "method": method, "tasks": created}

    async def run(self, uid: int, text: str, source: str, from_user: int | None = None) -> dict:
        return await self.process(uid, self.record(uid, text, source), from_user)

    def _parse_lines(self, uid: int, capture_id: int, text: str, from_user: int | None) -> list[dict]:
        """No AI: one task per line (v1 quick-add syntax), indented/"+" lines are steps."""
        items = parser.split_bulk(text)
        if len(items) > parser.MAX_BULK:
            raise ValueError(f"too many tasks at once (max {parser.MAX_BULK})")
        out = []
        for line, subs in items:
            shared = bool(SHARE_TAG.search(line))
            p = parser.parse(SHARE_TAG.sub(" ", line).strip() or line, self.tasks.today())
            t = self.tasks.create(uid, p.title, priority=p.priority, due_date=p.due_date, due_time=p.due_time,
                                  recurrence_rule=p.recurrence, steps=[SHARE_TAG.sub("", x).strip() for x in subs],
                                  capture_id=capture_id, shared=shared, from_user=from_user)
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
        return len(ids)

    def recent(self, uid: int, limit: int = 10) -> list[dict]:
        rows = self.tasks.db.query(
            "SELECT c.*, (SELECT COUNT(*) FROM tasks t WHERE t.capture_id=c.id"
            " AND t.status!='deleted') AS task_count FROM captures c WHERE c.user_id=?"
            " ORDER BY c.id DESC LIMIT ?", (uid, limit))
        return [dict(r) for r in rows]
