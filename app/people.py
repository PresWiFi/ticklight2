"""People: link a partner (or anyone in the household). Linked people can
- see each other's wins and react to them,
- share tasks (🏠 shared tasks show up for both, either can tick them),
- send each other tasks.
Open tasks stay private unless shared."""

from __future__ import annotations

import secrets
from datetime import timedelta

from .db import DB

REACTIONS = ("🎉", "❤️", "👏", "🔥", "💪", "😂")


class People:
    def __init__(self, db: DB, tasks, prefs, users):
        self.db = db
        self.tasks = tasks
        self.prefs = prefs
        self.users = users

    def linked(self, uid: int) -> list[int]:
        return [r["other_id"] for r in self.db.query("SELECT other_id FROM links WHERE user_id=?", (uid,))]

    def is_linked(self, uid: int, other: int) -> bool:
        return other in self.linked(uid)

    # ---------- invites ----------
    def invite(self, uid: int, name: str = "") -> dict:
        code = secrets.token_urlsafe(9)
        expires = (self.tasks.now() + timedelta(hours=72)).isoformat(timespec="seconds")
        with self.db.tx() as c:
            c.execute("DELETE FROM invites WHERE expires_at < ?", (self.tasks._stamp(),))
            c.execute("INSERT INTO invites(code, from_user, name, expires_at) VALUES(?,?,?,?)",
                      (code, uid, (name or "").strip()[:40], expires))
        return {"code": code, "expires_at": expires}

    def invite_info(self, code: str) -> dict | None:
        row = self.db.one("SELECT * FROM invites WHERE code=? AND expires_at>=?", (code, self.tasks._stamp()))
        return dict(row) if row else None

    def redeem(self, code: str, uid: int) -> int:
        """Link `uid` with whoever made the invite. Returns the inviter's id."""
        inv = self.invite_info(code)
        if not inv:
            raise ValueError("that invite is wrong or expired")
        if inv["from_user"] == uid:
            raise ValueError("that's your own invite; open it on your partner's phone or account")
        stamp = self.tasks._stamp()
        with self.db.tx() as c:
            for a, b in ((uid, inv["from_user"]), (inv["from_user"], uid)):
                c.execute("INSERT OR IGNORE INTO links(user_id, other_id, created_at) VALUES(?,?,?)", (a, b, stamp))
            c.execute("DELETE FROM invites WHERE code=?", (code,))
        self.tasks._changed()
        return inv["from_user"]

    def new_member(self, name: str) -> int:
        """No-login mode: create the invited person's account."""
        with self.db.tx() as c:
            return c.execute("INSERT INTO users(name, created_at) VALUES(?,?)",
                             ((name or "Partner").strip()[:40], self.tasks._stamp())).lastrowid

    def unlink(self, uid: int, other: int) -> None:
        with self.db.tx() as c:
            c.execute("DELETE FROM links WHERE (user_id=? AND other_id=?) OR (user_id=? AND other_id=?)",
                      (uid, other, other, uid))
            # Their shared tasks stay theirs; nothing to move
        self.tasks._changed()

    # ---------- views ----------
    def _done_today(self, uid: int) -> int:
        return self.db.one("SELECT COUNT(*) FROM completions c JOIN tasks t ON t.id=c.task_id"
                           " WHERE COALESCE(c.by_user, t.user_id)=? AND c.skipped=0 AND c.done_at>=?",
                           (uid, self.tasks.today().isoformat()))[0]

    def summary(self, uid: int) -> list[dict]:
        out = []
        for other in self.linked(uid):
            u = self.users.get(other)
            if not u:
                continue
            shares = self.prefs.get(other)["people"]["share_wins"]
            out.append({"id": other, "name": u["name"] or "Partner",
                        "done_today": self._done_today(other) if shares else None})
        return out

    def feed(self, uid: int, days: int = 7) -> list[dict]:
        """Recent wins of linked people (and your own, so you can see their reactions)."""
        since = (self.tasks.today() - timedelta(days=days)).isoformat()
        people = {uid: self.users.get(uid)}
        for o in self.linked(uid):
            if self.prefs.get(o)["people"]["share_wins"]:
                people[o] = self.users.get(o)
        ids = list(people)
        marks = ",".join("?" * len(ids))
        rows = self.db.query(
            f"SELECT c.id, c.done_at, t.title, t.energy, COALESCE(c.by_user, t.user_id) AS who FROM completions c"
            f" JOIN tasks t ON t.id=c.task_id WHERE COALESCE(c.by_user, t.user_id) IN ({marks})"
            f" AND c.skipped=0 AND c.done_at>=? AND t.status!='deleted' ORDER BY c.done_at DESC LIMIT 100",
            (*ids, since))
        reacts: dict[int, list] = {}
        if rows:
            cm = ",".join("?" * len(rows))
            for r in self.db.query(f"SELECT r.completion_id, r.emoji, r.from_user, u.name FROM reactions r"
                                   f" JOIN users u ON u.id=r.from_user WHERE r.completion_id IN ({cm})",
                                   [r["id"] for r in rows]):
                reacts.setdefault(r["completion_id"], []).append(
                    {"emoji": r["emoji"], "from": r["from_user"], "name": r["name"]})
        out = []
        for r in rows:
            who = r["who"]
            titles = who == uid or self.prefs.get(who)["people"]["share_titles"]
            out.append({"completion_id": r["id"], "at": r["done_at"], "user_id": who,
                        "name": (people[who] or {}).get("name") or "Partner", "mine": who == uid,
                        "title": r["title"] if titles else "a task", "hard": r["energy"] == 3,
                        "reactions": reacts.get(r["id"], [])})
        return out

    def react(self, uid: int, completion_id: int, emoji: str) -> dict:
        if emoji not in REACTIONS:
            raise ValueError("unknown reaction")
        row = self.db.one("SELECT c.id, t.title, COALESCE(c.by_user, t.user_id) AS who FROM completions c"
                          " JOIN tasks t ON t.id=c.task_id WHERE c.id=?", (completion_id,))
        if not row or not self.is_linked(uid, row["who"]):
            raise ValueError("you can only react to your people's wins")
        with self.db.tx() as c:
            cur = c.execute("INSERT OR IGNORE INTO reactions(from_user, completion_id, emoji, at) VALUES(?,?,?,?)",
                            (uid, completion_id, emoji, self.tasks._stamp()))
        self.tasks._changed()
        titles = self.prefs.get(row["who"])["people"]["share_titles"]
        return {"to": row["who"], "title": row["title"] if titles else "a task", "new": cur.rowcount == 1}

    def digest(self, uid: int, other: int) -> int:
        """How many things `other` finished today (0 if they don't share)."""
        if not self.prefs.get(other)["people"]["share_wins"]:
            return 0
        return self._done_today(other)
