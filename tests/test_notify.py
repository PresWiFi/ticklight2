import asyncio
import random
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from app import actions
from app.auth import Users
from app.db import DB
from app.notify import Scheduler, plan
from app.prefs import DEFAULTS, Prefs, clean
from app.tasks import Tasks

TZ = ZoneInfo("America/Denver")


class Clock:
    def __init__(self, dt):
        self.dt = dt

    def __call__(self):
        return self.dt

    def set(self, hm, days=0):
        h, m = map(int, hm.split(":"))
        self.dt = (self.dt + timedelta(days=days)).replace(hour=h, minute=m)


@pytest.fixture
def env():
    clock = Clock(datetime(2026, 11, 2, 7, 0, tzinfo=TZ))  # Monday
    db = DB(":memory:")
    tasks = Tasks(db, TZ, clock, rng=random.Random(0))
    uid = Users(db, clock).owner()
    prefs = Prefs(db, "08:00")
    # Routines off unless a test turns them on, to keep plans small
    prefs.update(uid, {"morning": {"enabled": False}, "evening": {"enabled": False},
                       "weekly": {"enabled": False}})
    return tasks, prefs, clock, uid


def keys(tasks, prefs, uid):
    return [n.key.rsplit(":", 1)[-1] for n in plan(tasks, prefs, uid)]


def test_prefs_validation():
    p = clean({"morning": {"time": "7:30"}, "evening": {"time": "25:00"},
               "nudges": [{"id": "Meds!", "label": "Pills", "kind": "meds", "times": "08:00, 20:00",
                           "enabled": True}, {"id": "", "label": "x"}]})
    assert p["morning"]["time"] == "07:30"
    assert p["evening"]["time"] == DEFAULTS["evening"]["time"]
    assert p["nudges"] == [{"id": "meds", "kind": "meds", "label": "Pills", "enabled": True,
                            "times": ["08:00", "20:00"], "follow_up_minutes": 15, "max_follow_ups": 0}]


def test_task_reminder_stages(env):
    tasks, prefs, clock, uid = env
    t = tasks.quick_add(uid, "dentist at 3pm")
    clock.set("14:44")
    assert keys(tasks, prefs, uid) == []
    clock.set("14:45")
    assert keys(tasks, prefs, uid) == ["lead"]
    clock.set("15:00")
    assert keys(tasks, prefs, uid) == ["at"]
    clock.set("15:16")
    assert keys(tasks, prefs, uid) == ["at", "f1"]
    tasks.not_now(uid, t["id"])
    assert keys(tasks, prefs, uid) == ["at"]  # no nagging after "not now"
    tasks.complete(uid, t["id"])
    assert keys(tasks, prefs, uid) == []


def test_hyperfocus_lets_only_critical_through(env):
    tasks, prefs, clock, uid = env
    prefs.update(uid, {"nudges": [{"id": "water", "kind": "water", "label": "Water", "enabled": True,
                                   "every_minutes": 60, "from": "09:00", "to": "21:00"}]})
    tasks.quick_add(uid, "standup at 10am")
    clock.set("09:45")
    tasks.set_hyperfocus(uid, 60)
    notes = plan(tasks, prefs, uid)
    assert [n.title.startswith("🧭") for n in notes] == [True]  # lead reminder, worded as "surface"
    clock.set("10:46")  # hyperfocus over: end note + water come back
    titles = " ".join(n.title for n in plan(tasks, prefs, uid))
    assert "💧" in titles and "🎧" in titles


def test_meds_follow_up_until_taken(env):
    tasks, prefs, clock, uid = env
    prefs.update(uid, {"nudges": [{"id": "meds", "kind": "meds", "label": "Meds", "enabled": True,
                                   "times": ["08:00"], "follow_up_minutes": 15, "max_follow_ups": 3}]})
    clock.set("08:20")
    assert keys(tasks, prefs, uid) == ["0", "1"]
    r = asyncio.run(actions.apply(actions.Ctx(tasks, prefs), uid, "took:meds"))
    assert r["ok"] and "+5 XP" in r["message"]
    assert keys(tasks, prefs, uid) == []


