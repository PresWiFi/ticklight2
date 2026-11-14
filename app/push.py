"""Web Push (installable web app notifications). Keys are generated on first start."""

from __future__ import annotations

import asyncio
import base64
import json
import logging

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec
from py_vapid import Vapid
from pywebpush import WebPushException, webpush

from .db import DB

log = logging.getLogger("ticklight.push")


class Push:
    def __init__(self, db: DB, subject: str, now):
        self.db = db
        self.subject = subject
        self.now = now
        pem = db.get_meta("vapid_private_pem")
        if not pem:
            key = ec.generate_private_key(ec.SECP256R1())
            pem = key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                    serialization.NoEncryption()).decode()
            db.set_meta("vapid_private_pem", pem)
        self.vapid = Vapid.from_pem(pem.encode())
        raw = self.vapid.public_key.public_bytes(serialization.Encoding.X962,
                                                 serialization.PublicFormat.UncompressedPoint)
        self.public_key = base64.urlsafe_b64encode(raw).rstrip(b"=").decode()

    def subscribe(self, uid: int, sub: dict, label: str = "") -> None:
        keys = sub.get("keys") or {}
        if not (sub.get("endpoint", "").startswith("https://") and keys.get("p256dh") and keys.get("auth")):
            raise ValueError("invalid push subscription")
        with self.db.tx() as c:
            c.execute("INSERT INTO push_subs(user_id, endpoint, p256dh, auth, label, created_at)"
                      " VALUES(?,?,?,?,?,?) ON CONFLICT(endpoint) DO UPDATE SET user_id=excluded.user_id,"
                      " p256dh=excluded.p256dh, auth=excluded.auth, label=excluded.label",
                      (uid, sub["endpoint"], keys["p256dh"], keys["auth"], label[:60],
                       self.now().isoformat(timespec="seconds")))

    def unsubscribe(self, uid: int, endpoint: str) -> None:
        with self.db.tx() as c:
            c.execute("DELETE FROM push_subs WHERE user_id=? AND endpoint=?", (uid, endpoint))

    def count(self, uid: int) -> int:
        return self.db.one("SELECT COUNT(*) FROM push_subs WHERE user_id=?", (uid,))[0]

    def _send_one(self, row, data: str) -> bool:
        try:
            webpush({"endpoint": row["endpoint"], "keys": {"p256dh": row["p256dh"], "auth": row["auth"]}},
                    data, vapid_private_key=self.vapid, vapid_claims={"sub": self.subject},
                    ttl=3600, timeout=10)
            return True
        except WebPushException as e:
            status = getattr(e.response, "status_code", None)
            if status in (404, 410):  # subscription is gone
                with self.db.tx() as c:
                    c.execute("DELETE FROM push_subs WHERE id=?", (row["id"],))
            else:
                log.warning("push failed (%s): %s", status, e)
        except Exception as e:
            log.warning("push failed: %s", e)
        return False

    async def send(self, uid: int, payload: dict) -> int:
        rows = self.db.query("SELECT * FROM push_subs WHERE user_id=?", (uid,))
        data = json.dumps(payload)
        results = await asyncio.gather(*(asyncio.to_thread(self._send_one, r, data) for r in rows))
        return sum(results)
