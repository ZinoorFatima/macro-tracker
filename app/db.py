"""SQLite access: schema, connection lifecycle and pragmas.

One connection per request, opened by the `get_db` dependency and closed when
the request ends. There is no pool: SQLite is a file, opening it is cheap, and a
pool would add the one thing this app has no need for.
"""

import os
import sqlite3
from pathlib import Path

# Overridable so tests can point at a throwaway file and a container can mount a
# volume, without the default local run needing any configuration.
DB_PATH = Path(
    os.environ.get(
        "MACRO_TRACKER_DB",
        Path(__file__).resolve().parent.parent / "macro_tracker.db",
    )
)

# How long a writer waits for a competing write to finish before giving up.
# Without this, a second concurrent write fails instantly with "database is
# locked" rather than waiting the few milliseconds the first one needs.
BUSY_TIMEOUT_MS = 5_000

SCHEMA = """
CREATE TABLE IF NOT EXISTS profile (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    age INTEGER NOT NULL,
    gender TEXT NOT NULL,
    height_cm REAL NOT NULL,
    weight_kg REAL NOT NULL,
    activity_level TEXT NOT NULL,
    goal TEXT NOT NULL,
    timeline_weeks INTEGER,
    target_weight_kg REAL,
    calorie_target INTEGER NOT NULL,
    protein_target_g INTEGER NOT NULL,
    carbs_target_g INTEGER NOT NULL,
    fat_target_g INTEGER NOT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS meals (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    date TEXT NOT NULL,
    meal_type TEXT NOT NULL,
    description TEXT NOT NULL,
    calories INTEGER NOT NULL,
    protein_g REAL NOT NULL,
    carbs_g REAL NOT NULL,
    fat_g REAL NOT NULL,
    items_json TEXT,
    score INTEGER,
    feedback TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_meals_date ON meals(date);

CREATE TABLE IF NOT EXISTS activities (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    date TEXT NOT NULL,
    activity_type TEXT NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    exercises_json TEXT,
    steps INTEGER,
    calories_burned INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_activities_date ON activities(date);

CREATE TABLE IF NOT EXISTS weight_logs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    date TEXT NOT NULL UNIQUE,
    weight_kg REAL NOT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS day_ratings (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    date TEXT NOT NULL UNIQUE,
    score INTEGER NOT NULL,
    summary TEXT NOT NULL,
    progress_note TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
"""


def connect() -> sqlite3.Connection:
    """Open a configured connection.

    `check_same_thread=False` is required, not merely convenient. FastAPI runs
    sync endpoints in a worker threadpool, and a dependency generator's setup,
    the endpoint body, and the generator's teardown are not guaranteed to land
    on the same worker thread. With SQLite's default thread check that raises
    `ProgrammingError` intermittently, depending on which thread the pool picks
    -- which is exactly the kind of failure that shows up in production and not
    in a quick manual test.

    Turning the check off is safe here because a connection is never *shared*:
    each request opens its own, and only one thread touches it at any moment.
    The regression test in `tests/test_db.py` hammers the API from several
    threads at once to keep that true.
    """
    conn = sqlite3.connect(DB_PATH, check_same_thread=False, timeout=BUSY_TIMEOUT_MS / 1000)
    conn.row_factory = sqlite3.Row
    # WAL lets reads proceed while a write is in flight, which matters as soon
    # as a slow AI-backed request overlaps with the dashboard polling.
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA synchronous = NORMAL")
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute(f"PRAGMA busy_timeout = {BUSY_TIMEOUT_MS}")
    return conn


def init_db() -> None:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = connect()
    try:
        conn.executescript(SCHEMA)
        conn.commit()
    finally:
        conn.close()


def get_db():
    """FastAPI dependency: one connection per request, committed on success.

    An exception inside the endpoint propagates into this generator, so the
    rollback branch is what keeps a half-applied multi-statement write from
    being committed.
    """
    conn = connect()
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
