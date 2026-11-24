"""Telegram bot: capture anything, focus, energy, morning focus message, reminders."""

from __future__ import annotations

import html
import logging
import re
from datetime import date

from telegram import InlineKeyboardButton as Btn
from telegram import InlineKeyboardMarkup, Update
from telegram.constants import ParseMode
from telegram.ext import (Application, CallbackQueryHandler, CommandHandler, ContextTypes,
                          MessageHandler, filters)

from . import actions
from .ai import AI, AIUnavailable
from .auth import Users
from .capture import Capture
from .config import Settings
from .notify import Note
from .prefs import Prefs
from .tasks import ENERGY_NAMES, NotFound, Tasks
from .voice import Voice, VoiceUnavailable

log = logging.getLogger("ticklight.bot")

HELP = """<b>Ticklight</b>
Send me anything: one task, a messy brain dump, a list, or a voice note. I'll sort it into tasks.
Reply to a task message to add steps to it.

/focus – what to do next
/energy – set your energy (low / medium / high)
/today – everything for today
/someday – parked tasks
/wish headphones £80 – add to your wishlist (/wish on its own lists it)
/session 25 – focus session on your top task (/session 25 12 for task 12, /session stop)
/hyperfocus – only meetings, meds, meals and deadlines get through (/hyperfocus off)
/done 12 · /undo 12 · /del 12
/id – your Telegram user id
/link 123456 – connect this chat to your account (code in ⚙ → People)"""

ENERGY_ICON = {1: "🪫", 2: "🔋", 3: "⚡"}


def esc(s: str) -> str:
    return html.escape(s or "", quote=False)


def fmt_when(t: dict, today: date) -> str:
    bits = []
    if t["due_date"]:
        d = date.fromisoformat(t["due_date"])
        delta = (d - today).days
        bits.append("today" if delta <= 0 else "tomorrow" if delta == 1
                    else d.strftime("%a") if delta < 7 else d.strftime("%d %b"))
    if t["due_time"]:
        bits.append(t["due_time"])
    if t["recurrence_text"]:
        bits.append("🔁 " + t["recurrence_text"])
    if t.get("current_streak", 0) >= 2:
        bits.append(f"🔥{t['current_streak']}")
    if t.get("minutes"):
        bits.append(f"~{t['minutes']}m")
    return " · ".join(bits)


def fmt_task(t: dict, today: date) -> str:
    mark = "❗" if t["priority"] == 2 else "•"
    when = fmt_when(t, today)
    line = f"{mark} <b>{esc(t['title'])}</b>{'  <i>' + esc(when) + '</i>' if when else ''}  /{t['id']}"
    if t.get("next_step"):
        line += f"\n    ↳ {esc(t['next_step']['title'])}"
    return line


def fmt_reward(r: dict | None) -> str:
    if not r:
        return ""
    out = f"\n<b>+{r['xp']} XP</b>"
    if r["lines"]:
        out += " · " + " · ".join(esc(x) for x in r["lines"])
    if r["level_up"]:
        out += f"\n🎉 <b>Level {r['level']}!</b>"
    return out


def split_items(text: str) -> list[str]:
    items = []
    for line in text.splitlines():
        items += [x.strip(" -•*\t") for x in line.split(";")]
    return [x for x in items if x]


