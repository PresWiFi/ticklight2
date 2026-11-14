"""Per-user preferences: routines, body nudges, reminders, notification channels."""

from __future__ import annotations

import copy
import json
import re

from .db import DB

NUDGE_KINDS = ("meds", "water", "meal", "move", "custom")
# Kinds that get through hyperfocus mode
CRITICAL_KINDS = ("meds", "meal")
SOUNDS = ("off", "brown", "rain", "cafe", "hum")

DEFAULTS = {
    "morning": {"enabled": True, "time": "08:00"},
    "evening": {"enabled": True, "time": "21:00"},
    "weekly": {"enabled": True, "day": 6, "time": "18:00"},   # day: Mon=0 … Sun=6
    "reminders": {"lead_minutes": 15, "follow_up_minutes": 15, "max_follow_ups": 2},
    "hyperfocus_minutes": 90,
    "channels": {"telegram": True, "push": True},
    # persona: see coach.PERSONAS; rotate = a different one each week
    "coach": {"persona": "sunny", "rotate": False, "ai": True},
    # What your linked people see, and your evening digest of their wins
    "people": {"share_wins": True, "share_titles": True, "digest": True, "digest_time": "20:00", "share_home": False},
    "sessions": {"minutes": 25, "break_minutes": 5, "checkins": 2, "quiet": True, "sound": "off"},
    "nudges": [
        {"id": "meds", "kind": "meds", "label": "Meds", "enabled": False, "times": ["08:00"],
         "follow_up_minutes": 15, "max_follow_ups": 4},
        {"id": "water", "kind": "water", "label": "Water", "enabled": False,
         "every_minutes": 120, "from": "09:00", "to": "21:00"},
        {"id": "lunch", "kind": "meal", "label": "Lunch", "enabled": False, "times": ["12:30"],
         "follow_up_minutes": 20, "max_follow_ups": 1},
        {"id": "dinner", "kind": "meal", "label": "Dinner", "enabled": False, "times": ["18:30"],
         "follow_up_minutes": 20, "max_follow_ups": 1},
    ],
}

_TIME = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")


def _time(v, default: str) -> str:
    v = str(v or "").strip()
    if re.match(r"^\d:\d\d$", v):
        v = "0" + v
    return v if _TIME.match(v) else default


def _int(v, default: int, lo: int, hi: int) -> int:
    try:
        return max(lo, min(hi, int(v)))
    except (TypeError, ValueError):
        return default


def _routine(v: dict, d: dict) -> dict:
    out = {"enabled": bool(v.get("enabled", d["enabled"])), "time": _time(v.get("time"), d["time"])}
    if "day" in d:
        out["day"] = _int(v.get("day"), d["day"], 0, 6)
    return out


def _nudge(n: dict) -> dict | None:
    nid = re.sub(r"[^a-z0-9_-]", "", str(n.get("id") or "").lower())[:20]
    kind = n.get("kind") if n.get("kind") in NUDGE_KINDS else "custom"
    label = str(n.get("label") or "").strip()[:40]
    if not nid or not label:
        return None
    out = {"id": nid, "kind": kind, "label": label, "enabled": bool(n.get("enabled"))}
    if n.get("every_minutes"):
        out["every_minutes"] = _int(n["every_minutes"], 120, 15, 24 * 60)
        out["from"] = _time(n.get("from"), "09:00")
        out["to"] = _time(n.get("to"), "21:00")
    else:
        times = n.get("times") or []
        if isinstance(times, str):
            times = re.split(r"[,\s]+", times)
        out["times"] = sorted({_time(t, "") for t in times} - {""})[:12]
        out["follow_up_minutes"] = _int(n.get("follow_up_minutes"), 15, 5, 240)
        out["max_follow_ups"] = _int(n.get("max_follow_ups"), 0, 0, 10)
    return out


def clean(raw: dict, base: dict | None = None) -> dict:
    """Validate a (partial) prefs dict on top of `base` (defaults if None)."""
    out = copy.deepcopy(base or DEFAULTS)
    raw = raw or {}
    for key in ("morning", "evening", "weekly"):
        if isinstance(raw.get(key), dict):
            out[key] = _routine({**out[key], **raw[key]}, DEFAULTS[key])
    if isinstance(raw.get("reminders"), dict):
        r = {**out["reminders"], **raw["reminders"]}
        out["reminders"] = {"lead_minutes": _int(r.get("lead_minutes"), 15, 0, 240),
                            "follow_up_minutes": _int(r.get("follow_up_minutes"), 15, 5, 240),
                            "max_follow_ups": _int(r.get("max_follow_ups"), 2, 0, 10)}
    if "hyperfocus_minutes" in raw:
        out["hyperfocus_minutes"] = _int(raw["hyperfocus_minutes"], 90, 15, 480)
    if isinstance(raw.get("channels"), dict):
        out["channels"] = {k: bool(raw["channels"].get(k, out["channels"][k]))
                           for k in ("telegram", "push")}
    if isinstance(raw.get("coach"), dict):
        from .coach import PERSONAS
        c = {**out["coach"], **raw["coach"]}
        out["coach"] = {"persona": c["persona"] if c.get("persona") in PERSONAS else "sunny",
                        "rotate": bool(c.get("rotate")), "ai": bool(c.get("ai", True))}
    if isinstance(raw.get("sessions"), dict):
        x = {**out["sessions"], **raw["sessions"]}
        out["sessions"] = {"minutes": _int(x.get("minutes"), 25, 5, 180),
                           "break_minutes": _int(x.get("break_minutes"), 5, 1, 60),
                           "checkins": _int(x.get("checkins"), 2, 0, 6),
                           "quiet": bool(x.get("quiet", True)),
                           "sound": x.get("sound") if x.get("sound") in SOUNDS else "off"}
    if isinstance(raw.get("people"), dict):
        x = {**out["people"], **raw["people"]}
        out["people"] = {k: bool(x.get(k)) for k in ("share_wins", "share_titles", "digest", "share_home")}
        out["people"]["digest_time"] = _time(x.get("digest_time"), "20:00")
    if isinstance(raw.get("nudges"), list):
        seen, nudges = set(), []
        for n in raw["nudges"][:20]:
            n = _nudge(n) if isinstance(n, dict) else None
            if n and n["id"] not in seen:
                seen.add(n["id"])
                nudges.append(n)
        out["nudges"] = nudges
    return out


class Prefs:
    def __init__(self, db: DB, morning_default: str = "08:00"):
        self.db = db
        self.morning_default = _time(morning_default, "")

    def get(self, uid: int) -> dict:
        row = self.db.one("SELECT prefs FROM users WHERE id=?", (uid,))
        stored = json.loads(row["prefs"]) if row and row["prefs"] else {}
        base = copy.deepcopy(DEFAULTS)
        if self.morning_default:
            base["morning"]["time"] = self.morning_default
        elif self.morning_default == "":
            base["morning"]["enabled"] = False
        return clean(stored, base)

    def update(self, uid: int, changes: dict) -> dict:
        new = clean(changes, self.get(uid))
        with self.db.tx() as c:
            c.execute("UPDATE users SET prefs=? WHERE id=?", (json.dumps(new), uid))
        return new
