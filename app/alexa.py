"""Alexa custom skill endpoint.

"Alexa, tell Ticklight to add buy milk"            → capture (AI-sorted)
"Alexa, ask Ticklight what's next"                 → top of Focus
"Alexa, tell Ticklight I finished the dishes"      → tick the closest-matching task
"Alexa, tell Ticklight I took my meds"             → meds check-in
"Alexa, tell Ticklight to start a 25 minute focus" → focus session

Every request is verified the way Amazon requires: certificate URL, certificate chain,
SHA-256 signature over the raw body, and a timestamp no older than 150 seconds.
"""

from __future__ import annotations

import base64
import difflib
import json
import logging
import time
from datetime import datetime
from urllib.parse import urlparse

import certifi
import httpx
from cryptography import x509
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import padding
from cryptography.x509.verification import PolicyBuilder, Store

log = logging.getLogger("ticklight.alexa")

CERT_HOST = "s3.amazonaws.com"
CERT_SAN = "echo-api.amazon.com"
MAX_SKEW = 150


class AlexaError(Exception):
    pass


# ---------- request verification ----------
def check_cert_url(url: str) -> None:
    u = urlparse(url or "")
    if (u.scheme.lower() != "https" or (u.hostname or "").lower() != CERT_HOST
            or not u.path.startswith("/echo.api/") or (u.port not in (None, 443))):
        raise AlexaError("bad certificate URL")
    if "/../" in u.path or u.path.endswith("/.."):
        raise AlexaError("bad certificate URL")


def check_timestamp(body: dict, now: float | None = None) -> None:
    ts = (body.get("request") or {}).get("timestamp", "")
    try:
        t = datetime.fromisoformat(ts.replace("Z", "+00:00")).timestamp()
    except ValueError as e:
        raise AlexaError("bad timestamp") from e
    if abs((now or time.time()) - t) > MAX_SKEW:
        raise AlexaError("request too old")


def check_signature(leaf: x509.Certificate, raw: bytes, signature_b64: str) -> None:
    try:
        leaf.public_key().verify(base64.b64decode(signature_b64), raw, padding.PKCS1v15(), hashes.SHA256())
    except Exception as e:
        raise AlexaError("bad signature") from e


class CertCache:
    def __init__(self):
        self._certs: dict[str, tuple[float, list[x509.Certificate]]] = {}
        self._store = None

    def _roots(self) -> Store:
        if self._store is None:
            self._store = Store(x509.load_pem_x509_certificates(open(certifi.where(), "rb").read()))
        return self._store

    async def chain(self, url: str) -> list[x509.Certificate]:
        hit = self._certs.get(url)
        if hit and hit[0] > time.time():
            return hit[1]
        async with httpx.AsyncClient(timeout=10) as c:
            r = await c.get(url)
            r.raise_for_status()
        certs = x509.load_pem_x509_certificates(r.content)
        if not certs:
            raise AlexaError("empty certificate chain")
        leaf, intermediates = certs[0], certs[1:]
        verifier = PolicyBuilder().store(self._roots()).build_server_verifier(x509.DNSName(CERT_SAN))
        try:
            verifier.verify(leaf, intermediates)  # checks dates, chain to a trusted root, and the SAN
        except Exception as e:
            raise AlexaError(f"certificate didn't verify: {e}") from e
        expires = leaf.not_valid_after_utc.timestamp()
        self._certs[url] = (min(expires, time.time() + 6 * 3600), certs)
        return certs


# ---------- skill ----------
def speak(text: str, end: bool = True, reprompt: str | None = None, card: str | None = None) -> dict:
    resp = {"outputSpeech": {"type": "PlainText", "text": text}, "shouldEndSession": end}
    if reprompt:
        resp["reprompt"] = {"outputSpeech": {"type": "PlainText", "text": reprompt}}
    if card:
        resp["card"] = {"type": "Simple", "title": "Ticklight", "content": card}
    return {"version": "1.0", "response": resp}


def slot(intent: dict, name: str) -> str:
    return ((intent.get("slots") or {}).get(name) or {}).get("value") or ""


def best_match(spoken: str, tasks: list[dict]) -> dict | None:
    spoken = spoken.lower().strip()
    if not spoken or not tasks:
        return None
    scored = []
    for t in tasks:
        title = t["title"].lower()
        score = difflib.SequenceMatcher(None, spoken, title).ratio()
        if spoken in title or title in spoken:
            score += 0.4
        scored.append((score, t))
    score, t = max(scored, key=lambda x: x[0])
    return t if score >= 0.55 else None


