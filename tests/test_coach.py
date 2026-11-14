import asyncio
import random
import re
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from app import actions
from app.auth import Users
from app.coach import PERSONAS, Coach
from app.db import DB
from app.notify import plan
from app.prefs import Prefs
from app.sessions import Sessions
from app.tasks import Tasks

TZ = ZoneInfo("America/Denver")
EVENTS = set(PERSONAS["sunny"]["lines"])


class Clock:
    def __init__(self, dt):
        self.dt = dt

    def __call__(self):
        return self.dt

    def advance(self, **kw):
        self.dt += timedelta(**kw)


@pytest.fixture
def env():
    clock = Clock(datetime(2026, 11, 2, 10, 0, tzinfo=TZ))
    db = DB(":memory:")
    tasks = Tasks(db, TZ, clock, rng=random.Random(3))
    uid = Users(db, clock).owner()
    prefs = Prefs(db, "")
    prefs.update(uid, {"evening": {"enabled": False}, "weekly": {"enabled": False}})
    coach = Coach(db, tasks, prefs, rng=random.Random(1))
    return tasks, prefs, coach, Sessions(db, tasks, prefs, coach), clock, uid


def test_every_persona_covers_every_event_and_formats():
    ctx = {"task": "T", "step": "S", "minutes": 5, "xp": 1, "level": 2, "streak": 3}
    for pid, p in PERSONAS.items():
        assert set(p["lines"]) == EVENTS, pid
        for event, lines in p["lines"].items():
            assert lines, (pid, event)
            for line in lines:
                assert "{" not in line.format(**ctx), (pid, event, line)
                assert len(re.findall(r"\{(\w+)\}", line)) == len(set(re.findall(r"\{(\w+)\}", line)) & set(ctx))


def test_no_immediate_repeats_and_persona_switch(env):
    tasks, prefs, coach, _, _, uid = env
    said = [coach.say(uid, "done", task="x")["text"] for _ in range(4)]
    assert len(set(said)) == 4  # sunny has 4 "done" lines
    prefs.update(uid, {"coach": {"persona": "unit7"}})
    assert coach.say(uid, "capture")["name"] == "Unit 7"
    prefs.update(uid, {"coach": {"persona": "nope"}})
    assert coach.persona(uid)["id"] == "sunny"


def test_weekly_rotation(env):
    tasks, prefs, coach, _, clock, uid = env
    prefs.update(uid, {"coach": {"rotate": True}})
    seen = set()
    for _ in range(len(PERSONAS)):
        seen.add(coach.persona_id(uid))
        clock.advance(days=7)
    assert len(seen) == len(PERSONAS)


def test_react_picks_the_moment(env):
    tasks, prefs, coach, _, _, uid = env
    hard = tasks.create(uid, "taxes", energy=3)
    before = tasks.get(uid, hard["id"])
    assert coach.react(uid, before, tasks.complete(uid, hard["id"]))["event"] in ("done_hard", "level_up")
    easy = tasks.create(uid, "bins", energy=1)
    before = tasks.get(uid, easy["id"])
    assert coach.react(uid, before, tasks.complete(uid, easy["id"]))["event"] in ("done", "level_up")


def test_greet_once_per_part_of_day(env):
    tasks, prefs, coach, _, clock, uid = env
    assert coach.greet(uid)["event"] == "greet_morning"
    assert coach.greet(uid) is None
    clock.advance(hours=5)  # 15:00
    assert coach.greet(uid)["event"] == "greet_afternoon"


