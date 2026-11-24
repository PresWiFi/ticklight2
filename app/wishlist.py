"""Wishlist: things you want but can't get yet. Not tasks, so they never nag or count as late."""

from __future__ import annotations

import re

from .tasks import NotFound, Tasks

TITLE_MAX = 120
LINK = re.compile(r"https?://\S+", re.I)
PRICE = re.compile(r"(?<![\w.])[£$€]\s?\d[\d,]*(?:\.\d{1,2})?(?:\s?[kK]\b)?")
STATUSES = ("want", "got")


def split_title(text: str) -> tuple[str, str, str]:
    """"Headphones £200 https://shop/x" -> ("Headphones", "£200", "https://shop/x")."""
    text = " ".join((text or "").split())
    link = (m := LINK.search(text)) and m.group().rstrip(".,)") or ""
    text = LINK.sub(" ", text)
    price = (m := PRICE.search(text)) and m.group().replace(" ", "") or ""
    text = PRICE.sub(" ", text)
    title = " ".join(text.split()).strip(" -–,:;") or link
    return title[:TITLE_MAX], price[:20], link[:500]


class Wishlist:
    def __init__(self, tasks: Tasks):
        self.tasks = tasks
        self.db = tasks.db

    def _row(self, uid: int, wish_id: int) -> dict:
        row = self.db.one("SELECT * FROM wishes WHERE id=? AND user_id=?", (wish_id, uid))
        if not row:
            raise NotFound(f"wish {wish_id}")
        return dict(row)

    def add(self, uid: int, text: str, note: str = "", capture_id: int | None = None) -> dict:
        title, price, link = split_title(text)
        if not title:
            raise ValueError("what do you want?")
        stamp = self.tasks._stamp()
        with self.db.tx() as c:
            wid = c.execute("INSERT INTO wishes(user_id, title, price, link, note, capture_id, created_at, updated_at)"
                            " VALUES(?,?,?,?,?,?,?,?)",
                            (uid, title, price, link, (note or "").strip()[:500], capture_id, stamp, stamp)).lastrowid
        self.tasks._changed()
        return self._row(uid, wid)

    def list(self, uid: int, status: str = "want", limit: int = 200) -> list[dict]:
        if status not in STATUSES:
            raise ValueError("status must be want or got")
        order = "created_at DESC" if status == "want" else "got_at DESC"
        return [dict(r) for r in self.db.query(
            f"SELECT * FROM wishes WHERE user_id=? AND status=? ORDER BY {order}, id DESC LIMIT ?",
            (uid, status, limit))]

    def update(self, uid: int, wish_id: int, changes: dict) -> dict:
        w = self._row(uid, wish_id)
        cols: dict = {}
        if changes.get("title") is not None:
            title = " ".join(changes["title"].split())[:TITLE_MAX]
            if not title:
                raise ValueError("a wish needs a name")
            cols["title"] = title
        for key, limit in (("price", 20), ("link", 500), ("note", 500)):
            if changes.get(key) is not None:
                cols[key] = changes[key].strip()[:limit]
        if cols.get("link") and not LINK.fullmatch(cols["link"]):
            raise ValueError("links need to start with http:// or https://")
        if (status := changes.get("status")) is not None:
            if status not in STATUSES:
                raise ValueError("status must be want or got")
            if status != w["status"]:
                cols["status"] = status
                cols["got_at"] = self.tasks._stamp() if status == "got" else None
        if cols:
            cols["updated_at"] = self.tasks._stamp()
            with self.db.tx() as c:
                c.execute(f"UPDATE wishes SET {', '.join(k + '=?' for k in cols)} WHERE id=?",
                          (*cols.values(), wish_id))
            self.tasks._changed()
        return self._row(uid, wish_id)

    def delete(self, uid: int, wish_id: int) -> dict:
        self._row(uid, wish_id)
        with self.db.tx() as c:
            c.execute("DELETE FROM wishes WHERE id=?", (wish_id,))
        self.tasks._changed()
        return {"ok": True}

    def undo_capture(self, uid: int, capture_id: int) -> int:
        with self.db.tx() as c:
            n = c.execute("DELETE FROM wishes WHERE capture_id=? AND user_id=?", (capture_id, uid)).rowcount
        if n:
            self.tasks._changed()
        return n

    def summary(self, uid: int) -> dict:
        rows = self.list(uid, "want", 3)
        n = self.db.one("SELECT COUNT(*) FROM wishes WHERE user_id=? AND status='want'", (uid,))[0]
        return {"count": n, "recent": [r["title"] for r in rows]}
