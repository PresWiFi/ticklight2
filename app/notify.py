"""Scheduled notifications: task reminders, body nudges, routines, hyperfocus.

`plan()` works out what should be sent right now (pure, easy to test). The Scheduler
runs it every 30 s and hands each new note to every channel (Telegram, phone push).
Each note has a key and is sent at most once.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta

from .prefs import CRITICAL_KINDS, Prefs
from .tasks import Tasks

log = logging.getLogger("ticklight.notify")


@dataclass
class Note:
    key: str
    at: datetime
    title: str
    body: str = ""
    actions: list[tuple[str, str]] = field(default_factory=list)  # (label, action)
    critical: bool = False          # gets through hyperfocus mode
    window: timedelta = timedelta(minutes=30)  # still worth sending this long after `at`
    tag: str = ""                   # push: a newer note with the same tag replaces the old one


# Several wordings per situation, so reminders don't turn into wallpaper.
T = {
    "lead": ["⏳ {t} in {m} min", "⏳ Heads up: {t} in {m} min", "⏳ {m} min until {t}",
             "⏳ Coming up in {m} min: {t}"],
    "lead_hf": ["🧭 Time to surface: {t} in {m} min", "🧭 Wrap up, {t} in {m} min",
                "🧭 Save your work: {t} in {m} min"],
    "at": ["⏰ {t}", "⏰ Now: {t}", "⏰ It's time: {t}", "⏰ {t}, right now"],
    "follow": ["👀 Still on? {t}", "👀 {t}: done, or not now?", "👀 Checking in: {t}",
               "👀 Did {t} happen?"],
    "meds": ["💊 {l} time", "💊 Time for your {l}", "💊 {l}: take them now?"],
    "meds_follow": ["💊 {l}? Tap when taken", "💊 Still need your {l}", "💊 Quick: {l} before you forget",
                    "💊 {l} — just checking"],
    "meal": ["🍽 {l} time", "🍽 Time to eat: {l}", "🍽 Food break: {l}"],
    "meal_follow": ["🍽 Eaten yet?", "🍽 {l}: did you eat?"],
    "water": ["💧 Water", "💧 Sip of water?", "💧 Hydration check", "💧 Drink something"],
    "move": ["🚶 Stand up and stretch", "🚶 Move for a minute", "🚶 Quick walk?"],
    "custom": ["🔔 {l}"],
    "custom_follow": ["🔔 {l}?"],
    "morning": ["☀️ Morning. Let's pick today's three.", "☀️ Good morning. 2 minutes to plan?",
                "☀️ New day. What matters today?"],
    "evening": ["🌙 Wind down: 2 minutes to close the day", "🌙 Evening shutdown time",
                "🌙 Let's park today and set up tomorrow"],
    "weekly": ["🧹 Weekly reset: 10 minutes", "🧹 Time for the weekly reset",
               "🧹 Clear the decks for next week"],
    "session_end": ["⏱ Time! {m} minutes done", "⏱ Session over: {m} minutes", "⏱ That's {m} minutes. Nice."],
    "break_end": ["☕ Break's over", "☕ Break done. Another round?", "☕ Back when you're ready"],
    "hf_end": ["🎧 Hyperfocus time's up. Stand, stretch, drink some water.",
               "🎧 Surface check: hyperfocus window ended", "🎧 Time check: your focus block is over"],
}


def pick(kind: str, key: str, **kw) -> str:
    options = T[kind]
    i = int(hashlib.sha1(key.encode()).hexdigest(), 16) % len(options)
    return options[i].format(**kw)


def _at(day: date, hm: str, tz) -> datetime:
    h, m = map(int, hm.split(":"))
    return datetime(day.year, day.month, day.day, h, m, tzinfo=tz)


def plan(tasks: Tasks, prefs: Prefs, uid: int, sessions=None, people=None) -> list[Note]:
    """Every note that is due now (sent or not; the scheduler de-duplicates)."""
    now = tasks.now()
    tz = now.tzinfo
    today = now.date()
    p = prefs.get(uid)
    hf = tasks.hyperfocus_until(uid)
    notes: list[Note] = []

    # ---- task reminders ----
    r = p["reminders"]
    for t in tasks.open_tasks(uid):
        if not (t["due_date"] and t["due_time"]):
            continue
        due = datetime.fromisoformat(f"{t['due_date']}T{t['due_time']}").replace(tzinfo=tz)
        if not (due - timedelta(hours=4) <= now <= due + timedelta(hours=6)):
            continue
        base = f"task:{t['id']}:{t['due_date']}T{t['due_time']}"
        title = t["title"]
        step = f"Start with: {t['next_step']['title']}" if t["next_step"] else ""
        acts = [("✅ Done", f"done:{t['id']}"), ("💤 Not now", f"notnow:{t['id']}")]
        if r["lead_minutes"]:
            k = f"{base}:lead"
            notes.append(Note(k, due - timedelta(minutes=r["lead_minutes"]),
                              pick("lead_hf" if hf else "lead", k, t=title, m=r["lead_minutes"]),
                              step, acts, critical=True, tag=base, window=timedelta(minutes=r["lead_minutes"])))
        k = f"{base}:at"
        notes.append(Note(k, due, pick("at", k, t=title), step, acts, critical=True, tag=base))
        snoozed = t["not_now_until"] and t["not_now_until"] > now.isoformat(timespec="seconds")
        if not snoozed:
            for i in range(1, r["max_follow_ups"] + 1):
                k = f"{base}:f{i}"
                notes.append(Note(k, due + timedelta(minutes=i * r["follow_up_minutes"]),
                                  pick("follow", k, t=title), step, acts,
                                  critical=t["priority"] == 2, tag=base))

    # ---- body nudges ----
    for n in p["nudges"]:
        if not n["enabled"]:
            continue
        kind, label = n["kind"], n["label"]
        crit = kind in CRITICAL_KINDS
        if "every_minutes" in n:
            start, end = _at(today, n["from"], tz), _at(today, n["to"], tz)
            last = tasks.last_checkin(uid, n["id"])
            recent = last and datetime.fromisoformat(last) > now - timedelta(minutes=n["every_minutes"] / 2)
            slot = start
            while slot <= min(now, end):
                nxt = slot + timedelta(minutes=n["every_minutes"])
                if now < nxt and not recent:
                    k = f"nudge:{n['id']}:{slot.isoformat(timespec='minutes')}"
                    notes.append(Note(k, slot, pick(kind if kind in T else "custom", k, l=label), "",
                                      [("✅ Done", f"took:{n['id']}")], critical=crit,
                                      window=nxt - slot, tag=f"nudge:{n['id']}"))
                slot = nxt
            continue
        for hm in n["times"]:
            at = _at(today, hm, tz)
            if now < at:
                continue
            last = tasks.last_checkin(uid, n["id"])
            if last and datetime.fromisoformat(last) >= at - timedelta(minutes=30):
                continue  # already done for this slot (or just before it)
            done_label = "✅ Took them" if kind == "meds" else "✅ Done"
            acts = [(done_label, f"took:{n['id']}"), ("⏰ Later", f"later:{n['id']}")]
            base = f"nudge:{n['id']}:{at.isoformat(timespec='minutes')}"
            notes.append(Note(f"{base}:0", at, pick(kind if kind in T else "custom", base, l=label),
                              "", acts, critical=crit, tag=base))
            follow = f"{kind}_follow" if f"{kind}_follow" in T else "custom_follow"
            for i in range(1, n["max_follow_ups"] + 1):
                k = f"{base}:{i}"
                notes.append(Note(k, at + timedelta(minutes=i * n["follow_up_minutes"]),
                                  pick(follow, k, l=label), "", acts, critical=crit, tag=base))

    # ---- routines ----
    def routine(name: str, conf: dict, body: str, actions: list, window_h: int):
        if not conf["enabled"]:
            return
        if name == "weekly" and today.weekday() != conf["day"]:
            return
        at = _at(today, conf["time"], tz)
        done = tasks.last_checkin(uid, f"routine:{name}")
        if done and done >= at.date().isoformat():
            return
        k = f"routine:{name}:{today.isoformat()}"
        notes.append(Note(k, at, pick(name, k), body, actions, window=timedelta(hours=window_h),
                          tag=f"routine:{name}"))

    if p["morning"]["enabled"] and now >= _at(today, p["morning"]["time"], tz):
        top = tasks.focus(uid, 3)
        body = "\n".join(f"• {t['title']}" for t in top) or "Nothing urgent. Good day to pick something fun."
        routine("morning", p["morning"], body,
                [("☀️ Plan my day", "url:/?routine=morning"), ("🪫", "energy:1"), ("🔋", "energy:2"),
                 ("⚡", "energy:3")], 3)
    if p["evening"]["enabled"] and now >= _at(today, p["evening"]["time"], tz):
        done = [t for t in tasks.done_on(uid, today) if not t["skipped"]]
        body = f"{len(done)} done today. Park the rest and pick tomorrow's first thing." if done \
            else "Park what's left and pick tomorrow's first thing. No judgement."
        routine("evening", p["evening"], body, [("🌙 Wind down", "url:/?routine=evening")], 3)
    if p["weekly"]["enabled"] and now >= _at(today, p["weekly"]["time"], tz):
        n = len(tasks.weekly(uid)["items"])
        body = f"{n} thing{'s' if n != 1 else ''} need a quick decision." if n else "Look back, look ahead."
        routine("weekly", p["weekly"], body, [("🧹 Start", "url:/?routine=weekly")], 6)

    # ---- focus sessions ----
    s = (sessions.active(uid) or sessions.latest(uid)) if sessions else None  # active() closes stale ones
    in_session = False
    if s and s["state"] in ("running", "overtime"):
        in_session = True
        k = f"session:{s['id']}:end:{s['minutes']}"
        notes.append(Note(k, datetime.fromisoformat(s["ends_at"]), pick("session_end", k, m=s["minutes"]),
                          s["task"]["title"] if s["task"] else s["intention"],
                          [("☕ Break", f"session:break:{s['id']}"), ("➕ 5 min", f"session:extend:{s['id']}")]
                          + ([("✅ Task done", f"session:done:{s['id']}")] if s["task_id"] else []),
                          critical=True, window=timedelta(minutes=60), tag=f"session:{s['id']}"))
    elif s and s["break_until"]:
        k = f"break:{s['id']}:{s['break_until']}"
        notes.append(Note(k, datetime.fromisoformat(s["break_until"]), pick("break_end", k),
                          s["task"]["title"] if s["task"] else "",
                          [("▶ Another round", f"session:again:{s['id']}"), ("✋ Done for now", f"session:rest:{s['id']}")],
                          critical=True, window=timedelta(minutes=30), tag=f"session:{s['id']}"))

    # ---- evening digest of your people's wins ----
    pp = p["people"]
    if people and pp["digest"] and now >= _at(today, pp["digest_time"], tz):
        for other in people.linked(uid):
            n = people.digest(uid, other)
            if n:
                name = (people.users.get(other) or {}).get("name") or "Your partner"
                k = f"digest:{other}:{today.isoformat()}"
                notes.append(Note(k, _at(today, pp["digest_time"], tz),
                                  f"💞 {name} finished {n} thing{'s' if n != 1 else ''} today",
                                  "Send some love?", [("💞 See wins", "url:/?people")],
                                  window=timedelta(hours=3), tag=k))

    # ---- end of hyperfocus ----
    row = tasks.db.one("SELECT dnd_until FROM users WHERE id=?", (uid,))
    if row and row["dnd_until"] and not in_session:
        end = datetime.fromisoformat(row["dnd_until"])
        k = f"hf_end:{row['dnd_until']}"
        notes.append(Note(k, end, pick("hf_end", k), "", [("🎧 15 more min", "hyperfocus:15")],
                          critical=True))

    return [n for n in notes if n.at <= now < n.at + n.window and (n.critical or not hf)]


class Scheduler:
    def __init__(self, tasks: Tasks, prefs: Prefs, sessions=None, people=None):
        self.tasks = tasks
        self.prefs = prefs
        self.sessions = sessions
        self.people = people
        self.channels: list = []    # objects with: async send(uid, note, prefs) -> None
        self._task: asyncio.Task | None = None

    def start(self) -> None:
        self._task = asyncio.create_task(self._loop())

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()

    async def _loop(self) -> None:
        while True:
            try:
                await self.tick()
            except asyncio.CancelledError:
                raise
            except Exception:
                log.exception("scheduler tick failed")
            await asyncio.sleep(30)

    def _claim(self, uid: int, key: str) -> bool:
        """Record that a note went out; False if it already had."""
        with self.tasks.db.tx() as c:
            cur = c.execute("INSERT OR IGNORE INTO sent_log(user_id, key, at) VALUES(?,?,?)",
                            (uid, key, self.tasks._stamp()))
            return cur.rowcount == 1

    async def tick(self) -> list[tuple[int, Note]]:
        sent = []
        for row in self.tasks.db.query("SELECT id FROM users"):
            uid = row["id"]
            notes = plan(self.tasks, self.prefs, uid, self.sessions, self.people)
            if not notes:
                continue
            p = self.prefs.get(uid)
            for note in notes:
                if not self._claim(uid, note.key):
                    continue
                for ch in self.channels:
                    try:
                        await ch.send(uid, note, p)
                    except Exception:
                        log.exception("channel %s failed", type(ch).__name__)
                sent.append((uid, note))
        return sent


    async def send_now(self, uid: int, note: Note) -> bool:
        """Send an event-driven note right away (e.g. a reaction). Quiet during hyperfocus
        unless critical; the dashboard still shows it."""
        if not note.critical and self.tasks.hyperfocus_until(uid):
            return False
        if not self._claim(uid, note.key):
            return False
        p = self.prefs.get(uid)
        for ch in self.channels:
            try:
                await ch.send(uid, note, p)
            except Exception:
                log.exception("channel %s failed", type(ch).__name__)
        return True


class PushChannel:
    def __init__(self, push):
        self.push = push

    async def send(self, uid: int, note: Note, p: dict) -> None:
        if not p["channels"]["push"]:
            return
        url = next((a[4:] for _, a in note.actions if a.startswith("url:")), "/")
        acts = [{"action": a, "title": label} for label, a in note.actions if not a.startswith("url:")]
        await self.push.send(uid, {"title": note.title, "body": note.body, "tag": note.tag or note.key,
                                   "url": url, "actions": acts[:2],
                                   "requireInteraction": note.critical})


def pending(tasks: Tasks, prefs: Prefs, uid: int) -> dict:
    """What the dashboard should offer right now: unconfirmed nudges and open routines."""
    now = tasks.now()
    today = now.date()
    p = prefs.get(uid)
    nudges = []
    for n in p["nudges"]:
        if not n["enabled"] or "times" not in n:
            continue
        passed = [hm for hm in n["times"] if _at(today, hm, now.tzinfo) <= now]
        if not passed:
            continue
        since = _at(today, passed[-1], now.tzinfo) - timedelta(minutes=30)
        last = tasks.last_checkin(uid, n["id"])
        if not last or datetime.fromisoformat(last) < since:
            nudges.append({"id": n["id"], "label": n["label"], "kind": n["kind"]})
    routines = []
    for name in ("morning", "evening", "weekly"):
        conf = p[name]
        if not conf["enabled"] or (name == "weekly" and today.weekday() != conf["day"]):
            continue
        start = _at(today, conf["time"], now.tzinfo)
        end = start + timedelta(hours=5 if name != "evening" else 4)
        done = tasks.last_checkin(uid, f"routine:{name}")
        if start <= now < end and not (done and done >= today.isoformat()):
            routines.append(name)
    return {"nudges": nudges, "routines": routines}
