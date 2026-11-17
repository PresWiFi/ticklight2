from __future__ import annotations

import asyncio
import hashlib
import logging
import os
from contextlib import asynccontextmanager
from datetime import date

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import actions, parser
from .ai import AI, AIUnavailable
from .alexa import Alexa, AlexaError
from .auth import COOKIE, OIDC, AuthError, Users, display_name
from .capture import Capture
from .coach import PERSONAS, Coach
from .game import Game
from .people import REACTIONS, People
from .config import settings
from .db import DB
from .notify import PushChannel, Scheduler, pending
from .prefs import Prefs
from .push import Push
from .sessions import Sessions
from .tasks import NotFound, Tasks
from .voice import Voice, VoiceUnavailable

logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"),
                    format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logging.getLogger("httpx").setLevel(logging.WARNING)
log = logging.getLogger("ticklight")

STATIC = os.path.join(os.path.dirname(__file__), "static")

db = DB(settings.db_path)
tasks = Tasks(db, settings.tz, energy_schedule=settings.energy_schedule,
              stale_overdue_days=settings.stale_overdue_days,
              stale_undated_days=settings.stale_undated_days)
users = Users(db, tasks.now)
ai = AI(settings)
prefs = Prefs(db, settings.summary_time)
capture = Capture(tasks, ai, prefs)
voice = Voice(settings)
push = Push(db, settings.vapid_subject or settings.base_url or "mailto:ticklight@localhost", tasks.now)
coach = Coach(db, tasks, prefs, ai)
sessions = Sessions(db, tasks, prefs, coach)
game = Game(db, tasks)
people = People(db, tasks, prefs, users)
alexa = Alexa(settings, __import__("types").SimpleNamespace(
    tasks=tasks, prefs=prefs, capture=capture, sessions=sessions, coach=coach, users=users))
ctx = actions.Ctx(tasks, prefs, sessions, coach)
scheduler = Scheduler(tasks, prefs, sessions, people)
scheduler.channels.append(PushChannel(push))
oidc = OIDC(settings) if settings.oidc_enabled else None
bot = None
_background: set[asyncio.Task] = set()


def spawn(coro) -> None:
    t = asyncio.create_task(coro)
    _background.add(t)
    t.add_done_callback(_background.discard)


async def process_capture(uid: int, cid: int) -> None:
    try:
        await capture.process(uid, cid)
    except Exception:
        pass  # logged and marked failed in Capture.process


async def start_bot_with_retry():
    global bot
    from telegram.error import InvalidToken

    from .bot import Bot
    delay = 5
    while True:
        candidate = Bot(settings, tasks, users, capture, ai, prefs, voice, ctx)
        try:
            await candidate.start()
            bot = candidate
            scheduler.channels.append(bot)
            return
        except InvalidToken:
            log.error("Telegram rejected TELEGRAM_BOT_TOKEN; bot disabled")
            return
        except Exception as e:
            log.warning("Telegram bot failed to start (%s); retrying in %ss", e, delay)
            try:
                await candidate.stop()
            except Exception:
                pass
            await asyncio.sleep(delay)
            delay = min(delay * 2, 300)


@asynccontextmanager
async def lifespan(app: FastAPI):
    if settings.oidc_enabled and not (settings.oidc_issuer and settings.oidc_client_id
                                      and settings.base_url):
        raise RuntimeError("AUTH_MODE=oidc needs OIDC_ISSUER, OIDC_CLIENT_ID and BASE_URL")
    users.owner()
    # Finish captures interrupted by a restart
    for row in db.query("SELECT id, user_id FROM captures WHERE status='pending'"):
        spawn(process_capture(row["user_id"], row["id"]))
    starter = None
    if settings.telegram_token:
        starter = asyncio.create_task(start_bot_with_retry())
    else:
        log.info("TELEGRAM_BOT_TOKEN not set; Telegram bot disabled")
    if not ai.configured:
        log.info("No AI provider configured; captures use the rule-based parser")
    scheduler.start()
    yield
    await scheduler.stop()
    if starter and not starter.done():
        starter.cancel()
    if bot:
        await bot.stop()


