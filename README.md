# Ticklight 2

A self-hosted ADHD task system for an always-on desk monitor, your phone, your watch, and Telegram.

- **Capture anything.** Type, paste, or say a messy brain dump (🎙 in the app, Telegram voice notes,
  the watch app); AI splits it into tasks with a life area, energy level, time estimate, and a tiny
  first step. Without AI, one line = one task (v1 quick-add syntax).
- **Focus.** The next 1–3 things, picked by what's due, priority, your current energy, and what you've
  already started. Each shows only its next step. **Not now** hides a task for 3 hours, no judgement.
- **Break it down.** AI turns a stuck task into tiny steps; the first takes under 2 minutes.
- **Energy.** 🪫 🔋 ⚡ in the header. Untouched, it follows `ENERGY_SCHEDULE`; a tap overrides it for 4 hours.
- **No guilt pile.** Nothing is labelled overdue. Missed one-off tasks sit at the bottom of Today, then
  move to 📦 Someday after 7 days (undated ones after 21 days untouched). Bring them back any time.
- **Today's three.** 📌 up to three picks a day; they lead Focus.
- **Routines.** ☀️ Morning launch (meds, energy, pick today's three, brain dump), 🌙 evening shutdown
  (what you did, park leftovers, brain dump, tomorrow's first thing), 🧹 weekly reset (one decision
  at a time for stale, pushed, and Someday tasks). A notification at the set time and a dashboard button until done.
- **Body nudges.** Meds, meals, water, or your own, at set times or every N minutes. Meds and meals
  repeat until you tap ✅.
- **Reminders that don't turn into wallpaper.** Timed tasks get a heads-up, an on-time ping, and
  follow-ups, each with varied wording. 💤 Not now stops the follow-ups.
- **Hyperfocus.** 🎧 for 90 minutes (configurable): only timed tasks, meds, meals, and high-priority
  follow-ups get through, and you're told when to surface.
- **Focus sessions (body doubling).** ▶ on a Focus card (or ⏱, or `/session 25` in Telegram): a
  full-screen timer with your companion beside you, the task's current step, check-ins ("going well /
  stuck / got distracted"), +5 min, breaks, and another round. Notifications go quiet during the
  session. Optional ambient sound generated in the browser: brown noise, rain, café, low hum. On the
  desk monitor (display mode) a session started from your phone takes over the screen.
- **Coach.** A companion in the corner that reacts to what you do: finishing hard tasks, level-ups,
  streaks, routines, sessions, coming back after a few days away. Six personalities (Sunny, Bolt, Sage,
  Gremlin, Unit 7, Mochi), or a new one every week. With AI configured it writes some lines itself in
  that voice; otherwise it uses its own hand-written lines, never repeating the last few.
- **Rewards.** XP per task (more for harder/longer ones), +3 per step, levels, and random surprise bonuses.
  Streaks forgive one missed occurrence.
- **Game layer** (🎒 under your level, or `g`). Coins from every task (and lucky drops) buy accessories
  for your companion, themes, and streak freezes. A daily streak that freezes protect automatically.
  Three new weekly challenges every Monday, sized to how last week went; tap to claim. 20 badges, some
  unlocking items. Seasonal events (Spooky, Frost, Bloom, Summer) with limited items and themes.
  Themes can rotate weekly.
- **People (partner / household).** ⚙ → People → invite link. Linked people see each other's wins
  (optionally without task names) and react with 🎉❤️👏🔥💪😂; the other person gets a notification.
  Mark a task 🏠 shared (or put `#home` / `#shared` on a line when capturing) and it shows up for both;
  whoever ticks it gets the XP. Send each other tasks from the 💞 card (AI-sorted like any capture).
  Optional evening digest: "Sam finished 5 things today". Open tasks stay private unless shared.
- **Android app.** The whole app outside Chrome (so Chrome screen time limits don't apply), with
  notifications, a Focus widget, a share target and a quick settings tile. See [android/README.md](android/README.md).
  Or install the web app from Chrome (⋮ → Add to home screen) for Web Push notifications.
- **Telegram.** Send text or a voice note to capture it. `/focus`, `/energy`, `/today`, `/someday`,
  `/session`, `/hyperfocus`. Gets the same notifications as the phone (toggle per channel in ⚙ → Notifications).
- **Alexa.** "Alexa, tell Tick Light to add buy milk tomorrow" · "…ask Tick Light what's next" ·
  "…tell Tick Light I finished the dishes" · "…I took my meds" · "…start a 25 minute focus".
- **Galaxy Watch.** Double-press the home key, speak, done. See [watch/README.md](watch/README.md).
- **Login.** OIDC (Authentik) for browsers, device tokens for the desk monitor, watch, and scripts.

Python (FastAPI) + SQLite. No build step.

## Run

Proxmox LXC (on the Proxmox host):

```
bash proxmox/ticklight-lxc.sh            # create
bash proxmox/ticklight-lxc.sh --update 123
```

Docker: `cp .env.example .env && docker compose up -d`

Local: `uv venv && uv pip install -r requirements-dev.txt && python -m app.main`

Config is in `.env` (`/etc/ticklight/ticklight.env` in the LXC). See `.env.example`.

## AI

Set `OPENROUTER_API_KEY`. By default Ticklight picks from OpenRouter's current free models and falls
back through `AI_ORDER`:

- `free`: OpenRouter free models (`OPENROUTER_FREE_MODELS`, or auto-picked). Rate-limited; OpenRouter
  allows more free requests per day once the account has some credit.
- `local`: any OpenAI-compatible server, e.g. Ollama on another PC
  (`LOCAL_AI_URL=http://pc:11434/v1`, `LOCAL_AI_MODEL=qwen3:14b`). Skipped while unreachable.
- `paid`: `OPENROUTER_PAID_MODEL`, a cheap model for when the others fail. Blank = never pay.

A failing model is skipped for 10 minutes. If everything fails, the rule-based parser takes over.

## Voice

Voice notes need a speech-to-text server with an OpenAI-compatible `/v1/audio/transcriptions` API.
[Speaches](https://speaches.ai) (faster-whisper) fits on the 4 GB GPU; `Systran/faster-whisper-small`
uses about 1 GB of VRAM. Example on a Docker host with the NVIDIA runtime (check the Speaches docs for
the current image tag and model download step):

```
docker run -d --gpus all -p 8000:8000 -v hf-cache:/home/ubuntu/.cache/huggingface \
  ghcr.io/speaches-ai/speaches:latest-cuda
```

Then set `WHISPER_URL=http://<host>:8000/v1`. Any other OpenAI-compatible server works (OpenAI, Groq:
also set `WHISPER_API_KEY`). Without one, the 🎙 button falls back to the browser's own speech
recognition where available (Chrome on Android), and the watch uses its own.

## People

With `AUTH_MODE=oidc`, your partner logs in with their own Authentik account and opens the invite link.
With `AUTH_MODE=none`, opening the invite link creates their account and logs that browser in with a
device token (keep the link private; it works once and expires after 3 days).

For Telegram, add their Telegram id to `TELEGRAM_ALLOWED_USERS`, then they press ⚙ → People →
**Link this account to Telegram** and send the code to the bot (`/link 123456`).

## Phone notifications

Push needs HTTPS (your reverse proxy). Open Ticklight on the phone, install it, then ⚙ → Notifications →
**Enable notifications** → **Send test**. Keys are generated on first start and kept in the database.

## Alexa

A private skill on your own Amazon account (no publishing). Needs Ticklight reachable over HTTPS
with a normal certificate (e.g. via your reverse proxy).

1. [Alexa developer console](https://developer.amazon.com/alexa/console/ask) → **Create skill** →
   type *Other / Custom*, hosting *Provision your own*. Start from scratch.
2. **Interaction model → JSON editor**: paste [alexa/interaction-model.json](alexa/interaction-model.json),
   save, **Build**.
3. **Endpoint**: HTTPS, default region `https://tasks.example.com/alexa`, certificate option
   "a certificate from a trusted certificate authority".
4. Copy the skill ID (`amzn1.ask.skill...`) into `ALEXA_SKILL_ID` and restart.
5. **Test** tab → enable *Development*. It now works on Echo devices signed in to the same Amazon account.

Every request is checked the way Amazon requires (certificate chain, signature, timestamp).
Several people on one Echo: set `ALEXA_USERS` (the account ids appear in the log when a request comes in).

**Google Home / Nest:** Google shut down third-party Conversational Actions in 2023, so there's no
supported way for a Google speaker to send your words to Ticklight. Use the watch, the phone app's
mic, or Telegram voice notes instead.

## Login with Authentik

1. In Authentik: **Applications → Create with provider → OAuth2/OpenID**.
   - Client type: confidential
   - Redirect URI: `https://<your ticklight host>/auth/callback` (strict)
   - Scopes: `openid`, `profile`, `email` (defaults)
2. Copy the client ID and secret. The issuer is shown on the provider page
   (`https://auth.example.com/application/o/<slug>/`).
3. Set:
   ```
   AUTH_MODE=oidc
   BASE_URL=https://tasks.example.com
   OIDC_ISSUER=https://auth.example.com/application/o/ticklight/
   OIDC_CLIENT_ID=...
   OIDC_CLIENT_SECRET=...
   OIDC_ALLOWED_GROUPS=ticklight     # optional
   ```
4. Restart. The first login takes over the tasks you created before enabling login.

Run it behind your reverse proxy with HTTPS; the session cookie is `Secure` when `BASE_URL` is https.

## Device tokens (desk monitor, watch, scripts)

⚙ → **Create token**. A token is shown once.

- Desk monitor: `https://tasks.example.com/?display&token=<token>` (saved in the browser, then removed
  from the URL). `chromium --app=...` or F11 for kiosk.
- Watch: use pairing instead (⚙ → Devices → Pair your watch).
- Capture from anything that can send HTTP:
  ```
  curl -X POST 'https://tasks.example.com/api/capture?source=watch' \
    -H 'Authorization: Bearer <token>' -H 'Content-Type: text/plain' -d 'call mum tomorrow'
  ```
  Returns immediately; add `&wait=1` to get the created tasks back.

## Telegram

1. Create a bot with [@BotFather](https://t.me/BotFather), set `TELEGRAM_BOT_TOKEN`.
2. Send `/id` to the bot, put the number in `TELEGRAM_ALLOWED_USERS`, restart.

Any message is captured. Reply to a task message to add steps. Buttons: ✅ done, 💤 not now, 🪜 break down.

## Keyboard

`/` capture · `v` voice · `s` focus session · `g` collection · `n` new task · `e` cycle energy · `h` hyperfocus · `d` display mode ·
`t` light/dark · `Esc` clear.

## Tests

`.venv/bin/python -m pytest`
