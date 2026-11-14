"""Focus sessions: a timed block on one task, with a companion, check-ins and breaks."""

from __future__ import annotations

import json
from datetime import datetime, timedelta

from .db import DB

MOODS = ("good", "stuck", "distracted")
STALE_MINUTES = 60  # a session this far past its end closes itself


class Sessions:
    def __init__(self, db: DB, tasks, prefs, coach):
        self.db = db
        self.tasks = tasks
        self.prefs = prefs
        self.coach = coach

    # ---------- reading ----------
    def _decorate(self, uid: int, row) -> dict:
        s = dict(row)
        s["checkins"] = json.loads(s["checkins"] or "[]")
        start = datetime.fromisoformat(s["started_at"])
        ends = start + timedelta(minutes=s["minutes"])
        now = self.tasks.now()
        s["ends_at"] = ends.isoformat(timespec="seconds")
        if s["ended_at"]:
            s["state"] = "break" if s["break_until"] and s["break_until"] > now.isoformat(timespec="seconds") else "ended"
        else:
            s["state"] = "running" if now < ends else "overtime"
        s["remaining_s"] = max(0, int((ends - now).total_seconds()))
        s["elapsed_s"] = int(((datetime.fromisoformat(s["ended_at"]) if s["ended_at"] else now) - start).total_seconds())
        n = self.prefs.get(uid)["sessions"]["checkins"]
        due = [start + timedelta(minutes=s["minutes"] * (i + 1) / (n + 1)) for i in range(n)]
        pending = [d for d in due[len(s["checkins"]):]]
        s["next_checkin_at"] = pending[0].isoformat(timespec="seconds") if pending and s["state"] == "running" else None
        s["task"] = None
        if s["task_id"]:
            try:
                s["task"] = self.tasks.get(uid, s["task_id"])
            except Exception:
                pass
        return s

    def get(self, uid: int, sid: int) -> dict:
        row = self.db.one("SELECT * FROM focus_sessions WHERE id=? AND user_id=?", (sid, uid))
        if not row:
            raise ValueError("no such session")
        return self._decorate(uid, row)

    def latest(self, uid: int) -> dict | None:
        row = self.db.one("SELECT * FROM focus_sessions WHERE user_id=? ORDER BY id DESC LIMIT 1", (uid,))
        return self._decorate(uid, row) if row else None

    def active(self, uid: int) -> dict | None:
        """The running session, or the one whose break is still going."""
        row = self.db.one("SELECT * FROM focus_sessions WHERE user_id=? ORDER BY id DESC LIMIT 1", (uid,))
        if not row:
            return None
        s = self._decorate(uid, row)
        if s["state"] == "overtime" and self.tasks.now() > datetime.fromisoformat(s["ends_at"]) + timedelta(minutes=STALE_MINUTES):
            self.end(uid, s["id"], "timeout", quiet=True)
            return None
        return s if s["state"] in ("running", "overtime", "break") else None

    def minutes_today(self, uid: int) -> int:
        today = self.tasks.today().isoformat()
        rows = self.db.query("SELECT started_at, ended_at, minutes FROM focus_sessions WHERE user_id=?"
                             " AND started_at>=?", (uid, today))
        total = 0
        now = self.tasks.now()
        for r in rows:
            start = datetime.fromisoformat(r["started_at"])
            end = datetime.fromisoformat(r["ended_at"]) if r["ended_at"] else now
            total += max(0, min((end - start).total_seconds() / 60, r["minutes"] + 30))
        return int(total)

    # ---------- changing ----------
    async def start(self, uid: int, minutes: int | None = None, task_id: int | None = None,
                    intention: str = "") -> dict:
        p = self.prefs.get(uid)["sessions"]
        minutes = max(5, min(180, int(minutes or p["minutes"])))
        task = self.tasks.get(uid, task_id) if task_id else None
        cur = self.active(uid)
        if cur and cur["state"] != "break":
            self.end(uid, cur["id"], "stopped", quiet=True)
        stamp = self.tasks._stamp()
        with self.db.tx() as c:
            sid = c.execute("INSERT INTO focus_sessions(user_id, task_id, intention, minutes, started_at)"
                            " VALUES(?,?,?,?,?)", (uid, task_id, (intention or "").strip()[:200],
                                                   minutes, stamp)).lastrowid
            c.execute("UPDATE focus_sessions SET break_until=NULL WHERE user_id=? AND id!=?", (uid, sid))
        if p["quiet"]:
            self.tasks.set_hyperfocus(uid, minutes)
        step = task["next_step"]["title"] if task and task["next_step"] else (intention or None)
        line = await self.coach.say_smart(uid, "session_start", task=task["title"] if task else intention or None,
                                          step=step, minutes=minutes)
        s = self.get(uid, sid)
        s["coach"] = line
        return s

    async def checkin(self, uid: int, sid: int, mood: str) -> dict:
        if mood not in MOODS:
            raise ValueError(f"mood must be one of {', '.join(MOODS)}")
        s = self.get(uid, sid)
        checkins = s["checkins"] + [{"at": self.tasks._stamp(), "mood": mood}]
        with self.db.tx() as c:
            c.execute("UPDATE focus_sessions SET checkins=? WHERE id=?", (json.dumps(checkins), sid))
        self.tasks._changed()
        task = s["task"]
        step = task["next_step"]["title"] if task and task["next_step"] else None
        ctx = {"task": task["title"] if task else s["intention"] or None, "step": step}
        event = {"good": "checkin_good", "stuck": "checkin_stuck", "distracted": "checkin_distracted"}[mood]
        line = await self.coach.say_smart(uid, event, **ctx) if mood == "stuck" else self.coach.say(uid, event, **ctx)
        out = self.get(uid, sid)
        out["coach"] = line
        return out

    def extend(self, uid: int, sid: int, minutes: int = 5) -> dict:
        s = self.get(uid, sid)
        if s["ended_at"]:
            raise ValueError("that session has ended")
        minutes = max(1, min(60, int(minutes)))
        with self.db.tx() as c:
            c.execute("UPDATE focus_sessions SET minutes=minutes+? WHERE id=?", (minutes, sid))
        out = self.get(uid, sid)
        if self.prefs.get(uid)["sessions"]["quiet"]:
            self.tasks.set_hyperfocus(uid, max(1, out["remaining_s"] // 60 + 1))
        self.tasks._changed()
        return out

    def end(self, uid: int, sid: int, outcome: str = "stopped", complete_task: bool = False,
            quiet: bool = False) -> dict:
        s = self.get(uid, sid)
        if s["ended_at"]:
            return s
        now = self.tasks.now()
        start = datetime.fromisoformat(s["started_at"])
        actual = int(min((now - start).total_seconds() / 60, s["minutes"] + 30))
        steps = self.db.one("SELECT COUNT(*) FROM xp_events WHERE user_id=? AND task_id IS ? AND reason LIKE 'step:%'"
                            " AND created_at>=?", (uid, s["task_id"], s["started_at"]))[0] if s["task_id"] else 0
        full = actual >= s["minutes"] * 0.9
        xp = actual + (10 if full else 0)
        stamp = now.isoformat(timespec="seconds")
        with self.db.tx() as c:
            c.execute("UPDATE focus_sessions SET ended_at=?, outcome=?, steps_done=?, xp=? WHERE id=?",
                      (stamp, outcome, steps, xp, sid))
            if xp > 0:
                c.execute("INSERT INTO xp_events(user_id, task_id, amount, reason, created_at) VALUES(?,?,?,?,?)",
                          (uid, s["task_id"], xp, f"session:{sid}", stamp))
        hf = self.tasks.hyperfocus_until(uid)
        if hf and hf <= (datetime.fromisoformat(s["ends_at"]) + timedelta(minutes=2)).isoformat(timespec="seconds"):
            self.tasks.set_hyperfocus(uid, None)  # the session's quiet time, not one set separately
        out = {"reward": None}
        if complete_task and s["task_id"]:
            t = self.tasks.complete(uid, s["task_id"])
            out["reward"] = t.get("reward")
        if not quiet:
            event = "session_end" if full or outcome == "done" else "session_short"
            out["coach"] = self.coach.say(uid, event, minutes=actual,
                                          task=s["task"]["title"] if s["task"] else s["intention"] or None)
        self.tasks._changed()
        result = self.get(uid, sid)
        result.update(out)
        result["xp"] = xp
        result["actual_minutes"] = actual
        return result

    def start_break(self, uid: int, sid: int, minutes: int | None = None) -> dict:
        s = self.get(uid, sid)
        if not s["ended_at"]:
            s = self.end(uid, sid, "done")
        minutes = int(minutes or self.prefs.get(uid)["sessions"]["break_minutes"])
        until = (self.tasks.now() + timedelta(minutes=minutes)).isoformat(timespec="seconds")
        with self.db.tx() as c:
            c.execute("UPDATE focus_sessions SET break_until=? WHERE id=?", (until, sid))
        out = self.get(uid, sid)
        out["coach"] = self.coach.say(uid, "break_start")
        return out

    def end_break(self, uid: int, sid: int) -> dict:
        with self.db.tx() as c:
            c.execute("UPDATE focus_sessions SET break_until=NULL WHERE id=? AND user_id=?", (sid, uid))
        self.tasks._changed()
        return self.get(uid, sid)