app = FastAPI(title="Ticklight", lifespan=lifespan)


def current_user(request: Request) -> int:
    header = request.headers.get("authorization", "")
    token = header.removeprefix("Bearer ").strip() or request.query_params.get("token", "")
    if token:
        uid = users.token_user(token)
        if uid is None:
            raise HTTPException(401, "invalid device token")
        return uid
    cookie = request.cookies.get(COOKIE)
    if cookie:
        uid = users.session_user(cookie)
        if uid is not None:
            return uid
    if not settings.oidc_enabled:
        return users.owner()
    raise HTTPException(401, "login required")


@app.exception_handler(NotFound)
async def not_found(_: Request, exc: NotFound):
    return JSONResponse({"detail": f"{exc} not found"}, status_code=404)


@app.exception_handler(ValueError)
async def bad_value(_: Request, exc: ValueError):
    return JSONResponse({"detail": str(exc)}, status_code=400)


@app.exception_handler(VoiceUnavailable)
async def voice_down(_: Request, exc: VoiceUnavailable):
    return JSONResponse({"detail": f"Voice unavailable: {exc}"}, status_code=503)


@app.exception_handler(AIUnavailable)
async def ai_down(_: Request, exc: AIUnavailable):
    return JSONResponse({"detail": f"AI unavailable: {exc}"}, status_code=503)


# ---------- auth ----------
@app.get("/auth/login")
async def login(next: str = "/"):
    if not oidc:
        return RedirectResponse("/")
    return RedirectResponse(await oidc.login_url(next))


@app.get("/auth/callback")
async def callback(code: str = "", state: str = "", error: str = ""):
    if not oidc:
        return RedirectResponse("/")
    if error or not code:
        return JSONResponse({"detail": f"login failed: {error or 'no code'}"}, status_code=400)
    try:
        claims, next_url = await oidc.callback(code, state)
    except AuthError as e:
        return JSONResponse({"detail": str(e)}, status_code=403)
    uid = users.upsert_oidc(claims["sub"], display_name(claims), claims.get("email", ""))
    resp = RedirectResponse(next_url)
    resp.set_cookie(COOKIE, users.create_session(uid, settings.session_days),
                    max_age=settings.session_days * 86400, httponly=True, samesite="lax",
                    secure=settings.base_url.startswith("https://"))
    return resp


@app.get("/auth/logout")
async def logout(request: Request):
    if cookie := request.cookies.get(COOKIE):
        users.end_session(cookie)
    target = (await oidc.logout_url() if oidc else None) or "/"
    resp = RedirectResponse(target)
    resp.delete_cookie(COOKIE)
    return resp


# ---------- API ----------
User = Depends(current_user)


class TaskIn(BaseModel):
    title: str | None = None
    notes: str | None = None
    priority: int | None = None
    due_date: date | None = None
    due_time: str | None = None
    recurrence: dict | None = None
    area: str | None = None
    energy: int | str | None = None
    minutes: int | None = None
    steps: list[str] | None = None
    shared: bool | None = None


class TextIn(BaseModel):
    text: str


@app.get("/api/health")
def health():
    return {"ok": True, "telegram": bool(bot), "ai": ai.configured, "version": tasks.version}


@app.get("/api/me")
def me(uid: int = User):
    return {"user": users.get(uid), "auth_mode": settings.auth_mode, "ai": ai.configured,
            "voice": voice.configured, "timezone": settings.tz_name,
            "telegram": bool(bot), "push_key": push.public_key, "push_devices": push.count(uid)}


class NameIn(BaseModel):
    name: str


@app.patch("/api/me")
def rename_me(body: NameIn, uid: int = User):
    """Your display name (people see it). With OIDC it's refreshed from the login provider."""
    return users.rename(uid, body.name)


@app.get("/api/version")
def version(uid: int = User):
    return {"version": tasks.version, "today": tasks.today().isoformat()}


