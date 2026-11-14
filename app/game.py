"""Game layer: coins, a shop, a wardrobe for the companion, themes, a daily streak with
freezes, weekly challenges, badges, and seasonal events. Built for novelty: something new
to chase every week, nothing that punishes a bad day."""

from __future__ import annotations

import json
import random
from datetime import date, datetime, timedelta

from .db import DB

FREEZE_PRICE = 50
MAX_FREEZES = 3

# Seasonal events (MM-DD windows; may wrap the new year)
EVENTS = [
    {"id": "spooky", "name": "Spooky season", "emoji": "🎃", "start": "10-01", "end": "10-31",
     "blurb": "Witch hat and the Spooky theme are in the shop until Halloween."},
    {"id": "frost", "name": "Frost fest", "emoji": "❄️", "start": "12-01", "end": "01-06",
     "blurb": "Cosy season: Santa hat and the Frost theme, for a limited time."},
    {"id": "bloom", "name": "Bloom", "emoji": "🌸", "start": "03-20", "end": "05-31",
     "blurb": "Spring is here: flower crown and the Bloom theme."},
    {"id": "summer", "name": "Summer break", "emoji": "🏖", "start": "07-01", "end": "08-31",
     "blurb": "Sun hat and the Beach theme while summer lasts."},
]

# kind: accessory (with slot) or theme. price None = earned, not bought.
CATALOG: dict[str, dict] = {
    # head
    "party_hat":   {"kind": "accessory", "slot": "head", "name": "Party hat", "emoji": "🎉", "price": 40, "level": 2},
    "beanie":      {"kind": "accessory", "slot": "head", "name": "Beanie", "emoji": "🧶", "price": 60, "level": 3},
    "headphones":  {"kind": "accessory", "slot": "head", "name": "Headphones", "emoji": "🎧", "price": 80, "level": 4},
    "wizard_hat":  {"kind": "accessory", "slot": "head", "name": "Wizard hat", "emoji": "🧙", "price": 150, "level": 6},
    "crown":       {"kind": "accessory", "slot": "head", "name": "Crown", "emoji": "👑", "price": 200, "level": 8},
    "halo":        {"kind": "accessory", "slot": "head", "name": "Halo", "emoji": "😇", "price": 300, "level": 12},
    "witch_hat":   {"kind": "accessory", "slot": "head", "name": "Witch hat", "emoji": "🧹", "price": 60, "level": 1, "event": "spooky"},
    "santa_hat":   {"kind": "accessory", "slot": "head", "name": "Santa hat", "emoji": "🎅", "price": 60, "level": 1, "event": "frost"},
    "flower_crown": {"kind": "accessory", "slot": "head", "name": "Flower crown", "emoji": "🌸", "price": 60, "level": 1, "event": "bloom"},
    "sun_hat":     {"kind": "accessory", "slot": "head", "name": "Sun hat", "emoji": "👒", "price": 60, "level": 1, "event": "summer"},
    # face
    "blush":       {"kind": "accessory", "slot": "face", "name": "Blush", "emoji": "😊", "price": 30, "level": 1},
    "glasses":     {"kind": "accessory", "slot": "face", "name": "Glasses", "emoji": "🤓", "price": 50, "level": 2},
    "shades":      {"kind": "accessory", "slot": "face", "name": "Shades", "emoji": "😎", "price": 90, "level": 4},
    "monocle":     {"kind": "accessory", "slot": "face", "name": "Monocle", "emoji": "🧐", "price": 120, "level": 5},
    # neck
    "bowtie":      {"kind": "accessory", "slot": "neck", "name": "Bow tie", "emoji": "🎀", "price": 40, "level": 2},
    "scarf":       {"kind": "accessory", "slot": "neck", "name": "Scarf", "emoji": "🧣", "price": 70, "level": 3},
    "medal":       {"kind": "accessory", "slot": "neck", "name": "Medal", "emoji": "🏅", "price": None, "level": 1,
                    "how": "Badge: 100 tasks"},
    # aura
    "sparkles":    {"kind": "accessory", "slot": "aura", "name": "Sparkles", "emoji": "✨", "price": 150, "level": 5},
    "rainbow":     {"kind": "accessory", "slot": "aura", "name": "Rainbow", "emoji": "🌈", "price": 250, "level": 10},
    "fire":        {"kind": "accessory", "slot": "aura", "name": "Fire aura", "emoji": "🔥", "price": None, "level": 1,
                    "how": "Badge: 14-day streak"},
    # themes
    "midnight":    {"kind": "theme", "name": "Midnight", "emoji": "🌙", "price": 0, "level": 1},
    "paper":       {"kind": "theme", "name": "Paper", "emoji": "📄", "price": 0, "level": 1},
    "terminal":    {"kind": "theme", "name": "Terminal", "emoji": "💻", "price": 100, "level": 3},
    "forest":      {"kind": "theme", "name": "Forest", "emoji": "🌲", "price": 80, "level": 2},
    "ocean":       {"kind": "theme", "name": "Ocean", "emoji": "🌊", "price": 80, "level": 2},
    "candy":       {"kind": "theme", "name": "Candy", "emoji": "🍬", "price": 120, "level": 5},
    "sunset":      {"kind": "theme", "name": "Sunset", "emoji": "🌅", "price": 120, "level": 4},
    "synthwave":   {"kind": "theme", "name": "Synthwave", "emoji": "🕹", "price": 150, "level": 6},
    "spooky":      {"kind": "theme", "name": "Spooky", "emoji": "🎃", "price": 50, "level": 1, "event": "spooky"},
    "frost":       {"kind": "theme", "name": "Frost", "emoji": "❄️", "price": 50, "level": 1, "event": "frost"},
    "bloom":       {"kind": "theme", "name": "Bloom", "emoji": "🌸", "price": 50, "level": 1, "event": "bloom"},
    "beach":       {"kind": "theme", "name": "Beach", "emoji": "🏖", "price": 50, "level": 1, "event": "summer"},
}
FREE = ["midnight", "paper"]

