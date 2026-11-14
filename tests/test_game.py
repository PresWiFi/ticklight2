import random
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from app.auth import Users
from app.db import DB
from app.game import CATALOG, Game
from app.tasks import Tasks

TZ = ZoneInfo("America/Denver")


class Clock:
    def __init__(self, dt):
        self.dt = dt

    def __call__(self):
        return self.dt

    def advance(self, **kw):
        self.dt += timedelta(**kw)


class NoLuck(random.Random):
    def random(self):
        return 0.99


@pytest.fixture
def env():
    clock = Clock(datetime(2026, 11, 2, 9, 0, tzinfo=TZ))  # Monday
    db = DB(":memory:")
    tasks = Tasks(db, TZ, clock, rng=NoLuck())
    uid = Users(db, clock).owner()
    return tasks, Game(db, tasks), clock, uid


def give(game, uid, n):
    with game.db.tx() as c:
        game._coin(c, uid, n, "test")


def test_coins_from_tasks_and_undo(env):
    tasks, game, clock, uid = env
    t = tasks.create(uid, "x")
    r = tasks.complete(uid, t["id"])["reward"]
    assert r["coins"] >= 1 and game.coins(uid) == r["coins"]
    tasks.undo_complete(uid, t["id"])
    assert game.coins(uid) == 0


def test_shop_rules(env):
    tasks, game, clock, uid = env
    with pytest.raises(ValueError, match="level"):
        give(game, uid, 500); game.buy(uid, "crown")
    with pytest.raises(ValueError, match="can't be bought"):
        game.buy(uid, "medal")
    with pytest.raises(ValueError, match="seasonal"):
        game.buy(uid, "witch_hat")  # November: not spooky season
    s = game.buy(uid, "blush")
    assert s["coins"] == 470 and "blush" in game.owned(uid)
    with pytest.raises(ValueError, match="already"):
        game.buy(uid, "blush")
    game.equip(uid, "blush")
    assert game.summary(uid)["equipped"] == {"face": "blush"}
    game.equip(uid, None, "face")
    assert game.summary(uid)["equipped"] == {}
    with pytest.raises(ValueError):
        game.equip(uid, "crown")
    for _ in range(2):
        game.buy(uid, "freeze")
    with pytest.raises(ValueError, match="at most"):
        game.buy(uid, "freeze")
    assert game.summary(uid)["freezes"] == 3


def test_seasonal_event_and_wraparound(env):
    tasks, game, clock, uid = env
    assert game.event() is None
    clock.dt = datetime(2026, 12, 20, 9, tzinfo=TZ)
    assert game.event()["id"] == "frost"
    clock.dt = datetime(2027, 1, 3, 9, tzinfo=TZ)
    assert game.event()["id"] == "frost"
    give(game, uid, 100)
    game.buy(uid, "santa_hat")
    clock.dt = datetime(2027, 2, 1, 9, tzinfo=TZ)
    ids = {i["id"] for i in game.full(uid)["items"]}
    assert "santa_hat" in ids and "witch_hat" not in ids  # owned seasonal items stay visible


def test_themes(env):
    tasks, game, clock, uid = env
    assert game.summary(uid)["theme"] is None
    with pytest.raises(ValueError):
        game.set_theme(uid, "forest")
    give(game, uid, 300)
    with tasks.db.tx() as c:  # reach level 3
        c.execute("INSERT INTO xp_events(user_id, amount, reason, created_at) VALUES(?,?,?,?)",
                  (uid, 200, "test", tasks._stamp()))
    game.buy(uid, "forest"); game.buy(uid, "ocean")
    game.set_theme(uid, "ocean")
    assert game.summary(uid)["theme"] == "ocean"
    game.set_theme(uid, "weekly")
    seen = set()
    for _ in range(3):
        seen.add(game.summary(uid)["theme"]); clock.advance(days=7)
    assert seen == {"forest", "ocean"}


def test_streak_with_freeze(env):
    tasks, game, clock, uid = env
    for _ in range(3):
        tasks.complete(uid, tasks.create(uid, "x")["id"])
        game.rollover(uid)
        clock.advance(days=1)
    assert game.streak(uid) == 3  # today (day 4) not done yet: counts to yesterday
    clock.advance(days=1)  # missed day 4
    assert game.rollover(uid) == ["2026-11-05"]
    assert game.streak(uid) == 4 and game.summary(uid)["freezes"] == 0
    clock.advance(days=2)  # missed again, no freezes left
    assert game.rollover(uid) == []
    assert game.streak(uid) == 0


def test_weekly_challenges(env):
    tasks, game, clock, uid = env
    ch = game.challenges(uid)
    assert len(ch) == 3 and len({c["kind"] for c in ch}) == 3
    assert game.challenges(uid) == ch  # stable within the week
    with pytest.raises(ValueError, match="isn't finished"):
        game.claim(uid, 0)
    # force a tasks challenge and finish it
    with tasks.db.tx() as c:
        c.execute("UPDATE challenges SET kind='tasks', target=2 WHERE slot=0")
    for _ in range(2):
        tasks.complete(uid, tasks.create(uid, "x")["id"])
    c0 = game.challenges(uid)[0]
    assert c0["complete"] and game.summary(uid)["claimable"] >= 1
    before = game.coins(uid)
    r = game.claim(uid, 0)
    assert game.coins(uid) == before + r["coins"]
    with pytest.raises(ValueError, match="already"):
        game.claim(uid, 0)
    clock.advance(days=7)
    assert all(not c["claimed"] for c in game.challenges(uid))  # new week, new set


@pytest.mark.parametrize("kind", ["tasks", "hard", "sessions", "minutes", "mornings", "evenings", "steps",
                                  "areas", "early", "pinned"])
def test_every_challenge_kind_counts(env, kind):
    tasks, game, clock, uid = env
    assert game._progress(uid, kind, game._week(), game._week() + timedelta(days=7)) == 0


def test_badges_and_rewards(env):
    tasks, game, clock, uid = env
    clock.dt = clock.dt.replace(hour=6)
    t = tasks.create(uid, "x", energy=3)
    tasks.complete(uid, t["id"])
    new = {b["key"] for b in game.check_badges(uid)}
    assert {"first_task", "early_bird"} <= new
    assert game.check_badges(uid) == []
    assert {b["key"] for b in game.unseen_badges(uid)} == new
    game.mark_seen(uid)
    assert game.unseen_badges(uid) == []
    for _ in range(14):
        clock.advance(days=1)
        tasks.complete(uid, tasks.create(uid, "y")["id"])
    new = {b["key"] for b in game.check_badges(uid)}
    assert {"streak_7", "streak_14", "tasks_10"} <= new
    assert "fire" in game.owned(uid)


def test_catalog_is_consistent():
    for iid, it in CATALOG.items():
        assert it["kind"] in ("accessory", "theme"), iid
        if it["kind"] == "accessory":
            assert it["slot"] in ("head", "face", "neck", "aura"), iid