@app.get("/api/board")
def board(uid: int = User):
    b = tasks.board(uid, settings.upcoming_days)
    b.update(pending(tasks, prefs, uid))
    coach.greet(uid)
    b["coach"] = {"persona": coach.persona(uid), "latest": coach.latest(uid, 1)}
    b["session"] = sessions.active(uid)
    b["focus_minutes_today"] = sessions.minutes_today(uid)
    game.rollover(uid)
    game.check_badges(uid)
    b["game"] = game.summary(uid)
    b["new_badges"] = game.unseen_badges(uid)
    b["people"] = people.summary(uid)
    names = {p["id"]: p["name"] for p in b["people"]}
    for key in ("today", "upcoming", "later", "anytime", "focus", "pinned"):
        for t in b.get(key, []):
            if t["user_id"] != uid:
                t["owner_name"] = names.get(t["user_id"], "Partner")
            if t.get("from_user") and t["from_user"] != uid:
                t["from_name"] = names.get(t["from_user"], "Partner")
    b["pending_captures"] = db.one(
        "SELECT COUNT(*) FROM captures WHERE user_id=? AND status='pending'", (uid,))[0]
    return b


@app.get("/api/focus")
def focus(n: int = 3, uid: int = User):
    return {"energy": tasks.energy(uid), "tasks": tasks.focus(uid, max(1, min(10, n)))}


class EnergyIn(BaseModel):
    level: int | str | None


@app.post("/api/energy")
def set_energy(body: EnergyIn, uid: int = User):
    return tasks.set_energy(uid, body.level)


@app.post("/api/capture", status_code=202)
async def capture_text(request: Request, wait: bool = False, uid: int = User):
    """Capture anything. JSON {"text": ...} or a plain-text body (handy for watch and
    phone shortcuts). Sorted in the background unless ?wait=1."""
    if request.headers.get("content-type", "").startswith("application/json"):
        text = (await request.json()).get("text", "")
    else:
        text = (await request.body()).decode("utf-8", "replace")
    source = request.query_params.get("source", "web")[:20]
    cid = capture.record(uid, text, source)
    if wait:
        return await capture.process(uid, cid)
    spawn(process_capture(uid, cid))
    return {"capture_id": cid, "status": "pending"}


@app.post("/api/capture/voice", status_code=202)
async def capture_voice(request: Request, uid: int = User):
    """Raw audio body (webm/ogg/m4a/wav). Transcribed, then captured like text."""
    audio = await request.body()
    if not audio:
        raise ValueError("no audio")
    mime = request.headers.get("content-type", "audio/webm").split(";")[0]
    ext = {"audio/ogg": "ogg", "audio/mp4": "m4a", "audio/mpeg": "mp3", "audio/wav": "wav",
           "audio/x-wav": "wav"}.get(mime, "webm")
    text = await voice.transcribe(audio, f"voice.{ext}", mime)
    if not text:
        raise ValueError("couldn't hear anything")
    result = await capture.run(uid, text, "voice")
    result["heard"] = text
    return result


@app.get("/api/captures")
def captures(uid: int = User):
    return capture.recent(uid)


@app.post("/api/captures/{cid}/undo")
def undo_capture(cid: int, uid: int = User):
    return {"deleted": capture.undo(uid, cid)}


@app.post("/api/quick", status_code=201)
def quick_add(body: TextIn, uid: int = User):
    return tasks.quick_add(uid, body.text)


@app.post("/api/parse")
def parse_preview(body: TextIn, uid: int = User):
    p = parser.parse(body.text, tasks.today())
    return {"title": p.title, "summary": p.summary()}


@app.get("/api/tasks")
def list_tasks(status: str = "open", uid: int = User):
    if status not in ("open", "someday"):
        raise ValueError("status must be open or someday")
    return tasks.tasks_with_status(uid, status)


@app.post("/api/tasks", status_code=201)
def create_task(body: TaskIn, uid: int = User):
    return tasks.create(uid, body.title or "", body.notes or "",
                        1 if body.priority is None else body.priority,
                        body.due_date, body.due_time, body.recurrence,
                        body.area, body.energy, body.minutes, body.steps, shared=bool(body.shared))