def test_ai_line_with_fallback(env):
    tasks, prefs, coach, _, _, uid = env

    class FakeAI:
        configured = True
        calls = 0

        async def chat_json(self, system, user, timeout=10):
            FakeAI.calls += 1
            assert "Sunny" in system and "session_start" in user
            return {"line": "  Let's   go,   you! "}, "fake"
    coach.ai = FakeAI()
    line = asyncio.run(coach.say_smart(uid, "session_start", task="x", minutes=25))
    assert line["text"] == "Let's go, you!"

    class Broken(FakeAI):
        async def chat_json(self, *a, **k):
            raise RuntimeError("down")
    coach.ai = Broken()
    assert asyncio.run(coach.say_smart(uid, "session_start", task="x", minutes=25))["text"]
    prefs.update(uid, {"coach": {"ai": False}})
    coach.ai = FakeAI()
    FakeAI.calls = 0
    asyncio.run(coach.say_smart(uid, "session_start"))
    assert FakeAI.calls == 0


def test_session_lifecycle(env):
    tasks, prefs, coach, S, clock, uid = env
    t = tasks.create(uid, "write report", steps=["open doc", "write intro"])
    s = asyncio.run(S.start(uid, 30, t["id"]))
    assert s["state"] == "running" and s["coach"]["event"] == "session_start"
    assert "open doc" in s["coach"]["text"] or s["coach"]["text"]
    assert tasks.hyperfocus_until(uid)  # quiet during sessions
    assert s["next_checkin_at"] == (clock.dt + timedelta(minutes=10)).isoformat(timespec="seconds")
    clock.advance(minutes=10)
    s = asyncio.run(S.checkin(uid, s["id"], "stuck"))
    assert s["coach"]["event"] == "checkin_stuck" and len(s["checkins"]) == 1
    assert s["next_checkin_at"] == (clock.dt + timedelta(minutes=10)).isoformat(timespec="seconds")
    tasks.update_subtask(uid, t["subtasks"][0]["id"], done=True)
    s = S.extend(uid, s["id"], 5)
    assert s["minutes"] == 35
    clock.advance(minutes=25)
    keys = [n.key for n in plan(tasks, prefs, uid, S)]
    assert f"session:{s['id']}:end:35" in keys and not any(k.startswith("hf_end") for k in keys)
    s = S.end(uid, s["id"], "done", complete_task=True)
    assert s["outcome"] == "done" and s["xp"] == 45 and s["steps_done"] == 1
    assert s["reward"]["xp"] > 0 and tasks.get(uid, t["id"])["status"] == "done"
    assert tasks.hyperfocus_until(uid) is None
    assert S.minutes_today(uid) == 35
    s = S.start_break(uid, s["id"], 5)
    assert S.active(uid)["state"] == "break"
    clock.advance(minutes=5)
    assert [n.key.split(":")[0] for n in plan(tasks, prefs, uid, S)] == ["break"]
    clock.advance(minutes=1)
    assert S.active(uid) is None


def test_short_session_is_still_praised(env):
    tasks, prefs, coach, S, clock, uid = env
    s = asyncio.run(S.start(uid, 25, intention="tidy desk"))
    clock.advance(minutes=6)
    s = S.end(uid, s["id"], "stopped")
    assert s["coach"]["event"] == "session_short" and s["xp"] == 6


def test_forgotten_session_times_out(env):
    tasks, prefs, coach, S, clock, uid = env
    asyncio.run(S.start(uid, 25))
    clock.advance(minutes=90)
    assert S.active(uid) is None
    assert S.minutes_today(uid) == 55  # capped at planned + 30


def test_session_actions_from_notifications(env):
    tasks, prefs, coach, S, clock, uid = env
    ctx = actions.Ctx(tasks, prefs, S, coach)
    t = tasks.create(uid, "email Sam")
    s = asyncio.run(S.start(uid, 15, t["id"]))
    clock.advance(minutes=15)
    r = asyncio.run(actions.apply(ctx, uid, f"session:break:{s['id']}"))
    assert r["ok"] and r["message"].startswith("☕")
    r = asyncio.run(actions.apply(ctx, uid, f"session:again:{s['id']}"))
    assert r["ok"] and S.active(uid)["task_id"] == t["id"] and S.active(uid)["id"] != s["id"]
    r = asyncio.run(actions.apply(ctx, uid, f"done:{t['id']}"))
    assert r["coach"] and r["coach"]["text"] in r["message"]
