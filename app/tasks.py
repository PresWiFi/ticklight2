"""Task operations. Everything that changes data goes through here, so the web UI,
the API and the Telegram bot behave the same way. Every call is scoped to a user."""

from __future__ import annotations

import json
import random
from datetime import date, datetime, timedelta
from typing import Callable
from zoneinfo import ZoneInfo

from . import parser, recurrence
from .db import DB


class NotFound(Exception):
    pass


AREAS = ("work", "home", "money", "health", "people", "fun")
FIELDS = ("title", "notes", "priority", "due_date", "due_time", "recurrence",
          "area", "energy", "minutes", "shared")
# A user sees their own tasks plus tasks their linked people shared
VISIBLE = ("(user_id=? OR (shared=1 AND user_id IN (SELECT other_id FROM links WHERE user_id=?)))")
ENERGY_NAMES = {1: "low", 2: "medium", 3: "high"}
MANUAL_ENERGY_HOURS = 4  # a manually set energy level applies for this long


def _d(s: str | None) -> date | None:
    return date.fromisoformat(s) if s else None


def _valid_time(t: str | None) -> str | None:
    if not t:
        return None
    hh, mm = t.split(":")[:2]
    h, m = int(hh), int(mm)
    if not (0 <= h <= 23 and 0 <= m <= 59):
        raise ValueError("bad time")
    return f"{h:02d}:{m:02d}"


def _energy(v) -> int | None:
    if v in (None, ""):
        return None
    if isinstance(v, str) and not v.isdigit():
        v = {"low": 1, "medium": 2, "med": 2, "high": 3}.get(v.lower())
        if v is None:
            raise ValueError("energy must be low, medium or high")
    return max(1, min(3, int(v)))


def _area(v) -> str | None:
    if not v:
        return None
    v = str(v).lower().strip()
    if v not in AREAS:
        raise ValueError(f"area must be one of {', '.join(AREAS)}")
    return v


def _minutes(v) -> int | None:
    return None if v in (None, "") else max(1, min(24 * 60, int(v)))


def level_for(xp: int) -> dict:
    """Level L needs 25*L*(L+1) XP to reach L+1: 50, 150, 300, 500…"""
    lvl = 1
    while xp >= 25 * lvl * (lvl + 1):
        lvl += 1
    floor, ceil = 25 * (lvl - 1) * lvl, 25 * lvl * (lvl + 1)
    return {"xp": xp, "level": lvl, "into": xp - floor, "needed": ceil - floor}


BONUS_LINES = [
    "Critical hit! ×{m} XP",
    "Lucky drop! ×{m} XP",
    "Bonus round! ×{m} XP",
    "The dice like you today. ×{m} XP",
]