@app.get("/api/tasks/{task_id}")
def get_task(task_id: int, uid: int = User):
    t = tasks.get(uid, task_id)
    t["history"] = tasks.history(uid, task_id)
    return t


@app.patch("/api/tasks/{task_id}")
def update_task(task_id: int, body: TaskIn, uid: int = User):
    return tasks.update(uid, task_id, body.model_dump(exclude_unset=True, exclude={"steps"}))


@app.delete("/api/tasks/{task_id}")
def delete_task(task_id: int, uid: int = User):
    return tasks.delete(uid, task_id)


@app.post("/api/tasks/{task_id}/breakdown")
async def breakdown(task_id: int, uid: int = User):
    t = tasks.get(uid, task_id)
    done = [s["title"] for s in t["subtasks"] if s["done"]]
    steps = await ai.breakdown(t["title"], t["notes"], done)
    return tasks.replace_steps(uid, task_id, steps)


class SubtaskIn(BaseModel):
    title: str | None = None
    titles: list[str] | None = None
    done: bool | None = None


@app.post("/api/tasks/{task_id}/subtasks", status_code=201)
def add_subtasks(task_id: int, body: SubtaskIn, uid: int = User):
    return tasks.add_subtasks(uid, task_id, body.titles or [body.title or ""])


class ActionIn(BaseModel):
    hours: float | None = None
    when: str | None = None


@app.post("/api/tasks/{task_id}/{action}")
def task_action(task_id: int, action: str, body: ActionIn | None = None, uid: int = User):
    body = body or ActionIn()
    if action == "complete":
        before = tasks.get(uid, task_id)
        t = tasks.complete(uid, task_id)
        t["coach"] = coach.react(uid, before, t)
        return t
    fns = {
        "skip": lambda: tasks.skip(uid, task_id),
        "undo": lambda: tasks.undo_complete(uid, task_id),
        "restore": lambda: tasks.restore(uid, task_id),
        "someday": lambda: tasks.to_someday(uid, task_id),
        "bring-back": lambda: tasks.bring_back(uid, task_id, body.when),
        "not-now": lambda: tasks.not_now(uid, task_id, body.hours or 3),
        "pin": lambda: tasks.pin(uid, task_id, date.fromisoformat(body.when) if body.when else None),
        "unpin": lambda: tasks.unpin(uid, task_id),
        "move": lambda: tasks.move_to(uid, task_id, body.when or ""),
    }
    if action not in fns:
        raise HTTPException(404, "unknown action")
    return fns[action]()


@app.patch("/api/subtasks/{sub_id}")
def update_subtask(sub_id: int, body: SubtaskIn, uid: int = User):
    return tasks.update_subtask(uid, sub_id, body.title, body.done)


@app.delete("/api/subtasks/{sub_id}")
def delete_subtask(sub_id: int, uid: int = User):
    return tasks.delete_subtask(uid, sub_id)


# ---------- routines, nudges, hyperfocus ----------
@app.get("/api/prefs")
def get_prefs(uid: int = User):
    return prefs.get(uid)


@app.patch("/api/prefs")
def update_prefs(body: dict, uid: int = User):
    return prefs.update(uid, body)


class HyperfocusIn(BaseModel):
    minutes: int | None = None


@app.post("/api/hyperfocus")
def hyperfocus(body: HyperfocusIn, uid: int = User):
    minutes = body.minutes if body.minutes is not None else prefs.get(uid)["hyperfocus_minutes"]
    return {"until": tasks.set_hyperfocus(uid, minutes or None)}


class ActionStr(BaseModel):
    action: str


@app.post("/api/action")
async def notification_action(body: ActionStr, uid: int = User):
    """Buttons on phone notifications (the service worker posts here)."""
    return await actions.apply(ctx, uid, body.action)


@app.get("/api/today-checkins")
def today_checkins(uid: int = User):
    return tasks.checkins_on(uid, tasks.today())


