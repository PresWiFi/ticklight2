"""Users, login sessions, device tokens and OIDC (tested with Authentik).

AUTH_MODE=none: no login; everyone is the first user (trusted LAN only).
AUTH_MODE=oidc: browser logs in via OIDC; scripts, the desk monitor and the watch use
device tokens (Authorization: Bearer <token>, or ?token=<token> for kiosk URLs).
"""

from __future__ import annotations

import base64
import hashlib
import json
import secrets
import time
from datetime import timedelta
from urllib.parse import urlencode

import httpx

from .config import Settings
from .db import DB

COOKIE = "tl_session"


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


class AuthError(Exception):
    pass


class Users:
    def __init__(self, db: DB, now):
        self.db = db
        self.now = now  # callable returning an aware datetime

    def _stamp(self) -> str:
        return self.now().isoformat(timespec="seconds")

    def get(self, uid: int) -> dict | None:
        row = self.db.one("SELECT id, name, email, telegram_id FROM users WHERE id=?", (uid,))
        return dict(row) if row else None

    def rename(self, uid: int, name: str) -> dict:
        name = (name or "").strip()[:40]
        if not name:
            raise ValueError("name can't be empty")
        with self.db.tx() as c:
            c.execute("UPDATE users SET name=? WHERE id=?", (name, uid))
        return self.get(uid)

    def owner(self) -> int:
        """The first user, created on demand. Used in no-auth mode and for Telegram."""
        row = self.db.one("SELECT id FROM users ORDER BY id LIMIT 1")
        if row:
            return row["id"]
        with self.db.tx() as c:
            return c.execute("INSERT INTO users(name, created_at) VALUES('Me', ?)",
                             (self._stamp(),)).lastrowid

    def for_telegram(self, tg_id: int) -> int:
        row = self.db.one("SELECT id FROM users WHERE telegram_id=?", (tg_id,))
        return row["id"] if row else self.owner()

    def upsert_oidc(self, sub: str, name: str, email: str) -> int:
        row = self.db.one("SELECT id FROM users WHERE oidc_sub=?", (sub,))
        with self.db.tx() as c:
            if row:
                c.execute("UPDATE users SET name=?, email=? WHERE id=?", (name, email, row["id"]))
                return row["id"]
            # The first OIDC login claims the no-auth user, so existing tasks carry over.
            first = c.execute("SELECT id, oidc_sub FROM users ORDER BY id LIMIT 1").fetchone()
            if first and first["oidc_sub"] is None:
                c.execute("UPDATE users SET oidc_sub=?, name=?, email=? WHERE id=?",
                          (sub, name, email, first["id"]))
                return first["id"]
            return c.execute("INSERT INTO users(oidc_sub, name, email, created_at)"
                             " VALUES(?,?,?,?)", (sub, name, email, self._stamp())).lastrowid

    # ---------- sessions ----------
    def create_session(self, uid: int, days: int) -> str:
        token = secrets.token_urlsafe(32)
        now = self.now()
        with self.db.tx() as c:
            c.execute("DELETE FROM sessions WHERE expires_at < ?", (now.isoformat(),))
            c.execute("INSERT INTO sessions(token_hash, user_id, created_at, expires_at)"
                      " VALUES(?,?,?,?)", (_hash(token), uid, now.isoformat(timespec="seconds"),
                                           (now + timedelta(days=days)).isoformat(timespec="seconds")))
        return token

    def session_user(self, token: str) -> int | None:
        row = self.db.one("SELECT user_id, expires_at FROM sessions WHERE token_hash=?",
                          (_hash(token),))
        if not row or row["expires_at"] < self.now().isoformat():
            return None
        return row["user_id"]

    def end_session(self, token: str) -> None:
        with self.db.tx() as c:
            c.execute("DELETE FROM sessions WHERE token_hash=?", (_hash(token),))

    # ---------- device tokens ----------
    def create_token(self, uid: int, name: str) -> dict:
        name = (name or "").strip() or "device"
        token = "tl_" + secrets.token_urlsafe(24)
        with self.db.tx() as c:
            tid = c.execute("INSERT INTO api_tokens(user_id, name, token_hash, created_at)"
                            " VALUES(?,?,?,?)", (uid, name, _hash(token), self._stamp())).lastrowid
        return {"id": tid, "name": name, "token": token}

    def list_tokens(self, uid: int) -> list[dict]:
        return [dict(r) for r in self.db.query(
            "SELECT id, name, created_at, last_used_at FROM api_tokens WHERE user_id=?"
            " ORDER BY id", (uid,))]

    def delete_token(self, uid: int, token_id: int) -> None:
        with self.db.tx() as c:
            c.execute("DELETE FROM api_tokens WHERE id=? AND user_id=?", (token_id, uid))

    def token_user(self, token: str) -> int | None:
        row = self.db.one("SELECT id, user_id, last_used_at FROM api_tokens WHERE token_hash=?",
                          (_hash(token),))
        if not row:
            return None
        stamp = self._stamp()
        if (row["last_used_at"] or "")[:13] != stamp[:13]:  # update at most hourly
            with self.db.tx() as c:
                c.execute("UPDATE api_tokens SET last_used_at=? WHERE id=?", (stamp, row["id"]))
        return row["user_id"]


    # ---------- Telegram account linking ----------
    def telegram_code(self, uid: int) -> str:
        code = f"{secrets.randbelow(1_000_000):06d}"
        self.db.set_meta(f"tglink:{code}", f"{uid}|{(self.now() + timedelta(minutes=15)).isoformat()}")
        return code

    def telegram_link(self, code: str, tg_id: int) -> int:
        raw = self.db.get_meta(f"tglink:{code.strip()}")
        if not raw:
            raise ValueError("that code is wrong or expired")
        uid, exp = raw.split("|")
        if exp < self.now().isoformat():
            raise ValueError("that code is wrong or expired")
        with self.db.tx() as c:
            c.execute("UPDATE users SET telegram_id=NULL WHERE telegram_id=?", (tg_id,))
            c.execute("UPDATE users SET telegram_id=? WHERE id=?", (tg_id, int(uid)))
            c.execute("DELETE FROM meta WHERE key=?", (f"tglink:{code.strip()}",))
        return int(uid)

    # ---------- pairing (watch) ----------
    def pair_start(self) -> dict:
        """The watch asks for a code to show; you type it into the web app."""
        now = self.now()
        with self.db.tx() as c:
            c.execute("DELETE FROM pairings WHERE expires_at < ?", (now.isoformat(),))
            for _ in range(20):
                code = f"{secrets.randbelow(1_000_000):06d}"
                if not c.execute("SELECT 1 FROM pairings WHERE code=?", (code,)).fetchone():
                    break
            secret = secrets.token_urlsafe(24)
            c.execute("INSERT INTO pairings(code, secret, expires_at) VALUES(?,?,?)",
                      (code, _hash(secret), (now + timedelta(minutes=10)).isoformat(timespec="seconds")))
        return {"code": code, "secret": secret, "expires_in": 600}

    def pair_confirm(self, uid: int, code: str, name: str = "watch") -> None:
        code = (code or "").strip().replace(" ", "")
        row = self.db.one("SELECT * FROM pairings WHERE code=? AND expires_at>=?",
                          (code, self.now().isoformat()))
        if not row or row["user_id"]:
            raise ValueError("that code is wrong or expired")
        token = self.create_token(uid, name)["token"]
        with self.db.tx() as c:
            c.execute("UPDATE pairings SET user_id=?, token=? WHERE code=?", (uid, token, code))

    def pair_poll(self, code: str, secret: str) -> str | None:
        """Returns the token once confirmed (and forgets it). Raises if unknown/expired."""
        row = self.db.one("SELECT * FROM pairings WHERE code=? AND secret=? AND expires_at>=?",
                          (code, _hash(secret or ""), self.now().isoformat()))
        if not row:
            raise ValueError("pairing expired, start again")
        if not row["token"]:
            return None
        with self.db.tx() as c:
            c.execute("DELETE FROM pairings WHERE code=?", (code,))
        return row["token"]