class Bot:
    def __init__(self, settings: Settings, tasks: Tasks, users: Users, capture: Capture, ai: AI,
                 prefs: Prefs, voice: Voice, ctx: actions.Ctx | None = None):
        self.s = settings
        self.tasks = tasks
        self.users = users
        self.capture = capture
        self.ai = ai
        self.prefs = prefs
        self.voice = voice
        self.ctx = ctx or actions.Ctx(tasks, prefs)
        self.app: Application | None = None

    # ---------- lifecycle ----------
    async def start(self) -> None:
        self.app = Application.builder().token(self.s.telegram_token).build()
        a = self.app
        a.add_handler(CommandHandler(["start", "help"], self.cmd_help))
        a.add_handler(CommandHandler("id", self.cmd_id))
        a.add_handler(CommandHandler("link", self.cmd_link))
        a.add_handler(CommandHandler(["focus", "next"], self.cmd_focus))
        a.add_handler(CommandHandler("energy", self.cmd_energy))
        a.add_handler(CommandHandler("today", self.cmd_today))
        a.add_handler(CommandHandler("someday", self.cmd_someday))
        a.add_handler(CommandHandler(["wish", "wishlist", "want"], self.cmd_wish))
        a.add_handler(CommandHandler(["hyperfocus", "hf"], self.cmd_hyperfocus))
        a.add_handler(CommandHandler(["session", "focusnow"], self.cmd_session))
        a.add_handler(CommandHandler(["done", "undo", "del", "delete"], self.cmd_action))
        a.add_handler(MessageHandler(filters.Regex(r"^/\d+(@\w+)?$"), self.cmd_show))
        a.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, self.on_text))
        a.add_handler(MessageHandler(filters.VOICE | filters.AUDIO, self.on_voice))
        a.add_handler(CallbackQueryHandler(self.on_button))
        await a.initialize()
        await a.bot.set_my_commands([
            ("focus", "What to do next"), ("energy", "Set your energy"),
            ("today", "Everything for today"), ("someday", "Parked tasks"),
            ("session", "Start a focus session: /session 25"),
            ("hyperfocus", "Only important pings for a while"), ("help", "Help"),
        ])
        await a.start()
        await a.updater.start_polling(drop_pending_updates=False)
        log.info("Telegram bot started")

    async def stop(self) -> None:
        if self.app:
            if self.app.updater and self.app.updater.running:
                await self.app.updater.stop()
            if self.app.running:
                await self.app.stop()
            await self.app.shutdown()

    def uid(self, update: Update) -> int | None:
        u = update.effective_user
        if not u or u.id not in self.s.telegram_users:
            return None
        return self.users.for_telegram(u.id)

    async def _deny(self, update: Update) -> None:
        uid = update.effective_user.id if update.effective_user else "?"
        log.warning("Ignoring message from unauthorised user %s", uid)
        if update.effective_message:
            await update.effective_message.reply_text(
                f"Not authorised. Your user id is {uid}.\n"
                "Add it to TELEGRAM_ALLOWED_USERS and restart the service.")

    # ---------- views ----------
    def _focus_view(self, uid: int) -> tuple[str, InlineKeyboardMarkup]:
        e = self.tasks.energy(uid)
        items = self.tasks.focus(uid)
        today = self.tasks.today()
        head = f"{ENERGY_ICON[e['level']]} Energy: {e['name']}"
        if not items:
            text = f"{head}\n\nNothing needs you right now. Send me anything that's on your mind."
        else:
            lines = []
            for i, t in enumerate(items, 1):
                why = f"  <i>{esc(', '.join(t['focus_why']))}</i>" if t["focus_why"] else ""
                lines.append(f"{i}. {fmt_task(t, today)}{why}")
            text = f"{head}\n\n" + "\n".join(lines)
        rows = []
        for t in items:
            rows.append([Btn(f"✅ {t['title'][:28]}", callback_data=f"done:{t['id']}:focus"),
                         Btn("💤", callback_data=f"notnow:{t['id']}:focus"),
                         Btn("🪜", callback_data=f"steps:{t['id']}:focus")])
        rows.append([Btn(f"{ENERGY_ICON[i]} {n}", callback_data=f"energy:{i}:focus")
                     for i, n in ENERGY_NAMES.items()])
        return text, InlineKeyboardMarkup(rows)

    def _today_view(self, uid: int) -> tuple[str, InlineKeyboardMarkup]:
        b = self.tasks.board(uid, self.s.upcoming_days)
        today = self.tasks.today()
        if not b["today"]:
            text = "Nothing scheduled today."
        else:
            text = "<b>Today</b>\n" + "\n".join(fmt_task(t, today) for t in b["today"])
        if b["done_today"]:
            text += f"\n\n✅ {len(b['done_today'])} done · {b['stats']['xp_today']} XP today"
        rows = [[Btn(f"✅ {t['title'][:40]}", callback_data=f"done:{t['id']}:today")]
                for t in b["today"][:30]]
        return text, InlineKeyboardMarkup(rows)

    def _detail(self, t: dict) -> str:
        lines = [fmt_task(t, self.tasks.today())]
        if t["notes"]:
            lines.append(esc(t["notes"]))
        if t["subtasks"]:
            lines.append("")
            lines += [f"{'☑' if st['done'] else '☐'} {esc(st['title'])}" for st in t["subtasks"]]
        lines.append("\n<i>Reply to add steps.</i>")
        if t["status"] != "open":
            lines.append(f"<i>{t['status']}</i>")
        return "\n".join(lines)

    def _detail_kb(self, t: dict) -> InlineKeyboardMarkup:
        tid = t["id"]
        rows = [[Btn(f"{'☑' if st['done'] else '☐'} {st['title'][:45]}",
                     callback_data=f"st:{st['id']}:{tid}")] for st in t["subtasks"][:30]]
        rows.append([Btn("✅ Done", callback_data=f"done:{tid}:x"),
                     Btn("🪜 Steps", callback_data=f"steps:{tid}:x"),
                     Btn("💤 Not now", callback_data=f"notnow:{tid}:x")])
        rows.append([Btn("📦 Someday", callback_data=f"someday:{tid}:x"),
                     Btn("🗑 Delete", callback_data=f"del:{tid}:x")])
        return InlineKeyboardMarkup(rows)

    # ---------- commands ----------
    async def cmd_id(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        await update.effective_message.reply_text(f"Your user id: {update.effective_user.id}")

    async def cmd_link(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        """/link 123456 — connect this Telegram account to a Ticklight account (code from ⚙ → People)."""
        if not self.uid(update):
            return await self._deny(update)
        try:
            uid = self.users.telegram_link((ctx.args or [""])[0], update.effective_user.id)
        except ValueError as e:
            return await update.effective_message.reply_text(str(e))
        name = (self.users.get(uid) or {}).get("name") or "your account"
        await update.effective_message.reply_text(f"Linked to {name}. 👍")

    async def cmd_help(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        if not self.uid(update):
            return await self._deny(update)
        await update.effective_message.reply_html(HELP)

    async def cmd_focus(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        if not (uid := self.uid(update)):
            return await self._deny(update)
        text, kb = self._focus_view(uid)
        await update.effective_message.reply_html(text, reply_markup=kb)

    async def cmd_energy(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        if not (uid := self.uid(update)):
            return await self._deny(update)
        if ctx.args:
            try:
                self.tasks.set_energy(uid, ctx.args[0])
            except ValueError as e:
                return await update.effective_message.reply_text(str(e))
        text, kb = self._focus_view(uid)
        await update.effective_message.reply_html(text, reply_markup=kb)

    async def cmd_today(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        if not (uid := self.uid(update)):
            return await self._deny(update)
        text, kb = self._today_view(uid)
        await update.effective_message.reply_html(text, reply_markup=kb)

    async def cmd_someday(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        if not (uid := self.uid(update)):
            return await self._deny(update)
        items = self.tasks.tasks_with_status(uid, "someday")
        if not items:
            return await update.effective_message.reply_text("Someday is empty.")
        text = "<b>Someday</b> (no pressure)\n" + "\n".join(
            f"• {esc(t['title'])}  /{t['id']}" for t in items[:60])
        kb = InlineKeyboardMarkup([[Btn(f"↩️ {t['title'][:40]}", callback_data=f"back:{t['id']}:x")]
                                   for t in items[:20]])
        await update.effective_message.reply_html(text, reply_markup=kb)

    async def cmd_wish(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        if not (uid := self.uid(update)):
            return await self._deny(update)
        wl = self.capture.wishlist
        text = " ".join(ctx.args or [])
        if text:
            try:
                w = wl.add(uid, text)
            except ValueError as e:
                return await update.effective_message.reply_text(str(e))
            return await update.effective_message.reply_html(f"🎁 On the wishlist: <b>{esc(w['title'])}</b>")
        items = wl.list(uid)
        if not items:
            return await update.effective_message.reply_text("Your wishlist is empty. /wish something to add it.")
        await update.effective_message.reply_html("<b>🎁 Wishlist</b>\n" + "\n".join(
            f"• {esc(w['title'])}" + (f"  {esc(w['price'])}" if w["price"] else "") for w in items[:60]))

    async def cmd_show(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        if not (uid := self.uid(update)):
            return await self._deny(update)
        tid = int(update.effective_message.text[1:].split("@")[0])
        try:
            t = self.tasks.get(uid, tid)
        except NotFound:
            return await update.effective_message.reply_text("No such task.")
        await update.effective_message.reply_html(self._detail(t), reply_markup=self._detail_kb(t))

    async def cmd_action(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        if not (uid := self.uid(update)):
            return await self._deny(update)
        cmd = update.effective_message.text.split()[0][1:].split("@")[0]
        cmd = {"delete": "del"}.get(cmd, cmd)
        if not ctx.args or not ctx.args[0].lstrip("/").isdigit():
            return await update.effective_message.reply_text(f"Usage: /{cmd} <task id>")
        await update.effective_message.reply_html(self._apply(uid, cmd, int(ctx.args[0].lstrip("/"))))

    async def on_text(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        if not (uid := self.uid(update)):
            return await self._deny(update)
        msg = update.effective_message
        tid = self._replied_task_id(msg)
        if tid:
            try:
                t = self.tasks.add_subtasks(uid, tid, split_items(msg.text))
            except (NotFound, ValueError) as e:
                return await msg.reply_text(str(e))
            return await msg.reply_html(self._detail(t), reply_markup=self._detail_kb(t))
        await msg.chat.send_action("typing")
        await self._capture(msg, uid, msg.text, "telegram")

    async def on_voice(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        if not (uid := self.uid(update)):
            return await self._deny(update)
        msg = update.effective_message
        media = msg.voice or msg.audio
        if media.file_size and media.file_size > 20 * 1024 * 1024:
            return await msg.reply_text("That recording is too long. Keep it under a few minutes.")
        await msg.chat.send_action("typing")
        f = await media.get_file()
        audio = bytes(await f.download_as_bytearray())
        try:
            text = await self.voice.transcribe(audio, "voice.ogg", media.mime_type or "audio/ogg")
        except VoiceUnavailable as e:
            return await msg.reply_text(f"Couldn't transcribe that ({e}). Try typing it.")
        if not text:
            return await msg.reply_text("I couldn't hear anything in that one.")
        await self._capture(msg, uid, text, "voice", heard=text)

    async def cmd_hyperfocus(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        if not (uid := self.uid(update)):
            return await self._deny(update)
        arg = (ctx.args or [""])[0].lower()
        action = "hyperfocus:off" if arg in ("off", "stop", "end") else f"hyperfocus:{arg if arg.isdigit() else ''}"
        r = await actions.apply(self.ctx, uid, action)
        await update.effective_message.reply_text(r["message"])

    async def cmd_session(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        """/session [minutes] [task id]  ·  /session stop"""
        if not (uid := self.uid(update)):
            return await self._deny(update)
        S = self.ctx.sessions
        if not S:
            return
        args = [a.lstrip("/") for a in ctx.args or []]
        cur = S.active(uid)
        if args and args[0] in ("stop", "end", "off"):
            if not cur or cur["state"] == "break":
                return await update.effective_message.reply_text("No session running.")
            s = S.end(uid, cur["id"], "stopped")
            return await update.effective_message.reply_text(
                f"⏹ {s['actual_minutes']} min · +{s['xp']} XP\n{s['coach']['emoji']} {s['coach']['text']}")
        nums = [int(a) for a in args if a.isdigit()]
        minutes = nums[0] if nums else None
        task_id = nums[1] if len(nums) > 1 else None
        if task_id is None:
            top = self.tasks.focus(uid, 1)
            task_id = top[0]["id"] if top else None
        try:
            s = await S.start(uid, minutes, task_id)
        except NotFound:
            return await update.effective_message.reply_text("No such task.")
        what = f"<b>{esc(s['task']['title'])}</b>" if s["task"] else "whatever you pick"
        kb = InlineKeyboardMarkup([[Btn("⏹ Stop", callback_data=f"act:session:stop:{s['id']}"),
                                    Btn("➕ 5 min", callback_data=f"act:session:extend:{s['id']}")]])
        await update.effective_message.reply_html(
            f"▶ {s['minutes']} minutes on {what}\n{s['coach']['emoji']} {esc(s['coach']['text'])}", reply_markup=kb)

    async def _capture(self, msg, uid: int, text: str, source: str, heard: str = "") -> None:
        try:
            result = await self.capture.run(uid, text, source)
        except ValueError as e:
            return await msg.reply_text(str(e))
        created = result["tasks"]
        today = self.tasks.today()
        lines = []
        for t in created:
            parked = "  <i>(someday)</i>" if t["status"] == "someday" else ""
            lines.append(fmt_task(t, today) + parked)
        head = f"🎙 <i>“{esc(heard[:300])}”</i>\n" if heard else ""
        head += f"➕ {len(created)} task{'s' if len(created) != 1 else ''}"
        if (wishes := result.get("wishes")):
            head += f" · 🎁 {len(wishes)} on the wishlist"
            lines += [f"🎁 {esc(w['title'])}" for w in wishes]
        if result["method"] == "parser" and self.ai.configured:
            head += "  <i>(AI was busy, sorted by rules)</i>"
        rows = [[Btn(f"✅ {t['title'][:40]}", callback_data=f"done:{t['id']}:x")]
                for t in created[:10] if t["status"] == "open"]
        rows.append([Btn("↩️ Undo all", callback_data=f"uncap:{result['capture_id']}:x")])
        await msg.reply_html(head + "\n" + "\n".join(lines), reply_markup=InlineKeyboardMarkup(rows))

    def _replied_task_id(self, msg) -> int | None:
        r = msg.reply_to_message
        if not r or not r.from_user or not r.from_user.is_bot:
            return None
        ids = set(re.findall(r"(?<![\w/])/(\d+)\b", r.text or ""))
        return int(ids.pop()) if len(ids) == 1 else None

    def _apply(self, uid: int, action: str, tid: int) -> str:
        try:
            if action == "done":
                before = self.tasks.get(uid, tid)
                t = self.tasks.complete(uid, tid)
                nxt = ""
                if t["recurrence"]:
                    nxt = "\nNext: " + date.fromisoformat(t["due_date"]).strftime("%a %d %b")
                line = self.ctx.coach.react(uid, before, t) if self.ctx.coach else None
                said = f"\n{line['emoji']} <i>{esc(line['text'])}</i>" if line else ""
                return f"✅ <b>{esc(t['title'])}</b>{nxt}{fmt_reward(t.get('reward'))}{said}"
            if action == "undo":
                return f"↩️ Undid <b>{esc(self.tasks.undo_complete(uid, tid)['title'])}</b>"
            if action == "del":
                return f"🗑 Deleted <b>{esc(self.tasks.delete(uid, tid)['title'])}</b>"
            if action == "restore":
                return f"↩️ Restored <b>{esc(self.tasks.restore(uid, tid)['title'])}</b>"
            if action == "notnow":
                t = self.tasks.not_now(uid, tid)
                hint = "\nIt keeps getting pushed. Tap 🪜 to break it into tiny steps?" \
                    if t["suggest_breakdown"] else ""
                return f"💤 <b>{esc(t['title'])}</b> is out of the way for 3 hours.{hint}"
            if action == "someday":
                return f"📦 Parked <b>{esc(self.tasks.to_someday(uid, tid)['title'])}</b>"
            if action == "back":
                return f"↩️ <b>{esc(self.tasks.bring_back(uid, tid)['title'])}</b> is back"
        except NotFound:
            return "No such task."
        except ValueError as e:
            return f"Can't do that: {esc(str(e))}"
        return "Unknown action."

    # ---------- buttons ----------
    async def on_button(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        q = update.callback_query
        if not (uid := self.uid(update)):
            return await q.answer("Not authorised")
        action, arg, scope = (q.data.split(":") + ["", ""])[:3]
        arg = int(arg) if arg.isdigit() else 0

        async def edit(text, kb=None):
            try:
                await q.edit_message_text(text, parse_mode=ParseMode.HTML, reply_markup=kb)
            except Exception as e:  # "message is not modified" etc.
                log.debug("edit failed: %s", e)

        if action == "act":  # notification buttons, shared with phone push
            r = await actions.apply(self.ctx, uid, q.data[4:])
            await q.answer(r["message"][:190])
            if r["ok"]:
                text = (q.message.text_html or "") + f"\n\n<i>{esc(r['message'])}</i>"
                return await edit(text, None)
            return

        if action == "st":
            try:
                t = self.tasks.update_subtask(uid, arg)
            except NotFound:
                return await q.answer("That step no longer exists")
            await q.answer("+3 XP" if any(s["done"] for s in t["subtasks"] if s["id"] == arg) else "")
            return await edit(self._detail(t), self._detail_kb(t))
        if action == "energy":
            self.tasks.set_energy(uid, arg)
            await q.answer(f"Energy: {ENERGY_NAMES[arg]}")
            return await edit(*self._focus_view(uid))
        if action == "uncap":
            n = self.capture.undo(uid, arg)
            await q.answer()
            return await edit(f"↩️ Removed {n} task{'s' if n != 1 else ''}.")
        if action == "steps":
            await q.answer("Breaking it down…")
            try:
                t = self.tasks.get(uid, arg)
                steps = await self.ai.breakdown(
                    t["title"], t["notes"], [s["title"] for s in t["subtasks"] if s["done"]])
                t = self.tasks.replace_steps(uid, arg, steps)
            except AIUnavailable:
                return await q.message.reply_text(
                    "AI isn't available right now. Reply to the task with steps, one per line.")
            except NotFound:
                return
            return await q.message.reply_html(
                f"🪜 First step: <b>{esc(t['next_step']['title'])}</b>\n\n" + self._detail(t),
                reply_markup=self._detail_kb(t))

        msg = self._apply(uid, action, arg)
        await q.answer(html.unescape(re.sub(r"<[^>]+>", "", msg))[:190])
        if scope == "focus":
            return await edit(*self._focus_view(uid))
        if scope == "today":
            return await edit(*self._today_view(uid))
        undo = {"done": "undo", "del": "restore", "someday": "back"}.get(action)
        kb = InlineKeyboardMarkup([[Btn("↩️ Undo", callback_data=f"{undo}:{arg}:x")]]) if undo else None
        await edit(msg, kb)

    # ---------- notifications ----------
    async def send(self, uid: int, note: Note, p: dict) -> None:
        """Notification channel used by the scheduler."""
        if not p["channels"]["telegram"]:
            return
        chats = [tg for tg in self.s.telegram_users if self.users.for_telegram(tg) == uid]
        if not chats:
            return
        buttons = []
        for label, action in note.actions:
            if action.startswith("url:"):
                if self.s.base_url.startswith("https://"):
                    buttons.append(Btn(label, url=self.s.base_url + action[4:]))
            else:
                buttons.append(Btn(label, callback_data=f"act:{action}"[:64]))
        rows = [buttons[i:i + 4] for i in range(0, len(buttons), 4)]
        text = f"<b>{esc(note.title)}</b>" + (f"\n{esc(note.body)}" if note.body else "")
        for cid in chats:
            try:
                await self.app.bot.send_message(cid, text, parse_mode=ParseMode.HTML,
                                                reply_markup=InlineKeyboardMarkup(rows) if rows else None)
            except Exception as e:
                log.warning("send to %s failed: %s", cid, e)
