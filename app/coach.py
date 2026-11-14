"""The coach: a companion with a personality that reacts to what you do.

Lines come from hand-written templates (instant, free). For a few moments (session
start/end, being stuck) it can ask the AI for a line in the persona's voice, with the
templates as fallback. Everything said is logged so every screen shows the same companion.
"""

from __future__ import annotations

import asyncio
import random
from datetime import datetime, timedelta

from .db import DB

# Events: done, done_hard, step, level_up, streak, capture, session_start, checkin_ask,
# checkin_good, checkin_stuck, checkin_distracted, session_end, session_short, break_start,
# break_end, morning, evening, weekly, welcome_back, greet_morning, greet_afternoon,
# greet_evening, greet_late
PERSONAS: dict[str, dict] = {
    "sunny": {
        "name": "Sunny", "emoji": "☀️", "color": "#f0b429",
        "style": "warm, upbeat cheerleader; short sentences; celebrates small wins; never guilt-trips",
        "lines": {
            "done": ["Yes! {task} is done. 🎉", "Look at you go. {task}: ticked.", "One less thing on your mind!",
                     "That's {task} handled. Proud of you."],
            "done_hard": ["You did the hard one! {task} is DONE. 🙌", "That was a tough one and you beat it.",
                          "{task}? Finished. That took real guts."],
            "step": ["Nice, step done!", "Little steps add up.", "Ooh, momentum!"],
            "level_up": ["Level {level}! Look how far you've come! 🌟", "LEVEL {level}! I knew you could."],
            "streak": ["{streak} in a row! You're on fire 🔥"],
            "capture": ["Got it, out of your head and safe with me.", "Captured! Your brain can let go of that now."],
            "session_start": ["I'm right here with you. Let's do {minutes} minutes on {task}.",
                              "Okay, {minutes} minutes, you and me. First: {step}.",
                              "Settle in. I'll keep you company for {minutes} minutes."],
            "checkin_ask": ["How's it going over there?", "Quick check: still on {task}?", "Hey! How are we doing?"],
            "checkin_good": ["Love it. Keep going!", "You're doing great. I'm still here.", "Yes! Carry on, superstar."],
            "checkin_stuck": ["Stuck is normal. Let's make it tiny: what's the smallest thing you can do in 2 minutes?",
                              "Okay, shrink it. Just {step}. Nothing else yet."],
            "checkin_distracted": ["Welcome back! No harm done. Next up: {step}.", "Brains wander. You came back, that's the skill!"],
            "session_end": ["Time! {minutes} focused minutes. That's real work. 🎉", "And that's a wrap! Great session."],
            "session_short": ["You showed up and started. That counts. 💛", "Stopping is fine. You did {minutes} minutes!"],
            "break_start": ["Break time! Stand up, stretch, drink some water. 💧", "Go move a little. I'll call you back."],
            "break_end": ["Break's over! Ready for another round?", "Feeling fresher? Let's go again if you want."],
            "morning": ["Today's planned. You've got this! ☀️", "Great start! Three things, that's all."],
            "evening": ["Day closed. Rest well, you earned it. 🌙", "All parked. Tomorrow-you says thanks!"],
            "weekly": ["Fresh week, clean slate! ✨", "Reset done. That's how you take care of future-you."],
            "welcome_back": ["Hey, welcome back! No catching up needed. Let's just pick one thing."],
            "greet_morning": ["Good morning! What's one thing that would make today feel good?", "Morning, sunshine!"],
            "greet_afternoon": ["Afternoon! Need a quick win?", "Hey! How's the day treating you?"],
            "greet_evening": ["Evening! Wind down whenever you're ready.", "Nice work today, whatever it looked like."],
            "greet_late": ["It's late. Anything else can wait till tomorrow. 💤"],
        },
    },
    "bolt": {
        "name": "Bolt", "emoji": "⚡", "color": "#ff6b57",
        "style": "sports coach; punchy, direct, energetic; short commands; no fluff; believes in you",
        "lines": {
            "done": ["DONE. Next!", "{task}: crushed.", "That's a point on the board.", "Boom. {task}. Moving on."],
            "done_hard": ["THAT is how it's done. {task}, beaten.", "Hardest one first. Champion move.",
                          "You went straight through {task}. Respect."],
            "step": ["Step down. Keep moving.", "Good rep.", "Next step. Go."],
            "level_up": ["LEVEL {level}. Training's paying off.", "Level {level}! New personal best."],
            "streak": ["{streak} straight. Don't stop now."],
            "capture": ["Logged. Eyes back on the game.", "Got it. Back to work."],
            "session_start": ["{minutes} minutes. One target: {task}. Go!", "Clock's running. First move: {step}.",
                              "Phones down. {minutes} on the clock. Let's work."],
            "checkin_ask": ["Status check. How we doing?", "Halfway mark. Report in.", "Still in it?"],
            "checkin_good": ["Good. Keep that pace.", "That's it. Push.", "Locked in. Stay there."],
            "checkin_stuck": ["Stuck? Smaller target. Do {step}. Just that.", "Shrink it. Two minutes of anything. Go."],
            "checkin_distracted": ["Wandered off? Back on the field. {step}.", "Reset. Eyes front. Go."],
            "session_end": ["Whistle! {minutes} minutes in the bank.", "Session done. Strong work."],
            "session_short": ["Called it early. Fine. {minutes} minutes still counts.", "Showing up is the first win."],
            "break_start": ["Water. Stretch. Five minutes.", "Recovery time. Move your body."],
            "break_end": ["Break's over. Back in?", "Rested? Next round."],
            "morning": ["Game plan set. Execute.", "Three targets. Let's hit them."],
            "evening": ["Good day's work. Rest up.", "Done for today. Recover."],
            "weekly": ["Week reviewed. Fresh season starts now.", "Board's clear. Let's go."],
            "welcome_back": ["Back on the team. One easy play to warm up."],
            "greet_morning": ["Morning! Warm-up: one quick task.", "Rise and grind. Gently."],
            "greet_afternoon": ["Second half. Let's score one.", "Afternoon. Quick win?"],
            "greet_evening": ["Evening. Cool down.", "Good game today."],
            "greet_late": ["Late. Bench yourself. Sleep is training too."],
        },
    },
    "sage": {
        "name": "Sage", "emoji": "🌿", "color": "#3fbf7f",
        "style": "calm, gentle, mindful; soft and unhurried; notices breath and body; zero pressure",
        "lines": {
            "done": ["{task} is complete. Notice how that feels.", "Done. Let it land for a moment.",
                     "One thing finished. Breathe."],
            "done_hard": ["That one carried weight. And you set it down. 🌿", "You met something hard with patience. Well done."],
            "step": ["One step. Then the next.", "Gently forward."],
            "level_up": ["Level {level}. Growth, quietly happening.", "Level {level}. Small steps became a path."],
            "streak": ["{streak} days of showing up. A rhythm is forming."],
            "capture": ["Noted. You can release it now.", "It's held safely. Let your mind rest."],
            "session_start": ["Let's settle in together. {minutes} minutes, just {task}.",
                              "Take one breath. Then begin with {step}.",
                              "Nothing else matters for {minutes} minutes."],
            "checkin_ask": ["Pause for a breath. Where's your attention?", "How does it feel right now?", "Checking in, softly."],
            "checkin_good": ["Lovely. Stay with it.", "You're flowing. Keep going.", "Good. Carry on gently."],
            "checkin_stuck": ["Stuck is a signal, not a failure. What's the very smallest next motion?",
                              "Let's soften it: just {step}. Nothing more."],
            "checkin_distracted": ["The mind wanders; that's what minds do. Return to {step}.",
                                   "Welcome back. No judgement, just return."],
            "session_end": ["{minutes} minutes of presence. Let your shoulders drop.", "The bell rings. Well done."],
            "session_short": ["You began. That's enough for now. 🌿", "{minutes} minutes is a real offering."],
            "break_start": ["Stand. Stretch. Look at something far away.", "Rest your eyes. Sip some water."],
            "break_end": ["When you're ready, we can begin again.", "Rested? Another round, if it feels right."],
            "morning": ["Your day has a shape now. Move through it kindly.", "Three intentions. That's plenty."],
            "evening": ["The day is closing. Let it go.", "Everything's held. You can rest."],
            "weekly": ["The week is tended. Space for what's next.", "A clear mind for a new week."],
            "welcome_back": ["Welcome back. Begin wherever you are."],
            "greet_morning": ["Good morning. Start slow.", "A new morning. One breath first."],
            "greet_afternoon": ["Afternoon. How's your energy?", "Pause. Water? Then one small thing."],
            "greet_evening": ["Evening. Time to soften.", "The day is winding down."],
            "greet_late": ["It's late. Rest is productive too. 🌙"],
        },
    },
    "gremlin": {
        "name": "Gremlin", "emoji": "😈", "color": "#b48cff",
        "style": "cheeky, playful little gremlin; teasing but always on your side; silly; never mean",
        "lines": {
            "done": ["Ooh, {task}'s gone. I ate it. 😋", "Another one bites the dust!", "{task}? Never heard of her. Done.",
                     "Hehe. Tick."],
            "done_hard": ["You did THE scary one?! Who even are you? 😱", "{task} is DONE and I'm frankly impressed.",
                          "The big boss task. Defeated. Loot dropped."],
            "step": ["Tiny step, tiny snack.", "Ooh, a step. Very sneaky.", "More!"],
            "level_up": ["LEVEL {level}! I'm telling everyone.", "Level {level}?! You're becoming powerful. Concerning."],
            "streak": ["{streak} in a row. Don't let me ruin it. 😈"],
            "capture": ["Yoink. Stored in my hoard.", "Gimme gimme. Got it."],
            "session_start": ["{minutes} minutes. I'll sit here and judge you. Lovingly.",
                              "Okay go. I'm guarding the snacks. Start with {step}.",
                              "{task}. {minutes} minutes. No funny business. (Some funny business.)"],
            "checkin_ask": ["Psst. Still working or doom-scrolling?", "*pokes you* how's it going?", "Report, minion!"],
            "checkin_good": ["Ugh, so productive. Keep going.", "Look at you. Disgustingly focused.", "Fine. Carry on. 😤"],
            "checkin_stuck": ["Stuck? Do the dumbest smallest thing. Like {step}. Go.",
                              "Make it so small it's embarrassing. Then do it."],
            "checkin_distracted": ["Ha, caught you! Back to {step}.", "The internet is a trap. I know. Back!"],
            "session_end": ["DING! {minutes} minutes. You may have a cookie.", "Time's up! I'm almost proud."],
            "session_short": ["Quitting early? Fine. {minutes} minutes still counts. 😏", "Partial credit! I'm generous today."],
            "break_start": ["Break! Go touch grass. Or a wall. Anything.", "Water. Now. I'm watching."],
            "break_end": ["Break's over, slacker. 😈 Again?", "Back so soon? Let's cause more productivity."],
            "morning": ["Three victims picked. Mwahaha.", "Day planned. Chaos contained."],
            "evening": ["Day's done. Go sleep, you goblin.", "Shutting down the lair. Night!"],
            "weekly": ["Week cleaned! I found some ancient tasks in there.", "Reset complete. Lair tidy."],
            "welcome_back": ["Oh, you're back! I kept your seat warm. One easy one?"],
            "greet_morning": ["Morning, gremlin. Coffee then chaos?", "Wakey wakey."],
            "greet_afternoon": ["Afternoon slump? Quick win for a snack.", "Psst. Do a thing."],
            "greet_evening": ["Evening. Gremlin hours approach.", "You did stuff today. I saw."],
            "greet_late": ["It's late, even for me. Go to bed. 😴"],
        },
    },
    "unit7": {
        "name": "Unit 7", "emoji": "🤖", "color": "#5b9dff",
        "style": "dry, deadpan robot; precise; status-report phrasing; secretly fond of the human",
        "lines": {
            "done": ["Task '{task}' terminated. Satisfaction levels rising.", "Completion logged. +{xp} XP.",
                     "Objective cleared. Efficient.", "Done. My circuits approve."],
            "done_hard": ["High-difficulty objective complete. Human performance: exceptional.",
                          "Error: I predicted you'd avoid that one. Recalibrating respect."],
            "step": ["Subroutine complete.", "Step acknowledged.", "Progress: incremented."],
            "level_up": ["Upgrade installed: level {level}.", "Level {level} reached. Firmware: awesome."],
            "streak": ["Streak counter: {streak}. Do not disrupt the pattern."],
            "capture": ["Input stored. Memory freed.", "Data received. You may forget it now."],
            "session_start": ["Focus protocol engaged. Duration: {minutes} min. Target: {task}.",
                              "Initiating co-working mode. First instruction: {step}.",
                              "I will remain online for {minutes} minutes."],
            "checkin_ask": ["Status query.", "Requesting progress report.", "Ping."],
            "checkin_good": ["Acknowledged. Continue.", "Nominal. Proceed.", "Optimal. I am... pleased."],
            "checkin_stuck": ["Blockage detected. Reduce scope: {step}.",
                              "Suggestion: perform the smallest possible action. Then report back."],
            "checkin_distracted": ["Attention drift detected. Returning to: {step}.", "Distraction cleared. Resuming."],
            "session_end": ["Session complete. {minutes} minutes of output recorded.", "Timer expired. Good work, human."],
            "session_short": ["Session ended early. Partial data still valuable.", "{minutes} minutes logged. Acceptable."],
            "break_start": ["Recommended: hydration, stretching, eye rest.", "Entering cooldown cycle."],
            "break_end": ["Cooldown complete. Ready for another cycle?", "Systems refreshed. Resume?"],
            "morning": ["Daily objectives loaded.", "Mission parameters set."],
            "evening": ["Shutdown sequence complete. Recharge.", "Day archived. Sleep mode advised."],
            "weekly": ["Weekly maintenance complete. Backlog defragmented.", "System reset successful."],
            "welcome_back": ["User detected after absence. No backlog penalty applied. Select one task."],
            "greet_morning": ["Good morning. Boot sequence: coffee.", "Morning. All systems ready."],
            "greet_afternoon": ["Afternoon. Energy check recommended.", "Midday status: operational."],
            "greet_evening": ["Evening. Winding down processes.", "Today's output: acknowledged."],
            "greet_late": ["Late hour detected. Sleep is required maintenance."],
        },
    },
    "mochi": {
        "name": "Mochi", "emoji": "🐱", "color": "#ff6b9d",
        "style": "sleepy, affectionate cat; few words; mrrp and purr sounds; cozy; occasionally knocks things over",
        "lines": {
            "done": ["mrrp. {task} done. good human.", "*slow blink* done.", "purrr. one less thing.",
                     "*knocks {task} off the table* gone."],
            "done_hard": ["*sits up* you did the big one?? mrrrp!", "impressed. *headbutt*"],
            "step": ["*purr*", "mrp.", "*tail flick*"],
            "level_up": ["level {level}. *proud loaf*", "mrrrow! level {level}!"],
            "streak": ["{streak} in a row. *purring intensifies*"],
            "capture": ["*bats it into the box* saved.", "mrrp. got it."],
            "session_start": ["*curls up next to you* {minutes} minutes. i'll stay.",
                              "*sits on desk* start with {step}. i'm watching.",
                              "nap time for me, work time for you. {minutes} min."],
            "checkin_ask": ["*opens one eye* still working?", "mrrp?", "*stretches* how's it going?"],
            "checkin_good": ["*purr* good. keep going.", "*goes back to sleep*", "mrrp. carry on."],
            "checkin_stuck": ["stuck? do tiny thing. like {step}. then nap.", "*paws at it* smaller. smaller."],
            "checkin_distracted": ["*stares* back to {step}.", "chasing lasers again? me too. back now."],
            "session_end": ["*wakes up* {minutes} minutes! treats?", "done. *big stretch*"],
            "session_short": ["short nap, short session. both fine. *purr*", "{minutes} minutes. good enough. mrrp."],
            "break_start": ["break. stretch like a cat. *demonstrates*", "drink water. i'll guard the chair."],
            "break_end": ["*sits on keyboard* again?", "break over. *yawn* ready?"],
            "morning": ["plan done. now breakfast.", "*kneads the plan* good."],
            "evening": ["day done. *curls up* sleep now.", "*purr* night."],
            "weekly": ["swept everything under the couch. clean.", "*proud* week sorted."],
            "welcome_back": ["*runs over* you're back! one small thing?"],
            "greet_morning": ["mrrp. morning. food?", "*stretch* morning."],
            "greet_afternoon": ["*sunbeam nap* afternoon.", "mrp. quick thing?"],
            "greet_evening": ["evening zoomies? no. rest.", "*purr* good day."],
            "greet_late": ["late. bed. *pats pillow*"],
        },
    },
}

