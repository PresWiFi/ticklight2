# Ticklight 2 plan

Design input: the ADHD questionnaire (2026-11-14). Top goal: a system that stays engaging after
the novelty wears off, needs no upkeep, and never builds a guilt pile.

## Phase 1 — core ✅
- Capture anything (web dump box, Telegram text) → AI cleans it into tasks: title, life area,
  energy, minutes, tiny first step. Falls back to the rule-based parser when AI is unavailable.
- Focus view: next 1–3 tasks, chosen by urgency, priority, current energy, and "not now" taps.
  Full list one click away.
- "Break it down": AI splits a task into tiny steps; the focus card shows only the next one.
- Energy: set low/medium/high, or use a time-of-day default.
- No guilt pile: no "overdue" label (carried-over tasks sit in Today), stale tasks move to Someday.
- Forgiving streaks: one missed occurrence doesn't break a streak.
- XP, levels, and random surprise bonuses on completion.
- Telegram: capture, /focus, /today, done/not-now buttons, morning focus message, due-time reminders.
- Auth: OIDC (Authentik) web login, per-device tokens for the desk monitor and scripts, or no auth on a trusted LAN.
- Multi-user schema from day one (partner comes in phase 5).

## Phase 2 — capture and routines ✅
- Voice notes (Telegram) → Whisper on the 4 GB Proxmox GPU.
- Galaxy Watch: small Wear OS app (double-press home key → speak → captured), paired with a 6-digit code.
  Later: a tile showing the next Focus task with a Done button.
- Installable PWA with push notifications (Android).
- Routines: morning launch, evening shutdown, weekly reset (do / reschedule / drop / break down),
  meds/meals/water nudges.
- Reminders that vary wording and timing, escalate, and pull you out of hyperfocus only for
  meetings, meals, meds, and hard deadlines.

## Phase 3 — coach and body double ✅
- Focus sessions on the desk monitor: timer, ambient presence, check-ins, breaks, generated ambient sound.
- Coach with six switchable personalities (or weekly rotation) that reacts to what you do; AI-written
  lines at key moments when AI is configured.

## Phase 4 — game layer ✅
- Coins, shop, wardrobe for the companion (head/face/neck/aura), 12 themes (weekly rotation option),
  daily streak with freezes, weekly challenges, 20 badges, seasonal events with limited items.

## Phase 5 — partner ✅
- Invite links, wins feed with reactions, shared tasks (#home), sending tasks, evening digest,
  privacy toggles, Telegram account linking.

## Phase 6 — voice assistants ✅
- Alexa custom skill: capture, what's next, tick off, meds, focus session.
- Google Home: not possible (Conversational Actions shut down 2023); covered by watch/phone/Telegram voice.