# Weekly challenges: kind -> (text template, possible targets, coins per difficulty step)
CHALLENGES = {
    "tasks":   ("Finish {n} tasks", [8, 12, 18]),
    "hard":    ("Finish {n} high-energy tasks", [2, 3, 4]),
    "sessions": ("Do {n} focus sessions (10+ min)", [2, 3, 5]),
    "minutes": ("Focus for {n} minutes in sessions", [60, 100, 150]),
    "mornings": ("Do the morning launch {n} times", [3, 4, 5]),
    "evenings": ("Do the evening shutdown {n} times", [3, 4, 5]),
    "steps":   ("Tick {n} steps", [8, 12, 20]),
    "areas":   ("Finish tasks in {n} different life areas", [3, 4, 5]),
    "early":   ("Finish {n} tasks before 10am", [3, 4, 6]),
    "pinned":  ("Finish {n} of your daily picks", [4, 6, 9]),
}

BADGES = [
    ("first_task", "✅", "First tick", "Finish your first task"),
    ("tasks_10", "🔟", "Getting going", "Finish 10 tasks"),
    ("tasks_100", "💯", "Century", "Finish 100 tasks (unlocks the Medal)"),
    ("tasks_500", "🏆", "Unstoppable", "Finish 500 tasks"),
    ("hard_10", "💪", "Brave", "Finish 10 high-energy tasks"),
    ("level_5", "⭐", "Level 5", "Reach level 5"),
    ("level_10", "🌟", "Level 10", "Reach level 10"),
    ("level_20", "💫", "Level 20", "Reach level 20"),
    ("first_session", "⏱", "Company", "Do your first focus session"),
    ("sessions_10", "🎯", "In the zone", "Do 10 focus sessions"),
    ("focus_600", "🧠", "Deep work", "Focus for 10 hours in sessions"),
    ("streak_7", "🔥", "Week on fire", "7-day streak (+1 streak freeze)"),
    ("streak_14", "☄️", "Fortnight", "14-day streak (unlocks the Fire aura)"),
    ("streak_30", "🌋", "Monthly", "30-day streak"),
    ("early_bird", "🐦", "Early bird", "Finish a task before 7am"),
    ("night_owl", "🦉", "Night owl", "Finish a task after 11pm"),
    ("comeback", "🌱", "Comeback", "Finish a task after 3+ days away"),
    ("clean_slate", "🧹", "Clean slate", "Finish a weekly reset"),
    ("brain_dump", "🌪", "Brain dump", "Capture 5+ tasks in one go"),
    ("challenge", "🎖", "Challenger", "Claim a weekly challenge"),
]
BADGE_REWARDS = {"tasks_100": "medal", "streak_14": "fire"}


def _in_window(day: date, start: str, end: str) -> bool:
    md = day.strftime("%m-%d")
    return start <= md <= end if start <= end else (md >= start or md <= end)