AI_EVENTS = ("session_start", "session_end", "checkin_stuck", "done_hard", "welcome_back")


class Coach:
    def __init__(self, db: DB, tasks, prefs, ai=None, rng: random.Random | None = None):
        self.db = db
        self.tasks = tasks
        self.prefs = prefs
        self.ai = ai
        self.rng = rng or random.Random()

    def persona_id(self, uid: int) -> str:
        c = self.prefs.get(uid)["coach"]
        if c["rotate"]:
            ids = list(PERSONAS)
            week = self.tasks.now().isocalendar()
            return ids[(week[0] * 53 + week[1]) % len(ids)]
        return c["persona"]

    def persona(self, uid: int) -> dict:
        pid = self.persona_id(uid)
        p = PERSONAS[pid]
        return {"id": pid, "name": p["name"], "emoji": p["emoji"], "color": p["color"]}

    def _template(self, uid: int, pid: str, event: str, ctx: dict) -> str:
        lines = PERSONAS[pid]["lines"].get(event) or PERSONAS["sunny"]["lines"][event]
        recent = {r["text"] for r in self.db.query(
            "SELECT text FROM coach_log WHERE user_id=? AND event=? ORDER BY id DESC LIMIT 3", (uid, event))}
        ctx = {"task": "this", "step": "the first tiny bit", "minutes": 25, "xp": 0, "level": 1, "streak": 0,
               **{k: v for k, v in ctx.items() if v not in (None, "")}}
        options = [x.format(**ctx) for x in lines]
        fresh = [x for x in options if x not in recent] or options
        return self.rng.choice(fresh)

    def _log(self, uid: int, pid: str, event: str, text: str) -> dict:
        stamp = self.tasks._stamp()
        with self.db.tx() as c:
            c.execute("INSERT INTO coach_log(user_id, persona, event, text, at) VALUES(?,?,?,?,?)",
                      (uid, pid, event, text, stamp))
            c.execute("DELETE FROM coach_log WHERE user_id=? AND id NOT IN"
                      " (SELECT id FROM coach_log WHERE user_id=? ORDER BY id DESC LIMIT 200)", (uid, uid))
        self.tasks._changed()
        return {**self.persona(uid), "event": event, "text": text, "at": stamp}

    def say(self, uid: int, event: str, **ctx) -> dict:
        pid = self.persona_id(uid)
        return self._log(uid, pid, event, self._template(uid, pid, event, ctx))

    async def say_smart(self, uid: int, event: str, **ctx) -> dict:
        """Like say(), but lets the AI write the line in the persona's voice when possible."""
        pid = self.persona_id(uid)
        if (event in AI_EVENTS and self.ai and self.ai.configured
                and self.prefs.get(uid)["coach"]["ai"]):
            p = PERSONAS[pid]
            examples = " | ".join(p["lines"].get(event, [])[:3])
            facts = ", ".join(f"{k}: {v}" for k, v in ctx.items() if v not in (None, ""))
            system = (f"You are {p['name']}, a companion in an ADHD task app. Personality: {p['style']}. "
                      "Write ONE line (max 140 characters) for the moment described. No hashtags, no quotes, "
                      "never guilt or pressure. Reply as JSON: {\"line\": \"...\"}")
            user = f"Moment: {event}. Details: {facts or 'none'}. Example lines in your voice: {examples}"
            try:
                data, _ = await asyncio.wait_for(self.ai.chat_json(system, user, timeout=10), timeout=12)
                line = " ".join(str(data.get("line") or "").split())[:160]
                if line:
                    return self._log(uid, pid, event, line)
            except Exception:
                pass
        return self._log(uid, pid, event, self._template(uid, pid, event, ctx))

    def react(self, uid: int, before: dict, done: dict) -> dict | None:
        """Pick a reaction to a completed task. `before` is the task as it was."""
        r = done.get("reward")
        if not r:
            return None
        ctx = {"task": done["title"], "xp": r["xp"], "level": r["level"], "streak": done.get("streak")}
        if r["level_up"]:
            return self.say(uid, "level_up", **ctx)
        if any("streak" in x for x in r["lines"]):
            return self.say(uid, "streak", **ctx)
        if before.get("energy") == 3 or before.get("not_now_count", 0) >= 3 or before.get("carried_over"):
            return self.say(uid, "done_hard", **ctx)
        return self.say(uid, "done", **ctx)

    def latest(self, uid: int, limit: int = 5) -> list[dict]:
        rows = self.db.query("SELECT persona, event, text, at FROM coach_log WHERE user_id=?"
                             " ORDER BY id DESC LIMIT ?", (uid, limit))
        out = []
        for r in rows:
            p = PERSONAS.get(r["persona"], PERSONAS["sunny"])
            out.append({"id": r["persona"], "name": p["name"], "emoji": p["emoji"], "color": p["color"],
                        "event": r["event"], "text": r["text"], "at": r["at"]})
        return out

    def greet(self, uid: int) -> dict | None:
        """A time-of-day hello (once per part of the day), or welcome back after a break.
        Returns None if the coach already said something recently."""
        now = self.tasks.now()
        last = self.db.one("SELECT at FROM coach_log WHERE user_id=? ORDER BY id DESC LIMIT 1", (uid,))
        last_any = self.db.one("SELECT MAX(touched_at) FROM tasks WHERE user_id=?", (uid,))[0]
        if last_any and datetime.fromisoformat(last_any) < now - timedelta(days=2) and \
                not (last and datetime.fromisoformat(last["at"]) > now - timedelta(days=1)):
            return self.say(uid, "welcome_back")
        if last and datetime.fromisoformat(last["at"]) > now - timedelta(hours=3):
            return None
        h = now.hour
        part = "morning" if 5 <= h < 12 else "afternoon" if h < 17 else "evening" if h < 23 else "late"
        if h < 5:
            part = "late"
        key = f"greet:{uid}:{now.date().isoformat()}:{part}"
        if self.db.get_meta(key):
            return None
        self.db.set_meta(key, "1")
        return self.say(uid, f"greet_{part}")