class Alexa:
    def __init__(self, settings, services):
        self.s = settings
        self.x = services  # object with tasks, prefs, capture, sessions, coach, users
        self.certs = CertCache()

    @property
    def enabled(self) -> bool:
        return bool(self.s.alexa_skill_id)

    def user_for(self, body: dict) -> int:
        amazon_id = ((body.get("session") or {}).get("user") or {}).get("userId") or \
            (((body.get("context") or {}).get("System") or {}).get("user") or {}).get("userId", "")
        if amazon_id and not self.s.alexa_users:
            log.info("Alexa request from %s (set ALEXA_USERS to map it to someone else)", amazon_id)
        for pair in self.s.alexa_users:
            aid, _, uid = pair.rpartition(":")
            if aid == amazon_id and uid.isdigit():
                return int(uid)
        return self.x.users.owner()

    async def verify(self, raw: bytes, headers) -> dict:
        try:
            body = json.loads(raw)
        except ValueError as e:
            raise AlexaError("not JSON") from e
        app_id = ((body.get("session") or {}).get("application") or {}).get("applicationId") or \
            (((body.get("context") or {}).get("System") or {}).get("application") or {}).get("applicationId")
        if app_id != self.s.alexa_skill_id:
            raise AlexaError("wrong skill id")
        if self.s.alexa_verify:
            url = headers.get("signaturecertchainurl", "")
            check_cert_url(url)
            certs = await self.certs.chain(url)
            check_signature(certs[0], raw, headers.get("signature-256", ""))
            check_timestamp(body)
        return body

    async def handle(self, body: dict) -> dict:
        req = body.get("request") or {}
        kind = req.get("type")
        uid = self.user_for(body)
        if kind == "LaunchRequest":
            return speak("Ticklight here. Say add, then your task. Or ask what's next.", end=False,
                         reprompt="For example: add call the dentist tomorrow.")
        if kind == "SessionEndedRequest":
            return {"version": "1.0", "response": {}}
        if kind != "IntentRequest":
            return speak("Sorry, I didn't get that.")
        intent = req.get("intent") or {}
        name = intent.get("name", "")
        try:
            return await self._intent(uid, name, intent)
        except Exception:
            log.exception("alexa intent %s failed", name)
            return speak("Something went wrong on Ticklight's side. Try again in a moment.")

    async def _intent(self, uid: int, name: str, intent: dict) -> dict:
        x = self.x
        if name == "CaptureIntent":
            text = slot(intent, "text")
            if not text:
                return speak("What should I add?", end=False, reprompt="Say add, then the task.")
            r = await x.capture.run(uid, text, "alexa")
            titles = [t["title"] for t in r["tasks"]]
            if len(titles) == 1:
                return speak(f"Added {titles[0]}.", card=f"➕ {titles[0]}")
            return speak(f"Added {len(titles)} things: {', '.join(titles[:4])}.", card="\n".join(f"➕ {t}" for t in titles))
        if name == "NextIntent":
            top = x.tasks.focus(uid, 3)
            if not top:
                return speak("Nothing needs you right now. Nice.")
            first = top[0]
            line = f"Next up: {first['title']}."
            if first["next_step"]:
                line += f" Start with: {first['next_step']['title']}."
            if len(top) > 1:
                line += " After that: " + " and ".join(t["title"] for t in top[1:]) + "."
            return speak(line, card="\n".join(f"• {t['title']}" for t in top))
        if name == "DoneIntent":
            spoken = slot(intent, "task")
            t = best_match(spoken, x.tasks.open_tasks(uid))
            if not t:
                return speak(f"I couldn't find {spoken or 'that'} on your list.")
            before = x.tasks.get(uid, t["id"])
            done = x.tasks.complete(uid, t["id"])
            r = done.get("reward") or {}
            line = x.coach.react(uid, before, done)
            extra = f" {line['text']}" if line else ""
            return speak(f"Ticked off {t['title']}. Plus {r.get('xp', 0)} XP.{extra}", card=f"✅ {t['title']}")
        if name == "MedsIntent":
            nudges = [n for n in x.prefs.get(uid)["nudges"] if n["kind"] == "meds"]
            if not nudges:
                x.tasks.checkin(uid, "meds", "meds")
                return speak("Noted. Meds taken.")
            n = nudges[0]
            x.tasks.checkin(uid, n["id"], "meds")
            return speak(f"Noted, {n['label'].lower()} taken. I'll stop reminding you.")
        if name == "FocusIntent":
            minutes = slot(intent, "minutes")
            s = await x.sessions.start(uid, int(minutes) if minutes.isdigit() else None,
                                       (x.tasks.focus(uid, 1) or [{}])[0].get("id"))
            what = f" on {s['task']['title']}" if s["task"] else ""
            return speak(f"Starting {s['minutes']} minutes{what}. {s['coach']['text']}")
        if name == "AMAZON.HelpIntent":
            return speak("You can say: add buy milk tomorrow. What's next. I finished the dishes. "
                         "I took my meds. Or start a 25 minute focus.", end=False, reprompt="What would you like?")
        if name in ("AMAZON.StopIntent", "AMAZON.CancelIntent", "AMAZON.NavigateHomeIntent"):
            return speak("Okay.")
        return speak("Sorry, I didn't catch that. Try: add, then your task.", end=False,
                     reprompt="Say add, then your task.")