class Game:
    def __init__(self, db: DB, tasks, rng: random.Random | None = None):
        self.db = db
        self.tasks = tasks
        self.rng = rng or random.Random()

    # ---------- state ----------
    def _state(self, uid: int) -> dict:
        row = self.db.one("SELECT * FROM game_state WHERE user_id=?", (uid,))
        if not row:
            with self.db.tx() as c:
                c.execute("INSERT OR IGNORE INTO game_state(user_id) VALUES(?)", (uid,))
            row = self.db.one("SELECT * FROM game_state WHERE user_id=?", (uid,))
        return {"inventory": json.loads(row["inventory"]), "equipped": json.loads(row["equipped"]),
                "freezes": row["freezes"], "theme": row["theme"]}

    def _save(self, uid: int, **cols) -> None:
        self._state(uid)
        sets = {k: json.dumps(v) if isinstance(v, (list, dict)) else v for k, v in cols.items()}
        with self.db.tx() as c:
            c.execute(f"UPDATE game_state SET {', '.join(f'{k}=?' for k in sets)} WHERE user_id=?",
                      (*sets.values(), uid))
        self.tasks._changed()

    def coins(self, uid: int) -> int:
        return self.db.one("SELECT COALESCE(SUM(amount),0) FROM coin_events WHERE user_id=?", (uid,))[0]

    def _coin(self, c, uid: int, amount: int, reason: str) -> None:
        c.execute("INSERT INTO coin_events(user_id, amount, reason, created_at) VALUES(?,?,?,?)",
                  (uid, amount, reason, self.tasks._stamp()))

    def event(self, day: date | None = None) -> dict | None:
        day = day or self.tasks.today()
        return next((e for e in EVENTS if _in_window(day, e["start"], e["end"])), None)

    def owned(self, uid: int) -> set[str]:
        return set(FREE) | set(self._state(uid)["inventory"])

    # ---------- shop / wardrobe / themes ----------
    def buy(self, uid: int, item_id: str) -> dict:
        level = self.tasks.stats(uid)["level"]
        coins = self.coins(uid)
        if item_id == "freeze":
            st = self._state(uid)
            if st["freezes"] >= MAX_FREEZES:
                raise ValueError(f"you can hold {MAX_FREEZES} freezes at most")
            if coins < FREEZE_PRICE:
                raise ValueError("not enough coins")
            with self.db.tx() as c:
                self._coin(c, uid, -FREEZE_PRICE, "buy:freeze")
                c.execute("UPDATE game_state SET freezes=freezes+1 WHERE user_id=?", (uid,))
            self.tasks._changed()
            return self.summary(uid)
        item = CATALOG.get(item_id)
        if not item or item["price"] is None:
            raise ValueError("that can't be bought")
        if item_id in self.owned(uid):
            raise ValueError("you already have that")
        if item.get("event") and (self.event() or {}).get("id") != item["event"]:
            raise ValueError("that's only available during its seasonal event")
        if level < item["level"]:
            raise ValueError(f"unlocks at level {item['level']}")
        if coins < item["price"]:
            raise ValueError("not enough coins")
        st = self._state(uid)
        with self.db.tx() as c:
            self._coin(c, uid, -item["price"], f"buy:{item_id}")
        self._save(uid, inventory=st["inventory"] + [item_id])
        return self.summary(uid)

    def grant(self, uid: int, item_id: str) -> None:
        st = self._state(uid)
        if item_id not in st["inventory"]:
            self._save(uid, inventory=st["inventory"] + [item_id])

    def equip(self, uid: int, item_id: str | None, slot: str | None = None) -> dict:
        st = self._state(uid)
        eq = dict(st["equipped"])
        if item_id:
            item = CATALOG.get(item_id)
            if not item or item["kind"] != "accessory" or item_id not in self.owned(uid):
                raise ValueError("you don't have that")
            eq[item["slot"]] = item_id
        elif slot:
            eq.pop(slot, None)
        self._save(uid, equipped=eq)
        return self.summary(uid)

    def set_theme(self, uid: int, theme: str) -> dict:
        if theme not in ("auto", "weekly") and (theme not in CATALOG or CATALOG[theme]["kind"] != "theme"
                                                or theme not in self.owned(uid)):
            raise ValueError("you don't have that theme")
        self._save(uid, theme=theme)
        return self.summary(uid)

    def resolved_theme(self, uid: int) -> str | None:
        """None = follow the light/dark switch."""
        t = self._state(uid)["theme"]
        if t == "auto":
            return None
        if t == "weekly":
            owned = sorted(x for x in self.owned(uid) if CATALOG[x]["kind"] == "theme" and x not in FREE)
            if not owned:
                return None
            y, w, _ = self.tasks.today().isocalendar()
            return owned[(y * 53 + w) % len(owned)]
        return t

    # ---------- daily streak ----------
    def _done_days(self, uid: int, since: date) -> set[str]:
        rows = self.db.query(
            "SELECT DISTINCT substr(c.done_at,1,10) AS d FROM completions c JOIN tasks t ON t.id=c.task_id"
            " WHERE COALESCE(c.by_user, t.user_id)=? AND c.skipped=0 AND c.done_at>=?", (uid, since.isoformat()))
        frozen = self.db.query("SELECT day FROM freeze_days WHERE user_id=? AND day>=?", (uid, since.isoformat()))
        return {r["d"] for r in rows} | {r["day"] for r in frozen}

    def streak(self, uid: int, upto: date | None = None) -> int:
        """Days in a row with something done (or a freeze), ending today or yesterday."""
        today = upto or self.tasks.today()
        days = self._done_days(uid, today - timedelta(days=400))
        d = today if today.isoformat() in days else today - timedelta(days=1)
        n = 0
        while d.isoformat() in days:
            n += 1
            d -= timedelta(days=1)
        return n

    def rollover(self, uid: int) -> list[str]:
        """Once a day: spend freezes on missed days so the streak survives. Returns days frozen."""
        today = self.tasks.today()
        key = f"game:rollover:{uid}"
        last = self.db.get_meta(key)
        self.db.set_meta(key, today.isoformat())
        if not last or last >= today.isoformat():
            return []
        frozen = []
        d = max(date.fromisoformat(last), today - timedelta(days=7))
        while d < today:
            days = self._done_days(uid, d - timedelta(days=1))
            st = self._state(uid)
            if d.isoformat() not in days and st["freezes"] > 0 and self.streak(uid, d - timedelta(days=1)) > 0:
                with self.db.tx() as c:
                    c.execute("INSERT OR IGNORE INTO freeze_days(user_id, day) VALUES(?,?)", (uid, d.isoformat()))
                    c.execute("UPDATE game_state SET freezes=freezes-1 WHERE user_id=?", (uid,))
                frozen.append(d.isoformat())
            d += timedelta(days=1)
        if frozen:
            self.tasks._changed()
        return frozen

    # ---------- weekly challenges ----------
    def _week(self, day: date | None = None) -> date:
        day = day or self.tasks.today()
        return day - timedelta(days=day.weekday())

    def _progress(self, uid: int, kind: str, start: date, end: date) -> int:
        a, b = start.isoformat(), end.isoformat()
        q = self.db.one
        done = ("FROM completions c JOIN tasks t ON t.id=c.task_id WHERE COALESCE(c.by_user, t.user_id)=? AND c.skipped=0"
                " AND c.done_at>=? AND c.done_at<?")
        if kind == "tasks":
            return q(f"SELECT COUNT(*) {done}", (uid, a, b))[0]
        if kind == "hard":
            return q(f"SELECT COUNT(*) {done} AND t.energy=3", (uid, a, b))[0]
        if kind == "areas":
            return q(f"SELECT COUNT(DISTINCT t.area) {done} AND t.area IS NOT NULL", (uid, a, b))[0]
        if kind == "early":
            return q(f"SELECT COUNT(*) {done} AND substr(c.done_at,12,5) < '10:00'", (uid, a, b))[0]
        if kind == "pinned":
            return q(f"SELECT COUNT(*) {done} AND t.pinned_on = substr(c.done_at,1,10)", (uid, a, b))[0]
        if kind == "steps":
            return q("SELECT COUNT(*) FROM xp_events WHERE user_id=? AND reason LIKE 'step:%'"
                     " AND created_at>=? AND created_at<?", (uid, a, b))[0]
        if kind in ("mornings", "evenings"):
            name = "routine:morning" if kind == "mornings" else "routine:evening"
            return q("SELECT COUNT(DISTINCT substr(at,1,10)) FROM checkins WHERE user_id=? AND kind=?"
                     " AND at>=? AND at<?", (uid, name, a, b))[0]
        if kind in ("sessions", "minutes"):
            rows = self.db.query("SELECT started_at, ended_at, minutes FROM focus_sessions WHERE user_id=?"
                                 " AND started_at>=? AND started_at<? AND ended_at IS NOT NULL", (uid, a, b))
            mins = [min((datetime.fromisoformat(r["ended_at"]) - datetime.fromisoformat(r["started_at"])
                         ).total_seconds() / 60, r["minutes"] + 30) for r in rows]
            return sum(1 for m in mins if m >= 10) if kind == "sessions" else int(sum(mins))
        return 0

    def challenges(self, uid: int) -> list[dict]:
        week = self._week()
        rows = self.db.query("SELECT * FROM challenges WHERE user_id=? AND week=? ORDER BY slot",
                             (uid, week.isoformat()))
        if not rows:
            rnd = random.Random(f"{uid}:{week.isoformat()}")
            # Slightly harder if last week went well, easier if it didn't
            last = self._progress(uid, "tasks", week - timedelta(days=7), week)
            level = 0 if last < 8 else 1 if last < 20 else 2
            kinds = rnd.sample(sorted(CHALLENGES), 3)
            with self.db.tx() as c:
                for slot, kind in enumerate(kinds):
                    target = CHALLENGES[kind][1][min(2, level + (1 if slot == 2 else 0))]
                    c.execute("INSERT OR IGNORE INTO challenges(user_id, week, slot, kind, target, coins, xp)"
                              " VALUES(?,?,?,?,?,?,?)", (uid, week.isoformat(), slot, kind, target,
                                                         30 + 15 * slot + 10 * level, 25 + 10 * slot))
            rows = self.db.query("SELECT * FROM challenges WHERE user_id=? AND week=? ORDER BY slot",
                                 (uid, week.isoformat()))
        out = []
        for r in rows:
            prog = self._progress(uid, r["kind"], week, week + timedelta(days=7))
            out.append({"slot": r["slot"], "kind": r["kind"], "text": CHALLENGES[r["kind"]][0].format(n=r["target"]),
                        "target": r["target"], "progress": min(prog, r["target"]), "coins": r["coins"],
                        "xp": r["xp"], "complete": prog >= r["target"], "claimed": bool(r["claimed_at"])})
        return out

    def claim(self, uid: int, slot: int) -> dict:
        ch = next((c for c in self.challenges(uid) if c["slot"] == slot), None)
        if not ch or not ch["complete"]:
            raise ValueError("that challenge isn't finished yet")
        if ch["claimed"]:
            raise ValueError("already claimed")
        stamp = self.tasks._stamp()
        with self.db.tx() as c:
            c.execute("UPDATE challenges SET claimed_at=? WHERE user_id=? AND week=? AND slot=?",
                      (stamp, uid, self._week().isoformat(), slot))
            self._coin(c, uid, ch["coins"], f"challenge:{ch['kind']}")
            c.execute("INSERT INTO xp_events(user_id, amount, reason, created_at) VALUES(?,?,?,?)",
                      (uid, ch["xp"], f"challenge:{ch['kind']}", stamp))
        self.tasks._changed()
        return {"coins": ch["coins"], "xp": ch["xp"], "text": ch["text"]}

    # ---------- badges ----------
    def check_badges(self, uid: int) -> list[dict]:
        have = {r["key"] for r in self.db.query("SELECT key FROM achievements WHERE user_id=?", (uid,))}
        q = lambda sql, *a: self.db.one(sql, (uid, *a))[0]  # noqa: E731
        done = ("FROM completions c JOIN tasks t ON t.id=c.task_id WHERE COALESCE(c.by_user, t.user_id)=? AND c.skipped=0")
        n_done = q(f"SELECT COUNT(*) {done}")
        level = self.tasks.stats(uid)["level"]
        sessions = self.db.query("SELECT started_at, ended_at, minutes FROM focus_sessions WHERE user_id=?"
                                 " AND ended_at IS NOT NULL", (uid,))
        focus_min = sum(min((datetime.fromisoformat(r["ended_at"]) - datetime.fromisoformat(r["started_at"])
                             ).total_seconds() / 60, r["minutes"] + 30) for r in sessions)
        streak = self.streak(uid)
        checks = {
            "first_task": n_done >= 1, "tasks_10": n_done >= 10, "tasks_100": n_done >= 100,
            "tasks_500": n_done >= 500, "hard_10": q(f"SELECT COUNT(*) {done} AND t.energy=3") >= 10,
            "level_5": level >= 5, "level_10": level >= 10, "level_20": level >= 20,
            "first_session": len(sessions) >= 1, "sessions_10": len(sessions) >= 10, "focus_600": focus_min >= 600,
            "streak_7": streak >= 7, "streak_14": streak >= 14, "streak_30": streak >= 30,
            "early_bird": q(f"SELECT COUNT(*) {done} AND substr(c.done_at,12,5) < '07:00'") > 0,
            "night_owl": q(f"SELECT COUNT(*) {done} AND substr(c.done_at,12,5) >= '23:00'") > 0,
            "clean_slate": q("SELECT COUNT(*) FROM checkins WHERE user_id=? AND kind='routine:weekly'") > 0,
            "brain_dump": q("SELECT COUNT(*) FROM (SELECT capture_id FROM tasks WHERE user_id=? AND capture_id"
                            " IS NOT NULL GROUP BY capture_id HAVING COUNT(*) >= 5)") > 0,
            "challenge": q("SELECT COUNT(*) FROM challenges WHERE user_id=? AND claimed_at IS NOT NULL") > 0,
        }
        if "comeback" not in have:
            days = sorted(r["d"] for r in self.db.query(
                "SELECT DISTINCT substr(c.done_at,1,10) AS d FROM completions c JOIN tasks t ON t.id=c.task_id"
                " WHERE COALESCE(c.by_user, t.user_id)=? AND c.skipped=0", (uid,)))
            checks["comeback"] = any((date.fromisoformat(b) - date.fromisoformat(a)).days > 3
                                     for a, b in zip(days, days[1:], strict=False))
        new = [k for k, ok in checks.items() if ok and k not in have]
        if not new:
            return []
        stamp = self.tasks._stamp()
        with self.db.tx() as c:
            for k in new:
                c.execute("INSERT OR IGNORE INTO achievements(user_id, key, at) VALUES(?,?,?)", (uid, k, stamp))
                if k == "streak_7":
                    c.execute("UPDATE game_state SET freezes=MIN(freezes+1, ?) WHERE user_id=?", (MAX_FREEZES, uid))
        for k in new:
            if k in BADGE_REWARDS:
                self.grant(uid, BADGE_REWARDS[k])
        self.tasks._changed()
        info = {b[0]: b for b in BADGES}
        return [{"key": k, "emoji": info[k][1], "name": info[k][2], "desc": info[k][3]} for k in new]

    def unseen_badges(self, uid: int) -> list[dict]:
        info = {b[0]: b for b in BADGES}
        rows = self.db.query("SELECT key FROM achievements WHERE user_id=? AND seen=0", (uid,))
        return [{"key": r["key"], "emoji": info[r["key"]][1], "name": info[r["key"]][2],
                 "desc": info[r["key"]][3]} for r in rows if r["key"] in info]

    def mark_seen(self, uid: int) -> None:
        with self.db.tx() as c:
            c.execute("UPDATE achievements SET seen=1 WHERE user_id=?", (uid,))

    # ---------- views ----------
    def summary(self, uid: int) -> dict:
        """Small, for the dashboard header and avatar."""
        st = self._state(uid)
        ev = self.event()
        claimable = sum(1 for c in self.challenges(uid) if c["complete"] and not c["claimed"])
        return {"coins": self.coins(uid), "streak": self.streak(uid), "freezes": st["freezes"],
                "equipped": st["equipped"], "theme": self.resolved_theme(uid), "theme_setting": st["theme"],
                "event": ev, "claimable": claimable}

    def full(self, uid: int) -> dict:
        level = self.tasks.stats(uid)["level"]
        owned = self.owned(uid)
        coins = self.coins(uid)
        ev = (self.event() or {}).get("id")
        items = []
        for iid, it in CATALOG.items():
            if it.get("event") and it["event"] != ev and iid not in owned:
                continue  # seasonal and not in season: hide
            items.append({"id": iid, **it, "owned": iid in owned,
                          "locked": level < it["level"], "affordable": it["price"] is not None and coins >= it["price"]})
        have = {r["key"]: r["at"] for r in self.db.query("SELECT key, at FROM achievements WHERE user_id=?", (uid,))}
        return {**self.summary(uid), "level": level, "items": items, "freeze_price": FREEZE_PRICE,
                "max_freezes": MAX_FREEZES, "challenges": self.challenges(uid),
                "badges": [{"key": k, "emoji": e, "name": n, "desc": d, "at": have.get(k)} for k, e, n, d in BADGES]}
