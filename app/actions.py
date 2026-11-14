"""Actions triggered from notification buttons (Telegram and phone push share these).

Format: "verb:arg", e.g. "done:12", "took:meds", "hyperfocus:off", "session:break:4".
"url:..." actions are links, handled by each channel.
"""

from __future__ import annotations

from dataclasses import dataclass

from .tasks import ENERGY_NAMES, NotFound


@dataclass
class Ctx:
    tasks: object
    prefs: object
    sessions: object = None
    coach: object = None


async def apply(ctx: Ctx, uid: int, action: str) -> dict:
    tasks, prefs = ctx.tasks, ctx.prefs
    verb, _, arg = action.partition(":")
    try:
        if verb == "done":
            before = tasks.get(uid, int(arg))
            t = tasks.complete(uid, int(arg))
            r = t.get("reward")
            line = ctx.coach.react(uid, before, t) if ctx.coach else None
            msg = f"✅ {t['title']}" + (f" · +{r['xp']} XP" if r else "")
            return {"ok": True, "message": msg + (f"\n{line['emoji']} {line['text']}" if line else ""),
                    "reward": r, "task_id": t["id"], "coach": line}
        if verb == "notnow":
            t = tasks.not_now(uid, int(arg))
            return {"ok": True, "message": f"💤 {t['title']} moved aside for 3 hours"}
        if verb == "took":
            nudge = next((n for n in prefs.get(uid)["nudges"] if n["id"] == arg), None)
            if not nudge:
                return {"ok": False, "message": "That reminder no longer exists"}
            c = tasks.checkin(uid, arg, nudge["kind"])
            return {"ok": True, "message": f"✅ {nudge['label']} · +{c['xp']} XP"}
        if verb == "later":
            return {"ok": True, "message": "👍 I'll check back soon"}
        if verb == "energy":
            e = tasks.set_energy(uid, int(arg))
            return {"ok": True, "message": f"Energy: {ENERGY_NAMES[e['level']]}"}
        if verb == "hyperfocus":
            if arg == "off":
                tasks.set_hyperfocus(uid, None)
                return {"ok": True, "message": "Hyperfocus off"}
            minutes = int(arg) if arg.isdigit() else prefs.get(uid)["hyperfocus_minutes"]
            tasks.set_hyperfocus(uid, minutes)
            return {"ok": True, "message": f"🎧 Hyperfocus for {minutes} min. Only meetings, meds, meals and deadlines get through."}
        if verb == "session" and ctx.sessions:
            what, _, sid = arg.partition(":")
            sid = int(sid)
            S = ctx.sessions
            if what == "done":
                s = S.end(uid, sid, "done", complete_task=True)
                return {"ok": True, "message": f"✅ Session and task done · +{s['xp'] + (s['reward'] or {}).get('xp', 0)} XP"}
            if what == "stop":
                s = S.end(uid, sid, "stopped")
                return {"ok": True, "message": f"⏹ {s['actual_minutes']} minutes · +{s['xp']} XP"}
            if what == "extend":
                S.extend(uid, sid, 5)
                return {"ok": True, "message": "➕ 5 more minutes"}
            if what == "break":
                s = S.start_break(uid, sid)
                return {"ok": True, "message": f"☕ {s['coach']['text']}"}
            if what == "again":
                old = S.get(uid, sid)
                s = await S.start(uid, old["minutes"], old["task_id"], old["intention"])
                return {"ok": True, "message": f"▶ {s['coach']['text']}"}
            if what == "rest":
                S.end_break(uid, sid)
                return {"ok": True, "message": "✋ Done for now. Nice work."}
    except NotFound:
        return {"ok": False, "message": "That task no longer exists"}
    except ValueError as e:
        return {"ok": False, "message": str(e)}
    return {"ok": False, "message": "Unknown action"}
