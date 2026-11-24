import os
import sqlite3
import threading
from contextlib import contextmanager

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id             INTEGER PRIMARY KEY,
    name           TEXT NOT NULL DEFAULT '',
    email          TEXT NOT NULL DEFAULT '',
    oidc_sub       TEXT UNIQUE,
    telegram_id    INTEGER UNIQUE,
    energy         INTEGER,                         -- 1 low, 2 medium, 3 high (manual)
    energy_set_at  TEXT,
    dnd_until      TEXT,                            -- hyperfocus: only critical pings until then
    prefs          TEXT NOT NULL DEFAULT '{}',      -- JSON, see prefs.py
    created_at     TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS sessions (
    token_hash  TEXT PRIMARY KEY,
    user_id     INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    created_at  TEXT NOT NULL,
    expires_at  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS api_tokens (
    id            INTEGER PRIMARY KEY,
    user_id       INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    name          TEXT NOT NULL,
    token_hash    TEXT NOT NULL UNIQUE,
    created_at    TEXT NOT NULL,
    last_used_at  TEXT
);

CREATE TABLE IF NOT EXISTS projects (
    id          INTEGER PRIMARY KEY,
    user_id     INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    name        TEXT NOT NULL,
    status      TEXT NOT NULL DEFAULT 'open',       -- open | done
    capture_id  INTEGER REFERENCES captures(id) ON DELETE SET NULL,  -- started by this brain dump
    created_at  TEXT NOT NULL,
    updated_at  TEXT NOT NULL,
    done_at     TEXT
);
CREATE INDEX IF NOT EXISTS idx_projects_user ON projects(user_id, status);

CREATE TABLE IF NOT EXISTS wishes (
    id          INTEGER PRIMARY KEY,
    user_id     INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    title       TEXT NOT NULL,
    price       TEXT NOT NULL DEFAULT '',
    link        TEXT NOT NULL DEFAULT '',
    note        TEXT NOT NULL DEFAULT '',
    status      TEXT NOT NULL DEFAULT 'want',       -- want | got
    capture_id  INTEGER REFERENCES captures(id) ON DELETE SET NULL,
    created_at  TEXT NOT NULL,
    updated_at  TEXT NOT NULL,
    got_at      TEXT
);
CREATE INDEX IF NOT EXISTS idx_wishes_user ON wishes(user_id, status);

CREATE TABLE IF NOT EXISTS tasks (
    id             INTEGER PRIMARY KEY,
    user_id        INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    title          TEXT NOT NULL,
    notes          TEXT NOT NULL DEFAULT '',
    priority       INTEGER NOT NULL DEFAULT 1,      -- 0 low, 1 normal, 2 high
    area           TEXT,                            -- work, home, money, health, people, fun
    energy         INTEGER,                         -- energy needed: 1 low, 2 medium, 3 high
    minutes        INTEGER,                         -- rough estimate
    due_date       TEXT,                            -- YYYY-MM-DD
    due_time       TEXT,                            -- HH:MM
    recurrence     TEXT,                            -- JSON rule, see recurrence.py
    anchor         TEXT,
    status         TEXT NOT NULL DEFAULT 'open',    -- open | someday | done | deleted
    someday_reason TEXT,
    streak         INTEGER NOT NULL DEFAULT 0,
    best_streak    INTEGER NOT NULL DEFAULT 0,
    reminded_for   TEXT,
    not_now_until  TEXT,                            -- hidden from focus until then
    not_now_count  INTEGER NOT NULL DEFAULT 0,
    pinned_on      TEXT,                            -- picked as one of "today's three" for this date
    capture_id     INTEGER REFERENCES captures(id) ON DELETE SET NULL,
    shared         INTEGER NOT NULL DEFAULT 0,      -- visible to linked people (household)
    from_user      INTEGER REFERENCES users(id) ON DELETE SET NULL,  -- sent by someone else
    created_at     TEXT NOT NULL,
    updated_at     TEXT NOT NULL,
    touched_at     TEXT NOT NULL,                   -- last time you did anything with it
    completed_at   TEXT
);
CREATE INDEX IF NOT EXISTS idx_tasks_user_status ON tasks(user_id, status, due_date);

CREATE TABLE IF NOT EXISTS completions (
    id             INTEGER PRIMARY KEY,
    task_id        INTEGER NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
    done_at        TEXT NOT NULL,
    for_date       TEXT,
    skipped        INTEGER NOT NULL DEFAULT 0,
    prev_due       TEXT,
    prev_streak    INTEGER,
    prev_best      INTEGER,
    prev_status    TEXT,
    prev_subtasks  TEXT,
    by_user        INTEGER                          -- who ticked it (shared tasks)
);
CREATE INDEX IF NOT EXISTS idx_completions_task ON completions(task_id, id);
CREATE INDEX IF NOT EXISTS idx_completions_done ON completions(done_at);

CREATE TABLE IF NOT EXISTS subtasks (
    id          INTEGER PRIMARY KEY,
    task_id     INTEGER NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
    title       TEXT NOT NULL,
    done        INTEGER NOT NULL DEFAULT 0,
    position    INTEGER NOT NULL DEFAULT 0,
    created_at  TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_subtasks_task ON subtasks(task_id, position);

CREATE TABLE IF NOT EXISTS captures (
    id          INTEGER PRIMARY KEY,
    user_id     INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    text        TEXT NOT NULL,
    source      TEXT NOT NULL DEFAULT 'web',
    status      TEXT NOT NULL DEFAULT 'pending',    -- pending | done | failed
    method      TEXT,                               -- AI model used, or "parser"
    created_at  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS xp_events (
    id             INTEGER PRIMARY KEY,
    user_id        INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    task_id        INTEGER REFERENCES tasks(id) ON DELETE SET NULL,
    completion_id  INTEGER REFERENCES completions(id) ON DELETE CASCADE,
    amount         INTEGER NOT NULL,
    reason         TEXT NOT NULL,
    created_at     TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_xp_user ON xp_events(user_id, created_at);

CREATE TABLE IF NOT EXISTS checkins (
    id          INTEGER PRIMARY KEY,
    user_id     INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    kind        TEXT NOT NULL,                      -- nudge id (meds, water…) or routine:morning…
    at          TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_checkins ON checkins(user_id, kind, at);

-- Every scheduled notification that went out, so each fires once
CREATE TABLE IF NOT EXISTS sent_log (
    user_id  INTEGER NOT NULL,
    key      TEXT NOT NULL,
    at       TEXT NOT NULL,
    PRIMARY KEY (user_id, key)
);

CREATE TABLE IF NOT EXISTS push_subs (
    id          INTEGER PRIMARY KEY,
    user_id     INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    endpoint    TEXT NOT NULL UNIQUE,
    p256dh      TEXT NOT NULL,
    auth        TEXT NOT NULL,
    label       TEXT NOT NULL DEFAULT '',
    created_at  TEXT NOT NULL
);

-- Short-lived codes for pairing a watch without typing a token
CREATE TABLE IF NOT EXISTS pairings (
    code        TEXT PRIMARY KEY,
    secret      TEXT NOT NULL,                      -- the watch proves it started the pairing
    user_id     INTEGER,
    token       TEXT,                               -- handed to the watch once, then cleared
    expires_at  TEXT NOT NULL
);

-- Focus sessions (body doubling): a timed block on one task with check-ins
CREATE TABLE IF NOT EXISTS focus_sessions (
    id            INTEGER PRIMARY KEY,
    user_id       INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    task_id       INTEGER REFERENCES tasks(id) ON DELETE SET NULL,
    intention     TEXT NOT NULL DEFAULT '',
    minutes       INTEGER NOT NULL,                 -- planned length (grows with "+5 min")
    started_at    TEXT NOT NULL,
    ended_at      TEXT,
    outcome       TEXT,                             -- done | stopped | timeout
    checkins      TEXT NOT NULL DEFAULT '[]',       -- JSON [{at, mood}]
    steps_done    INTEGER NOT NULL DEFAULT 0,
    break_until   TEXT,
    xp            INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_sessions_user ON focus_sessions(user_id, started_at);

-- What the coach said, so every screen shows the same companion
CREATE TABLE IF NOT EXISTS coach_log (
    id         INTEGER PRIMARY KEY,
    user_id    INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    persona    TEXT NOT NULL,
    event      TEXT NOT NULL,
    text       TEXT NOT NULL,
    at         TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_coach_user ON coach_log(user_id, id);

-- ---------- game layer ----------
CREATE TABLE IF NOT EXISTS coin_events (
    id             INTEGER PRIMARY KEY,
    user_id        INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    amount         INTEGER NOT NULL,                -- negative = spent
    reason         TEXT NOT NULL,
    completion_id  INTEGER REFERENCES completions(id) ON DELETE CASCADE,
    created_at     TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_coins_user ON coin_events(user_id);

CREATE TABLE IF NOT EXISTS game_state (
    user_id    INTEGER PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
    inventory  TEXT NOT NULL DEFAULT '[]',          -- item ids owned
    equipped   TEXT NOT NULL DEFAULT '{}',          -- slot -> item id
    freezes    INTEGER NOT NULL DEFAULT 1,          -- streak freezes in stock
    theme      TEXT NOT NULL DEFAULT 'auto'
);

-- Days a streak freeze covered
CREATE TABLE IF NOT EXISTS freeze_days (
    user_id  INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    day      TEXT NOT NULL,
    PRIMARY KEY (user_id, day)
);

CREATE TABLE IF NOT EXISTS challenges (
    user_id     INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    week        TEXT NOT NULL,                      -- Monday's date
    slot        INTEGER NOT NULL,
    kind        TEXT NOT NULL,
    target      INTEGER NOT NULL,
    coins       INTEGER NOT NULL,
    xp          INTEGER NOT NULL,
    claimed_at  TEXT,
    PRIMARY KEY (user_id, week, slot)
);

CREATE TABLE IF NOT EXISTS achievements (
    user_id  INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    key      TEXT NOT NULL,
    at       TEXT NOT NULL,
    seen     INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (user_id, key)
);

-- ---------- people (partner / household) ----------
CREATE TABLE IF NOT EXISTS links (
    user_id     INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    other_id    INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    created_at  TEXT NOT NULL,
    PRIMARY KEY (user_id, other_id)
);

CREATE TABLE IF NOT EXISTS invites (
    code        TEXT PRIMARY KEY,
    from_user   INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    name        TEXT NOT NULL DEFAULT '',
    expires_at  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS reactions (
    id             INTEGER PRIMARY KEY,
    from_user      INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    completion_id  INTEGER NOT NULL REFERENCES completions(id) ON DELETE CASCADE,
    emoji          TEXT NOT NULL,
    at             TEXT NOT NULL,
    UNIQUE (from_user, completion_id, emoji)
);

CREATE TABLE IF NOT EXISTS meta (
    key   TEXT PRIMARY KEY,
    value TEXT
);
"""


class DB:
    def __init__(self, path: str):
        if path != ":memory:":
            os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
        self.conn = sqlite3.connect(path, check_same_thread=False, isolation_level=None)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.execute("PRAGMA foreign_keys=ON")
        self.conn.executescript(SCHEMA)
        self._migrate()
        self.lock = threading.RLock()

    # Columns added after a table first shipped: (table, column, definition)
    COLUMNS = [
        ("tasks", "shared", "INTEGER NOT NULL DEFAULT 0"),
        ("tasks", "from_user", "INTEGER REFERENCES users(id) ON DELETE SET NULL"),
        ("completions", "by_user", "INTEGER"),
        ("tasks", "project_id", "INTEGER REFERENCES projects(id) ON DELETE SET NULL"),
        ("captures", "project_id", "INTEGER REFERENCES projects(id) ON DELETE SET NULL"),
    ]

    def _migrate(self) -> None:
        for table, col, definition in self.COLUMNS:
            have = {r["name"] for r in self.conn.execute(f"PRAGMA table_info({table})")}
            if col not in have:
                self.conn.execute(f"ALTER TABLE {table} ADD COLUMN {col} {definition}")

    @contextmanager
    def tx(self):
        with self.lock:
            self.conn.execute("BEGIN IMMEDIATE")
            try:
                yield self.conn
                self.conn.execute("COMMIT")
            except BaseException:
                self.conn.execute("ROLLBACK")
                raise

    def query(self, sql: str, args=()) -> list[sqlite3.Row]:
        with self.lock:
            return self.conn.execute(sql, args).fetchall()

    def one(self, sql: str, args=()) -> sqlite3.Row | None:
        with self.lock:
            return self.conn.execute(sql, args).fetchone()

    def get_meta(self, key: str) -> str | None:
        row = self.one("SELECT value FROM meta WHERE key=?", (key,))
        return row["value"] if row else None

    def set_meta(self, key: str, value: str) -> None:
        with self.lock:
            self.conn.execute(
                "INSERT INTO meta(key, value) VALUES(?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value", (key, value))
