import random
from datetime import datetime
from zoneinfo import ZoneInfo

from app.ai import AI
from app.auth import Users
from app.bot import Bot
from app.capture import Capture
from app.config import Settings
from app.db import DB
from app.prefs import Prefs
from app.voice import Voice
from app.tasks import Tasks


def make_bot():
    clock = lambda: datetime(2026, 11, 2, 10, 0, tzinfo=ZoneInfo("UTC"))
    db = DB(":memory:")
    tasks = Tasks(db, ZoneInfo("UTC"), clock, rng=random.Random(1))
    users = Users(db, clock)
    s = Settings(telegram_users={42}, openrouter_key="", local_ai_url="")
    return (Bot(s, tasks, users, Capture(tasks, AI(s)), AI(s), Prefs(db), Voice(s)),
            tasks, users.owner())


def test_focus_view_and_actions():
    bot, tasks, uid = make_bot()
    t = tasks.quick_add(uid, "pay rent today !!")
    text, kb = bot._focus_view(uid)
    assert "pay rent" in text and "due today" in text
    assert kb.inline_keyboard[0][0].callback_data == f"done:{t['id']}:focus"
    assert "XP" in bot._apply(uid, "done", t["id"])
    assert "Undid" in bot._apply(uid, "undo", t["id"])
    msg = ""
    for _ in range(3):
        msg = bot._apply(uid, "notnow", t["id"])
    assert "break it into tiny steps" in msg
    assert bot._apply(uid, "done", 999) == "No such task."


def test_notification_buttons():
    import asyncio
    from datetime import datetime
    from types import SimpleNamespace

    from app.notify import Note

    bot, tasks, uid = make_bot()
    sent = []

    async def send_message(chat_id, text, **kw):
        sent.append((chat_id, text, kw["reply_markup"]))
    bot.app = SimpleNamespace(bot=SimpleNamespace(send_message=send_message))
    note = Note("k", datetime.now(), "☀️ Morning", "• pay <rent>",
                [("☀️ Plan", "url:/?routine=morning"), ("🔋", "energy:2")])
    p = bot.prefs.get(uid)
    asyncio.run(bot.send(uid, note, p))
    chat, text, kb = sent[0]
    assert chat == 42 and "&lt;rent&gt;" in text
    assert [b.callback_data for b in kb.inline_keyboard[0]] == ["act:energy:2"]  # no https BASE_URL: no link
    bot.s.base_url = "https://tl.example.com"
    asyncio.run(bot.send(uid, note, p))
    assert sent[1][2].inline_keyboard[0][0].url == "https://tl.example.com/?routine=morning"
    p["channels"]["telegram"] = False
    asyncio.run(bot.send(uid, note, p))
    assert len(sent) == 2