def test_routines(env):
    tasks, prefs, clock, uid = env
    prefs.update(uid, {"morning": {"enabled": True, "time": "08:00"},
                       "weekly": {"enabled": True, "day": 0, "time": "18:00"}})
    tasks.quick_add(uid, "pay rent today")
    clock.set("08:05")
    [n] = plan(tasks, prefs, uid)
    assert n.key == "routine:morning:2026-11-02" and "pay rent" in n.body
    assert n.actions[0] == ("☀️ Plan my day", "url:/?routine=morning")
    tasks.checkin(uid, "routine:morning")
    assert plan(tasks, prefs, uid) == []
    clock.set("18:30")
    assert [n.key for n in plan(tasks, prefs, uid)] == ["routine:weekly:2026-11-02"]
    clock.set("18:30", days=1)  # Tuesday: no weekly
    assert all("weekly" not in n.key for n in plan(tasks, prefs, uid))


def test_scheduler_sends_once_to_each_channel(env):
    tasks, prefs, clock, uid = env
    got = []

    class Fake:
        async def send(self, uid, note, p):
            got.append(note.key)

    s = Scheduler(tasks, prefs)
    s.channels += [Fake(), Fake()]
    tasks.quick_add(uid, "call at 9am")
    clock.set("09:00")
    asyncio.run(s.tick())
    asyncio.run(s.tick())
    assert len(got) == 2 and got[0].endswith(":at")


def test_wording_varies(env):
    tasks, prefs, clock, uid = env
    for i in range(6):
        tasks.quick_add(uid, f"thing {i} at 10am")
    clock.set("10:00")
    titles = {n.title.split(" thing")[0] for n in plan(tasks, prefs, uid)}
    assert len(titles) > 1


def test_pins(env):
    tasks, prefs, clock, uid = env
    ts = [tasks.create(uid, f"t{i}") for i in range(4)]
    for t in ts:
        clock.dt += timedelta(seconds=1)
        tasks.pin(uid, t["id"])
    pinned = [t["title"] for t in tasks.open_tasks(uid) if t["pinned"]]
    assert pinned == ["t1", "t2", "t3"]  # max three; oldest pick dropped
    assert tasks.focus(uid)[0]["focus_why"][0] == "today's pick"
    later = tasks.quick_add(uid, "dentist next month")
    tasks.pin(uid, later["id"])
    assert later["id"] in [t["id"] for t in tasks.focus(uid, 5)]


def test_weekly_review_items(env):
    tasks, prefs, clock, uid = env
    old = tasks.quick_add(uid, "call bank today")
    pushed = tasks.create(uid, "garage")
    for _ in range(3):
        tasks.not_now(uid, pushed["id"])
    tasks.to_someday(uid, tasks.create(uid, "learn piano")["id"])
    clock.set("09:00", days=2)
    w = tasks.weekly(uid)
    assert [(t["title"], t["review_why"]) for t in w["items"]] == [
        ("call bank", "carried over"), ("garage", "keeps getting pushed"), ("learn piano", "someday")]
    t = tasks.move_to(uid, old["id"], "tomorrow")
    assert t["due_date"] == "2026-11-05"


def test_pending_for_dashboard(env):
    from app.notify import pending
    tasks, prefs, clock, uid = env
    prefs.update(uid, {"evening": {"enabled": True, "time": "21:00"},
                       "nudges": [{"id": "meds", "kind": "meds", "label": "Meds", "enabled": True,
                                   "times": ["08:00", "20:00"]}]})
    clock.set("07:59")
    assert pending(tasks, prefs, uid) == {"nudges": [], "routines": []}
    clock.set("09:00")
    assert pending(tasks, prefs, uid)["nudges"] == [{"id": "meds", "label": "Meds", "kind": "meds"}]
    tasks.checkin(uid, "meds")
    clock.set("21:10")
    assert pending(tasks, prefs, uid) == {"nudges": [{"id": "meds", "label": "Meds", "kind": "meds"}],
                                          "routines": ["evening"]}
