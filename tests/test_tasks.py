import random
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from app.auth import Users
from app.db import DB
from app.tasks import NotFound, Tasks, level_for

TZ = ZoneInfo("Europe/London")


class Clock:
    def __init__(self, dt):
        self.dt = dt

    def __call__(self):
        return self.dt

    def advance(self, **kw):
        self.dt += timedelta(**kw)


class NoLuck(random.Random):
    def random(self):
        return 0.99  # never roll a surprise bonus


@pytest.fixture
def env():
    clock = Clock(datetime(2026, 11, 2, 10, 0, tzinfo=TZ))  # Monday 10:00
    db = DB(":memory:")
    tasks = Tasks(db, TZ, clock, rng=NoLuck(), energy_schedule="00:1,09:3,15:1")
    users = Users(db, clock)
    return tasks, clock, users.owner(), users


def test_tasks_are_scoped_per_user(env):
    tasks, _, uid, users = env
    assert users.upsert_oidc("sub-1", "Me", "") == uid  # first login claims existing data
    other = users.upsert_oidc("sub-2", "Partner", "")
    assert other != uid
    t = tasks.quick_add(uid, "mine")
    with pytest.raises(NotFound):
        tasks.get(other, t["id"])
    assert tasks.open_tasks(other) == []


def test_streak_forgives_one_miss(env):
    tasks, clock, uid, _ = env
    t = tasks.quick_add(uid, "meditate daily")
    t = tasks.complete(uid, t["id"])
    clock.advance(days=2)  # missed the 3rd, now the 4th
    assert tasks.get(uid, t["id"])["current_streak"] == 1
    t = tasks.complete(uid, t["id"])
    assert t["streak"] == 2
    clock.advance(days=3)  # missed two in a row
    assert tasks.get(uid, t["id"])["current_streak"] == 0


def test_xp_and_undo(env):
    tasks, _, uid, _ = env
    t = tasks.create(uid, "file taxes", energy=3, minutes=60, priority=2)
    done = tasks.complete(uid, t["id"])
    r = done["reward"]
    assert r["xp"] == (10 + 15 + 12 + 5) + 10  # base + first of the day
    assert "First one today +10" in r["lines"]
    assert tasks.stats(uid)["xp"] == r["xp"]
    tasks.undo_complete(uid, t["id"])
    assert tasks.stats(uid)["xp"] == 0


def test_surprise_bonus():
    class Lucky(random.Random):
        def random(self):
            return 0.0
    clock = Clock(datetime(2026, 11, 2, 10, 0, tzinfo=TZ))
    db = DB(":memory:")
    uid = Users(db, clock).owner()
    tasks = Tasks(db, TZ, clock, rng=Lucky())
    r = tasks.complete(uid, tasks.create(uid, "x")["id"])["reward"]
    assert r["bonus"] and r["xp"] > 30


def test_step_xp_toggles(env):
    tasks, _, uid, _ = env
    t = tasks.create(uid, "clean desk", steps=["clear cups", "wipe"])
    sid = t["subtasks"][0]["id"]
    t = tasks.update_subtask(uid, sid)
    assert t["next_step"]["title"] == "wipe" and tasks.stats(uid)["xp"] == 3
    tasks.update_subtask(uid, sid)
    assert tasks.stats(uid)["xp"] == 0


def test_levels():
    assert level_for(0)["level"] == 1
    assert level_for(49)["level"] == 1 and level_for(50)["level"] == 2
    assert level_for(150) == {"xp": 150, "level": 3, "into": 0, "needed": 150}


def test_energy_schedule_and_manual(env):
    tasks, clock, uid, _ = env
    assert tasks.energy(uid) == {"level": 3, "name": "high", "manual": False}
    tasks.set_energy(uid, "low")
    assert tasks.energy(uid)["level"] == 1 and tasks.energy(uid)["manual"]
    clock.advance(hours=5)  # manual setting expires; 15:00 schedule says low
    assert tasks.energy(uid) == {"level": 1, "name": "low", "manual": False}


def test_focus_ranking(env):
    tasks, clock, uid, _ = env
    tasks.set_energy(uid, 1)
    hard = tasks.create(uid, "write report", energy=3, minutes=90)
    quick = tasks.create(uid, "reply to Sam", energy=1, minutes=5)
    due = tasks.quick_add(uid, "pay rent today")
    later = tasks.quick_add(uid, "dentist next month")
    ids = [t["id"] for t in tasks.focus(uid)]
    assert ids[:2] == [due["id"], quick["id"]]
    assert later["id"] not in ids
    tasks.not_now(uid, due["id"])
    assert due["id"] not in [t["id"] for t in tasks.focus(uid)]
    clock.advance(hours=4)
    assert due["id"] in [t["id"] for t in tasks.focus(uid)]
    assert hard["id"] in [t["id"] for t in tasks.focus(uid, n=10)]


def test_not_now_suggests_breakdown(env):
    tasks, _, uid, _ = env
    t = tasks.create(uid, "sort garage")
    for _ in range(3):
        t = tasks.not_now(uid, t["id"])
    assert t["suggest_breakdown"]


def test_no_overdue_section_and_tidy(env):
    tasks, clock, uid, _ = env
    old = tasks.quick_add(uid, "call bank today")
    vague = tasks.create(uid, "learn spanish")
    rec = tasks.quick_add(uid, "bins every mon")
    clock.advance(days=2)
    b = tasks.board(uid)
    assert "overdue" not in b
    today = {t["id"]: t for t in b["today"]}
    assert today[old["id"]]["carried_over"]
    clock.advance(days=20)
    b = tasks.board(uid)
    assert b["someday_count"] == 2
    someday = {t["id"]: t["someday_reason"] for t in tasks.tasks_with_status(uid, "someday")}
    assert set(someday) == {old["id"], vague["id"]}
    assert rec["id"] in [t["id"] for t in tasks.open_tasks(uid)]
    back = tasks.bring_back(uid, old["id"], "tomorrow")
    assert back["status"] == "open" and back["due_date"] == "2026-11-25"


def test_replace_steps_keeps_done(env):
    tasks, _, uid, _ = env
    t = tasks.create(uid, "taxes", steps=["find login", "old step"])
    tasks.update_subtask(uid, t["subtasks"][0]["id"], done=True)
    t = tasks.replace_steps(uid, t["id"], ["open portal", "download PDF"])
    assert [s["title"] for s in t["subtasks"]] == ["find login", "open portal", "download PDF"]


def test_future_appointments_stay_out_of_focus(env):
    tasks, _, uid, _ = env
    appt = tasks.quick_add(uid, "dentist tomorrow 3pm")
    chore = tasks.quick_add(uid, "post parcel tomorrow")
    ids = [t["id"] for t in tasks.focus(uid)]
    assert chore["id"] in ids and appt["id"] not in ids