class Tasks:
    def __init__(self, db: DB, tz: ZoneInfo, clock: Callable[[], datetime] | None = None,
                 rng: random.Random | None = None, energy_schedule: str = "00:2",
                 stale_overdue_days: int = 7, stale_undated_days: int = 21):
        self.db = db
        self.tz = tz
        self._clock = clock
        self.rng = rng or random.Random()
        self.energy_schedule = sorted(
            (p.split(":")[0].zfill(2), int(p.split(":")[1]))
            for p in energy_schedule.split(",") if ":" in p)
        self.stale_overdue_days = stale_overdue_days
        self.stale_undated_days = stale_undated_days
        self.version = 0
        self.listeners: list[Callable[[], None]] = []

    # ---------- helpers ----------
    def now(self) -> datetime:
        return self._clock() if self._clock else datetime.now(self.tz)

    def today(self) -> date:
        return self.now().date()

    def _stamp(self) -> str:
        return self.now().isoformat(timespec="seconds")

    def _changed(self) -> None:
        self.version += 1
        for fn in self.listeners:
            fn()

    def _row(self, uid: int, task_id: int) -> dict:
        row = self.db.one(f"SELECT * FROM tasks WHERE id=? AND {VISIBLE}", (task_id, uid, uid))
        if not row:
            raise NotFound(f"task {task_id}")
        return self._decorate(dict(row))

    def get(self, uid: int, task_id: int) -> dict:
        return self._row(uid, task_id)

    def _grace_end(self, t: dict) -> date | None:
        """The day a recurring task's streak breaks. One missed occurrence is forgiven,
        so this is the occurrence *after* the next one."""
        rule, due = t["recurrence"], _d(t["due_date"])
        if not rule or not due:
            return None
        if rule["type"] == "after":
            return due + timedelta(days=2 * rule["days"])
        anchor = _d(t["anchor"]) or due
        return recurrence.next_after(rule, anchor, recurrence.next_after(rule, anchor, due))

    def _decorate(self, t: dict) -> dict:
        if isinstance(t.get("recurrence"), str):
            t["recurrence"] = json.loads(t["recurrence"])
        t["recurrence_text"] = recurrence.describe(t["recurrence"])
        grace = self._grace_end(t)
        t["current_streak"] = 0 if grace and self.today() >= grace else t["streak"]
        t["subtasks"] = self.subtasks(t["id"])
        t["subtasks_done"] = sum(1 for st in t["subtasks"] if st["done"])
        t["next_step"] = next((st for st in t["subtasks"] if not st["done"]), None)
        due = _d(t["due_date"])
        t["carried_over"] = bool(due and due < self.today() and t["status"] == "open")
        t["pinned"] = t.get("pinned_on") == self.today().isoformat()
        t["shared"] = bool(t.get("shared"))
        return t

    def _first_due(self, rule: dict | None, due: date | None,
                   due_time: str | None) -> tuple[date | None, date | None]:
        """Return (due_date, anchor) for a new or re-scheduled task."""
        now = self.now()
        today = now.date()
        time_passed = bool(due_time) and due_time <= now.strftime("%H:%M")
        if not rule:
            if due is None and due_time:
                due = today + timedelta(days=1) if time_passed else today
            return due, None
        anchor = due or today
        if rule["type"] == "after":
            return anchor, anchor
        first = recurrence.first_on_or_after(rule, anchor, anchor)
        if due is None and first == today and time_passed:
            first = recurrence.next_after(rule, anchor, today)
        return first, anchor

    # ---------- energy ----------
    def scheduled_energy(self) -> int:
        hm = self.now().strftime("%H:%M")
        level = 2
        for start, lvl in self.energy_schedule:
            if start <= hm[:2]:
                level = lvl
        return max(1, min(3, level))

    def energy(self, uid: int) -> dict:
        u = self.db.one("SELECT energy, energy_set_at FROM users WHERE id=?", (uid,))
        if u and u["energy"] and u["energy_set_at"]:
            set_at = datetime.fromisoformat(u["energy_set_at"])
            if self.now() - set_at < timedelta(hours=MANUAL_ENERGY_HOURS):
                return {"level": u["energy"], "name": ENERGY_NAMES[u["energy"]], "manual": True}
        lvl = self.scheduled_energy()
        return {"level": lvl, "name": ENERGY_NAMES[lvl], "manual": False}

    def set_energy(self, uid: int, level) -> dict:
        lvl = _energy(level)
        with self.db.tx() as c:
            c.execute("UPDATE users SET energy=?, energy_set_at=? WHERE id=?",
                      (lvl, self._stamp() if lvl else None, uid))
        self._changed()
        return self.energy(uid)

    # ---------- create / edit ----------
    def create(self, uid: int, title: str, notes: str = "", priority: int = 1,
               due_date: str | date | None = None, due_time: str | None = None,
               recurrence_rule: dict | None = None, area: str | None = None,
               energy=None, minutes=None, steps: list[str] | None = None,
               capture_id: int | None = None, status: str = "open", shared: bool = False,
               from_user: int | None = None) -> dict:
        title = (title or "").strip()
        if not title:
            raise ValueError("title is required")
        rule = recurrence.normalize(recurrence_rule)
        due = _d(due_date) if isinstance(due_date, str) else due_date
        due_time = _valid_time(due_time)
        due, anchor = self._first_due(rule, due, due_time)
        stamp = self._stamp()
        with self.db.tx() as c:
            cur = c.execute(
                "INSERT INTO tasks(user_id, title, notes, priority, area, energy, minutes,"
                " due_date, due_time, recurrence, anchor, status, capture_id, shared, from_user,"
                " created_at, updated_at, touched_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (uid, title, notes or "", max(0, min(2, int(priority))), _area(area),
                 _energy(energy), _minutes(minutes),
                 due.isoformat() if due else None, due_time,
                 json.dumps(rule) if rule else None,
                 anchor.isoformat() if anchor else None,
                 "someday" if status == "someday" else "open", capture_id, int(bool(shared)), from_user,
                 stamp, stamp, stamp))
            tid = cur.lastrowid
            for i, s in enumerate(x.strip() for x in steps or [] if x and x.strip()):
                c.execute("INSERT INTO subtasks(task_id, title, position, created_at)"
                          " VALUES(?,?,?,?)", (tid, s, i + 1, stamp))
        self._changed()
        return self.get(uid, tid)

    def quick_add(self, uid: int, text: str, **extra) -> dict:
        p = parser.parse(text, self.today())
        task = self.create(uid, p.title, priority=p.priority, due_date=p.due_date,
                           due_time=p.due_time, recurrence_rule=p.recurrence, **extra)
        task["parsed_summary"] = p.summary()
        return task

    def bulk_add(self, uid: int, text: str, capture_id: int | None = None, **extra) -> list[dict]:
        """One task per line; indented or "+" lines become steps of the task above."""
        items = parser.split_bulk(text)
        if len(items) > parser.MAX_BULK:
            raise ValueError(f"too many tasks at once (max {parser.MAX_BULK})")
        out = []
        for line, subs in items:
            p = parser.parse(line, self.today())
            t = self.create(uid, p.title, priority=p.priority, due_date=p.due_date,
                            due_time=p.due_time, recurrence_rule=p.recurrence,
                            steps=subs, capture_id=capture_id, **extra)
            t["parsed_summary"] = p.summary()
            out.append(t)
        return out

    def update(self, uid: int, task_id: int, changes: dict) -> dict:
        t = self._row(uid, task_id)
        new = {k: changes[k] for k in FIELDS if k in changes}
        if "title" in new:
            new["title"] = (new["title"] or "").strip()
            if not new["title"]:
                raise ValueError("title is required")
        if "priority" in new:
            new["priority"] = max(0, min(2, int(new["priority"])))
        if "area" in new:
            new["area"] = _area(new["area"])
        if "energy" in new:
            new["energy"] = _energy(new["energy"])
        if "minutes" in new:
            new["minutes"] = _minutes(new["minutes"])
        if "shared" in new:
            new["shared"] = int(bool(new["shared"]))
        if "due_time" in new:
            new["due_time"] = _valid_time(new["due_time"])
        if "due_date" in new:
            dd = new["due_date"]
            new["due_date"] = (dd if isinstance(dd, date) else _d(dd)).isoformat() if dd else None
        schedule_changed = any(k in new for k in ("due_date", "recurrence", "due_time"))
        if "recurrence" in new or "due_date" in new:
            rule = recurrence.normalize(new.get("recurrence", t["recurrence"]))
            due_in = new.get("due_date", t["due_date"])
            due, anchor = self._first_due(rule, _d(due_in), new.get("due_time", t["due_time"]))
            new["recurrence"] = json.dumps(rule) if rule else None
            new["due_date"] = due.isoformat() if due else None
            new["anchor"] = anchor.isoformat() if anchor else None
        if schedule_changed:
            new["reminded_for"] = None
        if not new:
            return t
        new["updated_at"] = new["touched_at"] = self._stamp()
        cols = ", ".join(f"{k}=?" for k in new)
        with self.db.tx() as c:
            c.execute(f"UPDATE tasks SET {cols} WHERE id=?", (*new.values(), task_id))
        self._changed()
        return self.get(uid, task_id)

    def _set_status(self, uid: int, task_id: int, status: str, **cols) -> dict:
        self._row(uid, task_id)
        stamp = self._stamp()
        sets = {"status": status, "updated_at": stamp, "touched_at": stamp, **cols}
        with self.db.tx() as c:
            c.execute(f"UPDATE tasks SET {', '.join(f'{k}=?' for k in sets)} WHERE id=?",
                      (*sets.values(), task_id))
        self._changed()
        return self.get(uid, task_id)

    def to_someday(self, uid: int, task_id: int, reason: str = "moved by you") -> dict:
        return self._set_status(uid, task_id, "someday", someday_reason=reason)

    def bring_back(self, uid: int, task_id: int, when: str | None = None) -> dict:
        """Move a Someday task back to the active list, optionally re-dated."""
        t = self._set_status(uid, task_id, "open", someday_reason=None, not_now_until=None,
                             not_now_count=0)
        if t["due_date"] and not t["recurrence"] and _d(t["due_date"]) < self.today():
            t = self.update(uid, task_id, {"due_date": None})
        if when:
            p = parser.parse("x " + when, self.today())
            t = self.update(uid, task_id, {"due_date": p.due_date, "due_time": p.due_time}
                            if p.due_date or p.due_time else {})
        return t

    def not_now(self, uid: int, task_id: int, hours: float = 3) -> dict:
        """Hide a task from Focus for a while, without judgement."""
        t = self._row(uid, task_id)
        until = (self.now() + timedelta(hours=hours)).isoformat(timespec="seconds")
        with self.db.tx() as c:
            c.execute("UPDATE tasks SET not_now_until=?, not_now_count=not_now_count+1,"
                      " touched_at=? WHERE id=?", (until, self._stamp(), task_id))
        self._changed()
        t = self.get(uid, task_id)
        t["suggest_breakdown"] = t["not_now_count"] >= 3 and not t["next_step"]
        return t

    # ---------- complete / skip / undo ----------
    def complete(self, uid: int, task_id: int, skipped: bool = False) -> dict:
        t = self._row(uid, task_id)
        if t["status"] not in ("open", "someday"):
            return t
        today, stamp = self.today(), self._stamp()
        rule, due = t["recurrence"], _d(t["due_date"])
        snapshot = json.dumps({st["id"]: st["done"] for st in t["subtasks"]})
        reward = None
        with self.db.tx() as c:
            cur = c.execute(
                "INSERT INTO completions(task_id, done_at, for_date, skipped, prev_due,"
                " prev_streak, prev_best, prev_status, prev_subtasks, by_user) VALUES(?,?,?,?,?,?,?,?,?,?)",
                (task_id, stamp, t["due_date"], int(skipped), t["due_date"],
                 t["streak"], t["best_streak"], t["status"], snapshot, uid))
            completion_id = cur.lastrowid
            if not rule:
                if skipped:
                    raise ValueError("only recurring tasks can be skipped")
                c.execute("UPDATE tasks SET status='done', completed_at=?, updated_at=?,"
                          " touched_at=? WHERE id=?", (stamp, stamp, stamp, task_id))
                streak = 0
            else:
                if skipped:
                    streak = 0
                else:
                    grace = self._grace_end(t)
                    streak = t["streak"] + 1 if grace is None or today < grace else 1
                best = max(t["best_streak"], streak)
                if rule["type"] == "after":
                    nxt = today + timedelta(days=rule["days"])
                else:
                    anchor = _d(t["anchor"]) or due or today
                    nxt = recurrence.next_after(rule, anchor, max(due or today, today))
                c.execute(
                    "UPDATE tasks SET due_date=?, streak=?, best_streak=?, reminded_for=NULL,"
                    " not_now_until=NULL, not_now_count=0, status='open', completed_at=?,"
                    " updated_at=?, touched_at=? WHERE id=?",
                    (nxt.isoformat(), streak, best, stamp, stamp, stamp, task_id))
                c.execute("UPDATE subtasks SET done=0 WHERE task_id=?", (task_id,))
            if not skipped:
                reward = self._award(c, uid, t, completion_id, streak, stamp)
        self._changed()
        out = self.get(uid, task_id)
        out["reward"] = reward
        return out

    def _award(self, c, uid: int, t: dict, completion_id: int, streak: int, stamp: str) -> dict:
        """XP for a completion. Bigger/harder tasks give more, with random surprise bonuses."""
        before = self._xp(uid)
        energy, minutes = t["energy"] or 2, t["minutes"] or 15
        base = 10 + 5 * energy + min(minutes, 120) // 10 * 2 + (5 if t["priority"] == 2 else 0)
        events = [(base, "done")]
        lines = []
        first = c.execute(
            "SELECT 1 FROM xp_events WHERE user_id=? AND reason='done' AND created_at>=?",
            (uid, self.today().isoformat())).fetchone() is None
        if first:
            events.append((10, "first of the day"))
            lines.append("First one today +10")
        if t["carried_over"]:
            events.append((5, "carried over"))
            lines.append("Cleared an old one +5")
        if streak >= 3 and streak % 5 == 0:
            events.append((streak * 2, "streak"))
            lines.append(f"🔥 {streak} streak +{streak * 2}")
        if self.rng.random() < 0.15:
            mult = self.rng.choice([2, 2, 2, 3])
            events.append((base * (mult - 1), "bonus"))
            lines.append(self.rng.choice(BONUS_LINES).format(m=mult))
        for amount, reason in events:
            c.execute("INSERT INTO xp_events(user_id, task_id, completion_id, amount, reason,"
                      " created_at) VALUES(?,?,?,?,?,?)",
                      (uid, t["id"], completion_id, amount, reason, stamp))
        total = sum(a for a, _ in events)
        coins = max(1, total // 8)
        if self.rng.random() < 0.08:
            drop = self.rng.randint(10, 30)
            coins += drop
            lines.append(f"🪙 Lucky drop! +{drop} coins")
        c.execute("INSERT INTO coin_events(user_id, amount, reason, completion_id, created_at)"
                  " VALUES(?,?,?,?,?)", (uid, coins, "done", completion_id, stamp))
        after = level_for(before + total)
        return {"xp": total, "coins": coins, "lines": lines, "bonus": any(r == "bonus" for _, r in events),
                "level": after["level"], "level_up": after["level"] > level_for(before)["level"]}

    def skip(self, uid: int, task_id: int) -> dict:
        return self.complete(uid, task_id, skipped=True)

    def undo_complete(self, uid: int, task_id: int) -> dict:
        self._row(uid, task_id)
        last = self.db.one(
            "SELECT * FROM completions WHERE task_id=? ORDER BY id DESC LIMIT 1", (task_id,))
        if not last:
            raise ValueError("nothing to undo")
        with self.db.tx() as c:
            c.execute(
                "UPDATE tasks SET status=?, due_date=?, streak=?, best_streak=?,"
                " completed_at=NULL, updated_at=? WHERE id=?",
                (last["prev_status"], last["prev_due"], last["prev_streak"],
                 last["prev_best"], self._stamp(), task_id))
            for sid, done in json.loads(last["prev_subtasks"] or "{}").items():
                c.execute("UPDATE subtasks SET done=? WHERE id=? AND task_id=?",
                          (int(done), int(sid), task_id))
            c.execute("DELETE FROM completions WHERE id=?", (last["id"],))  # cascades XP
        self._changed()
        return self.get(uid, task_id)

    def delete(self, uid: int, task_id: int) -> dict:
        return self._set_status(uid, task_id, "deleted")

    def restore(self, uid: int, task_id: int) -> dict:
        return self._set_status(uid, task_id, "open")

    # ---------- steps (subtasks) ----------
    def subtasks(self, task_id: int) -> list[dict]:
        rows = self.db.query(
            "SELECT id, title, done FROM subtasks WHERE task_id=? ORDER BY position, id",
            (task_id,))
        return [{"id": r["id"], "title": r["title"], "done": bool(r["done"])} for r in rows]

    def _sub_row(self, uid: int, sub_id: int):
        row = self.db.one("SELECT s.* FROM subtasks s JOIN tasks t ON t.id=s.task_id WHERE s.id=? AND"
                          " (t.user_id=? OR (t.shared=1 AND t.user_id IN (SELECT other_id FROM links WHERE user_id=?)))",
                          (sub_id, uid, uid))
        if not row:
            raise NotFound(f"step {sub_id}")
        return row

    def add_subtasks(self, uid: int, task_id: int, titles: list[str] | str) -> dict:
        self._row(uid, task_id)
        if isinstance(titles, str):
            titles = [titles]
        titles = [t.strip() for t in titles if t and t.strip()]
        if not titles:
            raise ValueError("step title is required")
        stamp = self._stamp()
        with self.db.tx() as c:
            pos = c.execute("SELECT COALESCE(MAX(position), 0) FROM subtasks WHERE task_id=?",
                            (task_id,)).fetchone()[0]
            for i, title in enumerate(titles, 1):
                c.execute("INSERT INTO subtasks(task_id, title, position, created_at)"
                          " VALUES(?,?,?,?)", (task_id, title, pos + i, stamp))
            c.execute("UPDATE tasks SET touched_at=? WHERE id=?", (stamp, task_id))
        self._changed()
        return self.get(uid, task_id)

    def replace_steps(self, uid: int, task_id: int, titles: list[str]) -> dict:
        """Swap the unfinished steps for a new breakdown, keeping finished ones."""
        self._row(uid, task_id)
        with self.db.tx() as c:
            c.execute("DELETE FROM subtasks WHERE task_id=? AND done=0", (task_id,))
        return self.add_subtasks(uid, task_id, titles)

    def update_subtask(self, uid: int, sub_id: int, title: str | None = None,
                       done: bool | None = None) -> dict:
        """Rename and/or tick a step. done=None with no title toggles it.
        Ticking a step gives a little XP; unticking takes it back."""
        row = self._sub_row(uid, sub_id)
        if title is not None and not title.strip():
            raise ValueError("step title is required")
        if done is None and title is None:
            done = not row["done"]
        stamp = self._stamp()
        with self.db.tx() as c:
            if title is not None:
                c.execute("UPDATE subtasks SET title=? WHERE id=?", (title.strip(), sub_id))
            if done is not None and bool(done) != bool(row["done"]):
                c.execute("UPDATE subtasks SET done=? WHERE id=?", (int(done), sub_id))
                c.execute("UPDATE tasks SET touched_at=?, not_now_until=NULL WHERE id=?",
                          (stamp, row["task_id"]))
                if done:
                    c.execute("INSERT INTO xp_events(user_id, task_id, amount, reason, created_at)"
                              " VALUES(?,?,?,?,?)", (uid, row["task_id"], 3, f"step:{sub_id}", stamp))
                else:
                    c.execute("DELETE FROM xp_events WHERE user_id=? AND reason=?",
                              (uid, f"step:{sub_id}"))
        self._changed()
        return self.get(uid, row["task_id"])

    def delete_subtask(self, uid: int, sub_id: int) -> dict:
        row = self._sub_row(uid, sub_id)
        with self.db.tx() as c:
            c.execute("DELETE FROM subtasks WHERE id=?", (sub_id,))
        self._changed()
        return self.get(uid, row["task_id"])

    # ---------- queries ----------
    def tasks_with_status(self, uid: int, status: str = "open") -> list[dict]:
        rows = self.db.query(
            f"SELECT * FROM tasks WHERE status=? AND {VISIBLE} ORDER BY"
            " due_date IS NULL, due_date, due_time IS NULL, due_time, priority DESC, id",
            (status, uid, uid))
        return [self._decorate(dict(r)) for r in rows]

    def open_tasks(self, uid: int) -> list[dict]:
        return self.tasks_with_status(uid, "open")

    def tidy(self, uid: int, force: bool = False) -> list[dict]:
        """Quietly move stale tasks to Someday. Runs at most once a day per user."""
        key, today = f"tidy:{uid}", self.today().isoformat()
        if not force and self.db.get_meta(key) == today:
            return []
        self.db.set_meta(key, today)
        overdue_cut = (self.today() - timedelta(days=self.stale_overdue_days)).isoformat()
        untouched_cut = (self.now() - timedelta(days=self.stale_undated_days)).isoformat()
        moved = []
        for t in self.open_tasks(uid):
            if t["recurrence"] or t["user_id"] != uid:
                continue
            if t["due_date"] and t["due_date"] < overdue_cut:
                reason = f"carried over {self.stale_overdue_days}+ days"
            elif not t["due_date"] and t["touched_at"] < untouched_cut:
                reason = f"untouched {self.stale_undated_days}+ days"
            else:
                continue
            with self.db.tx() as c:
                c.execute("UPDATE tasks SET status='someday', someday_reason=? WHERE id=?",
                          (reason, t["id"]))
            moved.append(t)
        if moved:
            self._changed()
        return moved

    def focus(self, uid: int, n: int = 3, energy: int | None = None) -> list[dict]:
        """The next few things worth doing, with a short reason for each."""
        now = self.now()
        today = now.date()
        soon = (now + timedelta(hours=2)).strftime("%H:%M")
        energy = energy or self.energy(uid)["level"]
        scored = []
        for t in self.open_tasks(uid):
            if t["not_now_until"] and t["not_now_until"] > now.isoformat(timespec="seconds"):
                continue
            due = _d(t["due_date"])
            score, why = 0.0, []
            if due and due < today:
                score += 40
                why.append("carried over")
            elif due == today:
                if t["due_time"] and t["due_time"] > soon:
                    score += 5  # later today, not yet
                else:
                    score += 45 if t["due_time"] and t["due_time"] <= soon else 35
                    why.append(f"due {t['due_time']}" if t["due_time"] else "due today")
            elif due and t["due_time"] and not t["pinned"]:
                continue  # a timed thing on a later day is an appointment, not a to-do yet
            elif due and (due - today).days == 1:
                score += 15
                why.append("due tomorrow")
            elif due and (due - today).days <= 3:
                score += 3
            elif due and not t["pinned"]:
                continue  # comfortably in the future; keep Focus short
            if t["pinned"]:
                score += 60
                why.insert(0, "today's pick")
            score += (t["priority"] - 1) * 20
            if t["priority"] == 2:
                why.append("important")
            need = t["energy"] or 2
            if need > energy:
                score -= 15 * (need - energy)
            elif need == energy and t["energy"]:
                score += 5
                why.append("fits your energy")
            if energy == 1 and (t["minutes"] or 99) <= 15:
                score += 12
                why.append("quick win")
            if t["subtasks_done"]:
                score += 8
                why.append("already started")
            if t["current_streak"] >= 2:
                score += 5
                why.append(f"🔥 {t['current_streak']}")
            score -= 3 * t["not_now_count"]
            t["focus_score"], t["focus_why"] = score, why[:2]
            scored.append(t)
        scored.sort(key=lambda t: (-t["focus_score"], t["created_at"]))
        return scored[:n]

    def anytime(self, uid: int) -> list[dict]:
        """Undated open tasks that aren't snoozed, most important first."""
        now = self.now().isoformat(timespec="seconds")
        out = [t for t in self.open_tasks(uid)
               if not t["due_date"] and not (t["not_now_until"] and t["not_now_until"] > now)]
        return sorted(out, key=lambda t: (-t["priority"], t["id"]))

    def snoozed(self, uid: int) -> list[dict]:
        """Open tasks hidden with "Not now" that haven't come back yet."""
        now = self.now().isoformat(timespec="seconds")
        return sorted((t for t in self.open_tasks(uid) if t["not_now_until"] and t["not_now_until"] > now),
                      key=lambda t: (-t["priority"], t["id"]))

    def board(self, uid: int, upcoming_days: int = 7) -> dict:
        self.tidy(uid)
        today = self.today()
        horizon = today + timedelta(days=upcoming_days)
        out = {"today": [], "upcoming": [], "later": [], "anytime": []}
        for t in self.open_tasks(uid):
            d = _d(t["due_date"])
            if d is None:
                out["anytime"].append(t)
            elif d <= today:
                out["today"].append(t)
            elif d <= horizon:
                out["upcoming"].append(t)
            else:
                out["later"].append(t)
        out["today"].sort(key=lambda t: (t["carried_over"], -t["priority"],
                                         t["due_time"] or "99", t["id"]))
        out["anytime"].sort(key=lambda t: (-t["priority"], t["id"]))
        out["focus"] = self.focus(uid)
        out["pinned"] = [t for t in self.open_tasks(uid) if t["pinned"]]
        out["hyperfocus_until"] = self.hyperfocus_until(uid)
        out["done_today"] = self.done_on(uid, today)
        out["someday_count"] = self.db.one(
            f"SELECT COUNT(*) FROM tasks WHERE status='someday' AND {VISIBLE}", (uid, uid))[0]
        out["energy"] = self.energy(uid)
        out["stats"] = self.stats(uid)
        out["now"] = self.now().isoformat(timespec="seconds")
        out["today_date"] = today.isoformat()
        out["version"] = self.version
        return out

    def done_on(self, uid: int, day: date) -> list[dict]:
        rows = self.db.query(
            "SELECT c.id AS completion_id, c.skipped, c.done_at, t.* FROM completions c"
            " JOIN tasks t ON t.id=c.task_id WHERE COALESCE(c.by_user, t.user_id)=? AND c.done_at >= ?"
            " AND c.done_at < ? AND t.status != 'deleted' ORDER BY c.done_at DESC",
            (uid, day.isoformat(), (day + timedelta(days=1)).isoformat()))
        return [self._decorate(dict(r)) for r in rows]

    def history(self, uid: int, task_id: int, limit: int = 60) -> list[dict]:
        self._row(uid, task_id)
        rows = self.db.query(
            "SELECT done_at, for_date, skipped FROM completions WHERE task_id=?"
            " ORDER BY id DESC LIMIT ?", (task_id, limit))
        return [dict(r) for r in rows]

    def _xp(self, uid: int) -> int:
        return self.db.one("SELECT COALESCE(SUM(amount), 0) FROM xp_events WHERE user_id=?",
                           (uid,))[0]

    def stats(self, uid: int) -> dict:
        out = level_for(self._xp(uid))
        out["xp_today"] = self.db.one(
            "SELECT COALESCE(SUM(amount), 0) FROM xp_events WHERE user_id=? AND created_at>=?",
            (uid, self.today().isoformat()))[0]
        return out

    # ---------- reminders ----------
    def due_reminders(self, uid: int, lead_minutes: int = 0,
                      max_late_hours: int = 6) -> list[dict]:
        now = self.now().replace(tzinfo=None)
        out = []
        for t in self.open_tasks(uid):
            if not (t["due_date"] and t["due_time"]):
                continue
            key = f"{t['due_date']}T{t['due_time']}"
            if t["reminded_for"] == key:
                continue
            due_dt = datetime.fromisoformat(key)
            if due_dt - timedelta(minutes=lead_minutes) <= now <= due_dt + timedelta(hours=max_late_hours):
                out.append(t)
        return out

    def mark_reminded(self, task_id: int, key: str | None) -> None:
        with self.db.tx() as c:
            c.execute("UPDATE tasks SET reminded_for=? WHERE id=?", (key, task_id))

    # ---------- today's three ----------
    def pin(self, uid: int, task_id: int, day: date | None = None) -> dict:
        """Mark a task as one of the day's picks (max 3 per day; oldest pick drops off)."""
        day = (day or self.today()).isoformat()
        self._row(uid, task_id)
        with self.db.tx() as c:
            picks = [r["id"] for r in c.execute(
                "SELECT id FROM tasks WHERE user_id=? AND pinned_on=? AND id!=? AND status='open'"
                " ORDER BY updated_at", (uid, day, task_id))]
            for old in picks[:max(0, len(picks) - 2)]:
                c.execute("UPDATE tasks SET pinned_on=NULL WHERE id=?", (old,))
            # Picking a task overrides an earlier "Not now", so it shows up in Focus.
            c.execute("UPDATE tasks SET pinned_on=?, not_now_until=NULL, updated_at=?, touched_at=? WHERE id=?",
                      (day, self._stamp(), self._stamp(), task_id))
        self._changed()
        return self.get(uid, task_id)

    def unpin(self, uid: int, task_id: int) -> dict:
        self._row(uid, task_id)
        with self.db.tx() as c:
            c.execute("UPDATE tasks SET pinned_on=NULL WHERE id=?", (task_id,))
        self._changed()
        return self.get(uid, task_id)

    def move_to(self, uid: int, task_id: int, when: str) -> dict:
        """Re-date a task: 'today', 'tomorrow', or anything the parser understands."""
        t = self._row(uid, task_id)
        p = parser.parse("x " + when, self.today())
        if not p.due_date:
            raise ValueError(f"couldn't understand {when!r}")
        changes = {"due_date": p.due_date}
        if p.due_time:
            changes["due_time"] = p.due_time
        if t["status"] == "someday":
            self.bring_back(uid, task_id)
        return self.update(uid, task_id, changes)

    # ---------- check-ins (meds taken, water, routines) ----------
    CHECKIN_XP = {"meds": 5, "meal": 3, "water": 2, "move": 3, "custom": 2,
                  "routine:morning": 15, "routine:evening": 15, "routine:weekly": 30}

    def checkin(self, uid: int, kind: str, xp_kind: str | None = None) -> dict:
        stamp = self._stamp()
        xp = self.CHECKIN_XP.get(xp_kind or kind, 2)
        with self.db.tx() as c:
            c.execute("INSERT INTO checkins(user_id, kind, at) VALUES(?,?,?)", (uid, kind, stamp))
            c.execute("INSERT INTO xp_events(user_id, amount, reason, created_at) VALUES(?,?,?,?)",
                      (uid, xp, f"checkin:{kind}", stamp))
        self._changed()
        return {"kind": kind, "at": stamp, "xp": xp}

    def last_checkin(self, uid: int, kind: str) -> str | None:
        row = self.db.one("SELECT MAX(at) FROM checkins WHERE user_id=? AND kind=?", (uid, kind))
        return row[0] if row else None

    def checkins_on(self, uid: int, day: date) -> list[dict]:
        rows = self.db.query("SELECT kind, at FROM checkins WHERE user_id=? AND at>=? AND at<?"
                             " ORDER BY at", (uid, day.isoformat(),
                                              (day + timedelta(days=1)).isoformat()))
        return [dict(r) for r in rows]

    # ---------- hyperfocus ----------
    def hyperfocus_until(self, uid: int) -> str | None:
        row = self.db.one("SELECT dnd_until FROM users WHERE id=?", (uid,))
        until = row["dnd_until"] if row else None
        return until if until and until > self.now().isoformat(timespec="seconds") else None

    def set_hyperfocus(self, uid: int, minutes: int | None) -> str | None:
        until = ((self.now() + timedelta(minutes=minutes)).isoformat(timespec="seconds")
                 if minutes else None)
        with self.db.tx() as c:
            c.execute("UPDATE users SET dnd_until=? WHERE id=?", (until, uid))
        self._changed()
        return until

    # ---------- reviews ----------
    def evening(self, uid: int) -> dict:
        today = self.today()
        leftovers = [t for t in self.open_tasks(uid)
                     if t["due_date"] and _d(t["due_date"]) <= today or t["pinned"]]
        return {"done": [t for t in self.done_on(uid, today) if not t["skipped"]],
                "leftovers": leftovers, "stats": self.stats(uid),
                "candidates": self.focus(uid, 8), "anytime": self.anytime(uid),
                "snoozed": self.snoozed(uid)}

    def weekly(self, uid: int) -> dict:
        """Things that need a decision, oldest first, plus how the week went."""
        week_ago = (self.now() - timedelta(days=7)).isoformat(timespec="seconds")
        items, seen = [], set()

        def add(ts, why):
            for t in ts:
                if t["id"] not in seen and len(items) < 25:
                    seen.add(t["id"])
                    t["review_why"] = why
                    items.append(t)
        open_ = self.open_tasks(uid)
        add([t for t in open_ if t["carried_over"] and not t["recurrence"]], "carried over")
        add([t for t in open_ if t["not_now_count"] >= 3], "keeps getting pushed")
        add([t for t in open_ if not t["due_date"] and t["touched_at"] < week_ago], "untouched for a week")
        add(sorted(self.tasks_with_status(uid, "someday"), key=lambda t: t["touched_at"]), "someday")
        done = self.db.one(
            "SELECT COUNT(*) FROM completions c JOIN tasks t ON t.id=c.task_id"
            " WHERE COALESCE(c.by_user, t.user_id)=? AND c.skipped=0 AND c.done_at>=?", (uid, week_ago))[0]
        xp = self.db.one("SELECT COALESCE(SUM(amount),0) FROM xp_events WHERE user_id=?"
                         " AND created_at>=?", (uid, week_ago))[0]
        return {"items": items, "done_this_week": done, "xp_this_week": xp,
                "upcoming": [t for t in open_ if t["due_date"] and 0 < (_d(t["due_date"]) - self.today()).days <= 7]}
