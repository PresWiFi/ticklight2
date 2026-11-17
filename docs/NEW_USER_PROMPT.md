# Set up Ticklight for someone new

Paste the prompt below into Claude Code (or another coding agent with shell access), started in a
clone of this repo. It interviews the person, maps their answers to Ticklight's settings, then
deploys and checks their setup.

---

```
You're setting up Ticklight (this repo) for a new person with ADHD. Read README.md, .env.example,
app/prefs.py (DEFAULTS), android/README.md and watch/README.md first so you know what exists.

Rules:
- Ask questions in small batches (3–5 at a time) and offer choices where possible. Keep it short;
  long questionnaires are exactly what ADHD brains drop.
- Don't change app code to fit one person. Use settings, prefs and the in-app options. If they need
  something that doesn't exist, write it down under "Feature requests" in the summary instead.
- Never print secrets (tokens, client secrets, keys) back in chat summaries or commit them.
- Ask before anything that creates or changes infrastructure (containers, DNS, proxy, Authentik).

## 1. Where it runs

Ask:
- Join an existing Ticklight server as another user (partner/household), or get their own server?
- If their own: Proxmox LXC, Docker, or local? Public URL and reverse proxy? Authentik/OIDC or
  AUTH_MODE=none on a trusted LAN?
- Timezone.

If they're joining an existing server, skip to step 3 and use ⚙ → People → invite link.

## 2. Server settings (.env)

Ask, then fill in .env (or /etc/ticklight/ticklight.env in the LXC):
- AI: OpenRouter key (free models), a local OpenAI-compatible server (Ollama etc.), or none.
  Set AI_ORDER from what they have.
- Voice: a speech-to-text server (WHISPER_URL), or browser speech only.
- Telegram bot (TELEGRAM_BOT_TOKEN, their id in TELEGRAM_ALLOWED_USERS)? Alexa (see alexa/)?
- How long until missed tasks move to Someday (STALE_OVERDUE_DAYS, STALE_UNDATED_DAYS). Shorter if
  an overdue pile makes them avoid the app.
- Typical energy by time of day -> ENERGY_SCHEDULE (e.g. 07:1,10:3,14:2,19:1).

## 3. How their brain works -> per-user prefs

Ask about each area, then map the answers. Prefs are set in ⚙ in the app, or with
PATCH /api/prefs using a device token for that user.

| Ask about | Maps to |
|---|---|
| Why past systems died (novelty, upkeep, stale lists, forgot it existed) | Desk monitor/display mode, widget, routines on, theme rotation, coach rotate |
| What stops them starting (unclear first step, boring, forgetting) | Encourage "break it down", sessions, coach persona |
| Body doubling: does working alongside someone help? | sessions.minutes, break_minutes, checkins, sound |
| Out of sight = out of mind? Always-on screen available? | Desk monitor URL with a device token, Android widget |
| Where ideas show up (desk, phone, hands busy, in bed, speaker) | Android app, watch, Telegram, Alexa, voice |
| Reminders: do they get ignored after a while? Snooze forever? | reminders.lead_minutes, follow_up_minutes, max_follow_ups |
| Meds, meals, water: what and when | nudges (kind, times or every_minutes, follow-ups) |
| Morning and evening rhythm, weekly review day | morning/evening/weekly enabled + time/day |
| Hyperfocus: what must break through? How long before surfacing? | hyperfocus_minutes |
| Streaks, points, games: motivating or stressful? | coach.persona, game layer, streak freezes |
| Do they want someone to notice wins (partner/family)? | people.* and invite link |
| Coach tone (cheerful, blunt, calm, cheeky, robot, cute) | coach.persona: sunny, bolt, sage, gremlin, unit7, mochi |

## 4. Deploy

- Proxmox: `bash proxmox/ticklight-lxc.sh` on the host (answers the prompts from step 1–2).
- Docker: `cp .env.example .env`, edit, `docker compose up -d`.
- OIDC: create the Authentik provider/application with redirect URI <BASE_URL>/auth/callback.
- Phone: Android app from android/README.md (needs ntfy for notifications), or the PWA from Chrome.
  If they log in with passkeys in the Android app, serve android/assetlinks.json on the login host.
- Watch: pair from ⚙ → Devices. Desk monitor: ⚙ → Device tokens -> <BASE_URL>/?display&token=<token>.

## 5. Check it works

With them, confirm: login, quick add (n, type, Enter), a brain dump gets split into tasks, a test
notification arrives on the phone, a nudge fires, the morning routine shows tasks to pick, and the
desk monitor shows Focus. Fix or note anything that fails.

## 6. Hand-off

Write `profile.md` for them (not committed if the repo is shared): their answers in short bullets,
the settings you chose and why, the URLs, how to update, and "Feature requests". Tell them the
3 things to do first (e.g. install the app, do tomorrow's morning launch, pin the widget).
```
