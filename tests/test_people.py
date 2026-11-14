import asyncio
import random
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from app.ai import AI
from app.auth import Users
from app.capture import Capture
from app.config import Settings
from app.db import DB
from app.game import Game
from app.notify import plan
from app.people import People
from app.prefs import Prefs
from app.tasks import NotFound, Tasks

TZ = ZoneInfo("America/Denver")


class Clock:
    def __init__(self, dt):
        self.dt = dt

    def __call__(self):
        return self.dt


@pytest.fixture
def env():
    clock = Clock(datetime(2026, 11, 2, 10, 0, tzinfo=TZ))
    db = DB(":memory:")
    tasks = Tasks(db, TZ, clock, rng=random.Random(5))
    users = Users(db, clock)
    me = users.owner()
    prefs = Prefs(db, "")
    people = People(db, tasks, prefs, users)
    partner = people.new_member("Sam")
    return tasks, users, prefs, people, me, partner, clock


def link(people, a, b):
    people.redeem(people.invite(a, "Sam")["code"], b)


def test_invite_rules(env):
    tasks, users, prefs, people, me, partner, clock = env
    inv = people.invite(me, "Sam")
    with pytest.raises(ValueError, match="your own"):
        people.redeem(inv["code"], me)
    assert people.redeem(inv["code"], partner) == me
    assert people.linked(me) == [partner] and people.linked(partner) == [me]
    with pytest.raises(ValueError, match="expired"):
        people.redeem(inv["code"], partner)  # single use
    old = people.invite(me)
    clock.dt += timedelta(hours=73)
    assert people.invite_info(old["code"]) is None


def test_private_vs_shared_tasks(env):
    tasks, users, prefs, people, me, partner, clock = env
    link(people, me, partner)
    private = tasks.create(me, "my secret")
    chore = tasks.create(me, "take bins out", shared=True)
    seen = {t["id"] for t in tasks.open_tasks(partner)}
    assert chore["id"] in seen and private["id"] not in seen
    with pytest.raises(NotFound):
        tasks.complete(partner, private["id"])
    done = tasks.complete(partner, chore["id"])  # partner ticks the shared chore
    assert done["reward"]["xp"] > 0
    assert tasks.stats(partner)["xp"] > 0 and tasks.stats(me)["xp"] == 0
    assert [t["title"] for t in tasks.done_on(partner, tasks.today())] == ["take bins out"]
    assert tasks.done_on(me, tasks.today()) == []
    assert Game(tasks.db, tasks).streak(partner) == 1
    people.unlink(me, partner)
    assert tasks.open_tasks(partner) == []


def test_feed_privacy_and_reactions(env):
    tasks, users, prefs, people, me, partner, clock = env
    link(people, me, partner)
    tasks.complete(me, tasks.create(me, "dentist call", energy=3)["id"])
    feed = people.feed(partner)
    assert feed[0]["title"] == "dentist call" and feed[0]["hard"] and not feed[0]["mine"]
    r = people.react(partner, feed[0]["completion_id"], "🎉")
    assert r == {"to": me, "title": "dentist call", "new": True}
    assert people.react(partner, feed[0]["completion_id"], "🎉")["new"] is False
    assert people.feed(me)[0]["reactions"][0]["name"] == "Sam"
    with pytest.raises(ValueError):
        people.react(me, feed[0]["completion_id"], "🎉")  # can't cheer your own
    with pytest.raises(ValueError):
        people.react(partner, feed[0]["completion_id"], "💩")
    prefs.update(me, {"people": {"share_titles": False}})
    assert people.feed(partner)[0]["title"] == "a task"
    prefs.update(me, {"people": {"share_wins": False}})
    assert people.feed(partner) == [] and people.summary(partner)[0]["done_today"] is None


def test_digest_note(env):
    tasks, users, prefs, people, me, partner, clock = env
    link(people, me, partner)
    for i in range(3):
        tasks.complete(me, tasks.create(me, f"t{i}")["id"])
    clock.dt = clock.dt.replace(hour=20, minute=5)
    notes = [n for n in plan(tasks, prefs, partner, people=people) if n.key.startswith("digest")]
    assert len(notes) == 1 and "3 things" in notes[0].title
    prefs.update(partner, {"people": {"digest": False}})
    assert not [n for n in plan(tasks, prefs, partner, people=people) if n.key.startswith("digest")]


def test_send_task_and_share_tags(env):
    tasks, users, prefs, people, me, partner, clock = env
    link(people, me, partner)
    s = Settings(openrouter_key="", local_ai_url="")
    cap = Capture(tasks, AI(s), prefs)
    r = asyncio.run(cap.run(partner, "buy milk tomorrow", "partner", from_user=me))
    t = r["tasks"][0]
    assert t["user_id"] == partner and t["from_user"] == me and t["title"] == "buy milk"
    r = asyncio.run(cap.run(me, "pay rent today\n#home hoover the stairs\nfix the shelf #shared\n  + buy screws", "web"))
    assert [(t["title"], t["shared"]) for t in r["tasks"]] == [
        ("pay rent", False), ("hoover the stairs", True), ("fix the shelf", True)]
    assert r["tasks"][2]["subtasks"][0]["title"] == "buy screws"
    seen = {t["title"] for t in tasks.open_tasks(partner)}
    assert {"hoover the stairs", "fix the shelf"} <= seen and "pay rent" not in seen


def test_telegram_linking(env):
    tasks, users, prefs, people, me, partner, clock = env
    code = users.telegram_code(partner)
    assert users.telegram_link(code, 555) == partner
    assert users.for_telegram(555) == partner
    with pytest.raises(ValueError):
        users.telegram_link(code, 555)


def test_migration_adds_columns(tmp_path):
    import sqlite3
    path = tmp_path / "old.db"
    from app.db import SCHEMA
    new_cols = ("shared         INTEGER NOT NULL DEFAULT 0", "from_user      INTEGER REFERENCES users(id) ON DELETE SET NULL",
                "by_user        INTEGER")
    old_schema = "\n".join(line for line in SCHEMA.splitlines() if not line.strip().startswith(new_cols))
    old_schema = old_schema.replace("prev_subtasks  TEXT,", "prev_subtasks  TEXT")
    con = sqlite3.connect(path)
    con.executescript(old_schema)
    assert "shared" not in {r[1] for r in con.execute("PRAGMA table_info(tasks)")}
    con.close()
    db = DB(str(path))
    cols = {r["name"] for r in db.query("PRAGMA table_info(tasks)")}
    assert {"shared", "from_user"} <= cols
    assert "by_user" in {r["name"] for r in db.query("PRAGMA table_info(completions)")}