@app.get("/api/routine/{name}")
def routine(name: str, uid: int = User):
    if name == "morning":
        return {"candidates": tasks.focus(uid, 8), "anytime": tasks.anytime(uid),
                "pinned": tasks.board(uid)["pinned"],
                "energy": tasks.energy(uid), "prefs": prefs.get(uid),
                "checkins": tasks.checkins_on(uid, tasks.today())}
    if name == "evening":
        return tasks.evening(uid)
    if name == "weekly":
        return tasks.weekly(uid)
    raise HTTPException(404, "unknown routine")


@app.post("/api/routine/{name}/done")
def routine_done(name: str, uid: int = User):
    if name not in ("morning", "evening", "weekly"):
        raise HTTPException(404, "unknown routine")
    out = tasks.checkin(uid, f"routine:{name}")
    out["coach"] = coach.say(uid, name)
    return out


# ---------- focus sessions & coach ----------
class SessionIn(BaseModel):
    minutes: int | None = None
    task_id: int | None = None
    intention: str = ""


@app.post("/api/sessions", status_code=201)
async def start_session(body: SessionIn, uid: int = User):
    return await sessions.start(uid, body.minutes, body.task_id, body.intention)


@app.get("/api/sessions/active")
def active_session(uid: int = User):
    return {"session": sessions.active(uid)}


class SessionAction(BaseModel):
    mood: str | None = None
    minutes: int | None = None
    complete_task: bool = False


@app.post("/api/sessions/{sid}/{action}")
async def session_action(sid: int, action: str, body: SessionAction | None = None, uid: int = User):
    body = body or SessionAction()
    if action == "checkin":
        return await sessions.checkin(uid, sid, body.mood or "")
    if action == "extend":
        return sessions.extend(uid, sid, body.minutes or 5)
    if action == "end":
        return sessions.end(uid, sid, "done" if body.complete_task else "stopped", body.complete_task)
    if action == "break":
        return sessions.start_break(uid, sid, body.minutes)
    if action == "end-break":
        return sessions.end_break(uid, sid)
    raise HTTPException(404, "unknown action")


class SayIn(BaseModel):
    event: str


@app.post("/api/coach/say")
def coach_say(body: SayIn, uid: int = User):
    """Let the UI ask for a line at moments only it knows about (e.g. a check-in is due)."""
    if body.event not in ("checkin_ask", "break_end"):
        raise ValueError("unsupported event")
    s = sessions.active(uid)
    task = s["task"] if s else None
    return coach.say(uid, body.event, task=task["title"] if task else None,
                     step=task["next_step"]["title"] if task and task["next_step"] else None)


@app.get("/api/coach")
def coach_info(uid: int = User):
    return {"persona": coach.persona(uid), "latest": coach.latest(uid, 10),
            "personas": [{"id": k, "name": v["name"], "emoji": v["emoji"], "color": v["color"],
                          "style": v["style"], "sample": v["lines"]["done"][0].format(task="Laundry", xp=20)}
                         for k, v in PERSONAS.items()]}


# ---------- game ----------
@app.get("/api/game")
def game_full(uid: int = User):
    return game.full(uid)


class ItemIn(BaseModel):
    item: str | None = None
    slot: str | None = None


@app.post("/api/game/buy")
def game_buy(body: ItemIn, uid: int = User):
    return game.buy(uid, body.item or "")


@app.post("/api/game/equip")
def game_equip(body: ItemIn, uid: int = User):
    return game.equip(uid, body.item, body.slot)


@app.post("/api/game/theme")
def game_theme(body: ItemIn, uid: int = User):
    return game.set_theme(uid, body.item or "auto")


class SlotIn(BaseModel):
    slot: int


@app.post("/api/game/claim")
def game_claim(body: SlotIn, uid: int = User):
    out = game.claim(uid, body.slot)
    game.check_badges(uid)
    return out


@app.post("/api/game/seen")
def game_seen(uid: int = User):
    game.mark_seen(uid)
    return {"ok": True}


# ---------- people ----------
class InviteIn(BaseModel):
    name: str = ""


@app.post("/api/people/invite")
def people_invite(body: InviteIn, request: Request, uid: int = User):
    inv = people.invite(uid, body.name)
    base = settings.base_url or str(request.base_url).rstrip("/")
    return {**inv, "url": f"{base}/join?code={inv['code']}"}