class OIDC:
    """Authorization code flow with PKCE. Identity comes from the ID token returned by
    the token endpoint over TLS, plus the userinfo endpoint."""

    def __init__(self, s: Settings):
        self.s = s
        self._discovery: dict | None = None
        self._pending: dict[str, dict] = {}

    @property
    def redirect_uri(self) -> str:
        return f"{self.s.base_url}/auth/callback"

    async def discovery(self) -> dict:
        if self._discovery is None:
            async with httpx.AsyncClient(timeout=10) as c:
                r = await c.get(f"{self.s.oidc_issuer}/.well-known/openid-configuration")
                r.raise_for_status()
                self._discovery = r.json()
        return self._discovery

    async def login_url(self, next_url: str = "/") -> str:
        d = await self.discovery()
        now = time.time()
        self._pending = {k: v for k, v in self._pending.items() if v["exp"] > now}
        state, nonce = secrets.token_urlsafe(24), secrets.token_urlsafe(24)
        verifier = secrets.token_urlsafe(48)
        challenge = base64.urlsafe_b64encode(
            hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
        if not next_url.startswith("/") or next_url.startswith("//"):
            next_url = "/"
        self._pending[state] = {"verifier": verifier, "nonce": nonce, "next": next_url,
                                "exp": now + 600}
        return d["authorization_endpoint"] + "?" + urlencode({
            "response_type": "code", "client_id": self.s.oidc_client_id,
            "redirect_uri": self.redirect_uri, "scope": self.s.oidc_scopes,
            "state": state, "nonce": nonce,
            "code_challenge": challenge, "code_challenge_method": "S256"})

    async def callback(self, code: str, state: str) -> tuple[dict, str]:
        """Returns (claims, next_url). Raises AuthError if anything is off."""
        pending = self._pending.pop(state, None)
        if not pending or pending["exp"] < time.time():
            raise AuthError("login expired, please try again")
        d = await self.discovery()
        async with httpx.AsyncClient(timeout=15) as c:
            r = await c.post(d["token_endpoint"], data={
                "grant_type": "authorization_code", "code": code,
                "redirect_uri": self.redirect_uri, "client_id": self.s.oidc_client_id,
                "client_secret": self.s.oidc_client_secret,
                "code_verifier": pending["verifier"]})
            if r.status_code != 200:
                raise AuthError(f"token exchange failed ({r.status_code})")
            tok = r.json()
            claims = self._id_claims(tok.get("id_token", ""), pending["nonce"])
            if d.get("userinfo_endpoint") and tok.get("access_token"):
                u = await c.get(d["userinfo_endpoint"],
                                headers={"Authorization": f"Bearer {tok['access_token']}"})
                if u.status_code == 200:
                    info = u.json()
                    if info.get("sub") == claims["sub"]:
                        claims = {**claims, **info}
        allowed = self.s.oidc_allowed_groups
        if allowed and not set(allowed) & set(claims.get("groups") or []):
            raise AuthError("your account isn't in an allowed group")
        return claims, pending["next"]

    def _id_claims(self, id_token: str, nonce: str) -> dict:
        try:
            payload = id_token.split(".")[1]
            claims = json.loads(base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)))
        except (IndexError, ValueError) as e:
            raise AuthError("no valid ID token") from e
        aud = claims.get("aud")
        auds = aud if isinstance(aud, list) else [aud]
        if (claims.get("iss", "").rstrip("/") != self.s.oidc_issuer
                or self.s.oidc_client_id not in auds
                or claims.get("nonce") != nonce
                or claims.get("exp", 0) < time.time()
                or not claims.get("sub")):
            raise AuthError("ID token didn't match this login")
        return claims

    async def logout_url(self) -> str | None:
        try:
            return (await self.discovery()).get("end_session_endpoint")
        except httpx.HTTPError:
            return None


def display_name(claims: dict) -> str:
    return claims.get("name") or claims.get("preferred_username") or claims.get("email") or "Me"