@app.get("/api/people")
def people_list(uid: int = User):
    return {"people": people.summary(uid), "reactions": REACTIONS}


@app.delete("/api/people/{other}")
def people_unlink(other: int, uid: int = User):
    people.unlink(uid, other)
    return {"ok": True}


@app.get("/api/people/feed")
def people_feed(uid: int = User):
    return {"feed": people.feed(uid), "reactions": REACTIONS}


class ReactIn(BaseModel):
    completion_id: int
    emoji: str


@app.post("/api/people/react")
async def people_react(body: ReactIn, uid: int = User):
    r = people.react(uid, body.completion_id, body.emoji)
    if r["new"]:
        from .notify import Note
        me_ = users.get(uid) or {}
        await scheduler.send_now(r["to"], Note(
            f"react:{uid}:{body.completion_id}:{body.emoji}", tasks.now(),
            f"{body.emoji} {me_.get('name') or 'Your partner'} reacted to “{r['title']}”", "",
            [("💞 See wins", "url:/?people")]))
    return {"ok": True}


class SendIn(BaseModel):
    text: str


@app.post("/api/people/{other}/send")
async def people_send(other: int, body: SendIn, uid: int = User):
    """Send someone a task (or a brain dump of them). It's sorted like any capture."""
    if not people.is_linked(uid, other):
        raise HTTPException(404, "not one of your people")
    result = await capture.run(other, body.text, "partner", from_user=uid)
    me_ = users.get(uid) or {}
    titles = ", ".join(t["title"] for t in result["tasks"][:3])
    from .notify import Note
    await scheduler.send_now(other, Note(
        f"sent:{uid}:{result['capture_id']}", tasks.now(),
        f"💌 {me_.get('name') or 'Your partner'} sent you: {titles}", "",
        [("✅ Done", f"done:{result['tasks'][0]['id']}")] if len(result["tasks"]) == 1 else []))
    return {"sent": len(result["tasks"]), "titles": [t["title"] for t in result["tasks"]]}


JOIN_PAGE = """<!doctype html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Join Ticklight</title><link rel="stylesheet" href="/static/style.css"></head>
<body style="align-items:center;justify-content:center;padding:24px"><div class="col" style="max-width:420px;text-align:center">
<h1 style="font-size:1.4em">💞 {title}</h1><p class="muted">{body}</p>{action}</div></body></html>"""


def _page(title: str, body: str, action: str = "", status: int = 200) -> HTMLResponse:
    import html as h
    return HTMLResponse(JOIN_PAGE.format(title=h.escape(title), body=h.escape(body), action=action),
                        status_code=status)


@app.get("/join")
def join_page(request: Request, code: str = ""):
    inv = people.invite_info(code)
    if not inv:
        return _page("Invite expired", "Ask for a new link.", status=404)
    who = (users.get(inv["from_user"]) or {}).get("name") or "Someone"
    if settings.oidc_enabled:
        cookie = request.cookies.get(COOKIE)
        uid = users.session_user(cookie) if cookie else None
        if uid is None:
            return RedirectResponse(f"/auth/login?next=/join?code={code}")
        try:
            people.redeem(code, uid)
        except ValueError as e:
            return _page("Couldn't join", str(e), status=400)
        return RedirectResponse("/?people")
    return _page(f"Join {who} on Ticklight",
                 "You'll get your own tasks, and you can share chores, send each other tasks, and cheer each other's wins.",
                 f'<form method="post"><button class="primary" style="padding:12px 24px">Join as {__import__("html").escape(inv["name"] or "me")}</button></form>')


@app.post("/join")
def join_confirm(code: str = ""):
    """No-login mode: make the new person's account and log this browser in with a device token."""
    inv = people.invite_info(code)
    if not inv or settings.oidc_enabled:
        return _page("Invite expired", "Ask for a new link.", status=404)
    uid = people.new_member(inv["name"])
    people.redeem(code, uid)
    token = users.create_token(uid, "browser")["token"]
    return RedirectResponse(f"/?token={token}&people", status_code=303)


# ---------- Alexa ----------
@app.post("/alexa")
async def alexa_endpoint(request: Request):
    """Alexa custom skill endpoint. Authenticated by Amazon's request signature, not by login."""
    if not alexa.enabled:
        raise HTTPException(404, "Alexa not configured")
    raw = await request.body()
    try:
        body = await alexa.verify(raw, request.headers)
    except AlexaError as e:
        log.warning("rejected Alexa request: %s", e)
        raise HTTPException(400, "invalid Alexa request")
    return await alexa.handle(body)


# ---------- phone push ----------
class SubIn(BaseModel):
    subscription: dict
    label: str = ""


@app.post("/api/push/subscribe")
def push_subscribe(body: SubIn, uid: int = User):
    push.subscribe(uid, body.subscription, body.label)
    return {"ok": True, "devices": push.count(uid)}


class EndpointIn(BaseModel):
    endpoint: str


@app.post("/api/push/unsubscribe")
def push_unsubscribe(body: EndpointIn, uid: int = User):
    push.unsubscribe(uid, body.endpoint)
    return {"ok": True}


@app.post("/api/push/test")
async def push_test(uid: int = User):
    n = await push.send(uid, {"title": "👋 Ticklight", "body": "Notifications work.", "tag": "test",
                              "url": "/", "actions": []})
    return {"sent": n}


@app.post("/api/telegram/link-code")
def telegram_link_code(uid: int = User):
    return {"code": users.telegram_code(uid), "linked": bool((users.get(uid) or {}).get("telegram_id"))}


# ---------- watch pairing ----------
@app.post("/api/pair/start")
def pair_start():
    return users.pair_start()


class PairPoll(BaseModel):
    code: str
    secret: str


@app.post("/api/pair/poll")
def pair_poll(body: PairPoll):
    token = users.pair_poll(body.code, body.secret)
    return {"token": token} if token else {"pending": True}


class PairConfirm(BaseModel):
    code: str
    name: str = "watch"


@app.post("/api/pair/confirm")
def pair_confirm(body: PairConfirm, uid: int = User):
    users.pair_confirm(uid, body.code, body.name)
    return {"ok": True}


class TokenIn(BaseModel):
    name: str


@app.get("/api/tokens")
def list_tokens(uid: int = User):
    return users.list_tokens(uid)


@app.post("/api/tokens", status_code=201)
def create_token(body: TokenIn, uid: int = User):
    return users.create_token(uid, body.name)


@app.delete("/api/tokens/{token_id}")
def delete_token(token_id: int, uid: int = User):
    users.delete_token(uid, token_id)
    return {"ok": True}


def _versioned_index() -> str:
    """index.html with ?v=<content hash> on app.js/style.css, so CDN and browser caches
    pick up a new release immediately."""
    html = open(os.path.join(STATIC, "index.html"), encoding="utf-8").read()
    for name in ("app.js", "style.css"):
        with open(os.path.join(STATIC, name), "rb") as f:
            v = hashlib.sha256(f.read()).hexdigest()[:10]
        html = html.replace(f'"/static/{name}"', f'"/static/{name}?v={v}"')
    return html


INDEX_HTML = _versioned_index()


@app.get("/")
def index():
    return HTMLResponse(INDEX_HTML, headers={"Cache-Control": "no-cache"})


@app.get("/sw.js")
def service_worker():
    # Served from the root so it can control the whole app
    return FileResponse(os.path.join(STATIC, "sw.js"), media_type="text/javascript",
                        headers={"Cache-Control": "no-cache"})


@app.get("/manifest.webmanifest")
def manifest():
    return FileResponse(os.path.join(STATIC, "manifest.webmanifest"),
                        media_type="application/manifest+json")


app.mount("/static", StaticFiles(directory=STATIC), name="static")


def run():
    import uvicorn
    uvicorn.run("app.main:app", host=settings.host, port=settings.port,
                proxy_headers=True, forwarded_allow_ips="*",
                log_level=os.getenv("LOG_LEVEL", "info").lower())


if __name__ == "__main__":
    run()
