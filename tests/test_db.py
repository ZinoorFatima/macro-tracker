"""Connection lifecycle.

Regression cover for a bug that only showed up in the browser: FastAPI runs sync
endpoints in a worker threadpool and does not guarantee that a dependency's
setup, the endpoint body, and the dependency's teardown run on the same thread.
With SQLite's default `check_same_thread=True` that raised `ProgrammingError`
intermittently -- the History page 500'd on roughly every other load.
"""

import sqlite3
import threading

import pytest

from app import db as app_db
from tests.conftest import TODAY, meal_payload

pytestmark = pytest.mark.usefixtures("today")


class TestConnection:
    def test_rows_come_back_as_mappings(self, clean_db):
        conn = app_db.connect()
        try:
            row = conn.execute("SELECT 1 AS one").fetchone()
            assert row["one"] == 1
        finally:
            conn.close()

    def test_wal_is_enabled(self, clean_db):
        conn = app_db.connect()
        try:
            assert conn.execute("PRAGMA journal_mode").fetchone()[0].lower() == "wal"
        finally:
            conn.close()

    def test_foreign_keys_are_on(self, clean_db):
        conn = app_db.connect()
        try:
            assert conn.execute("PRAGMA foreign_keys").fetchone()[0] == 1
        finally:
            conn.close()

    def test_a_connection_can_be_used_from_another_thread(self, clean_db):
        """The exact condition that broke the app under uvicorn."""
        conn = app_db.connect()
        results = []

        def query():
            try:
                results.append(conn.execute("SELECT 1").fetchone()[0])
            except sqlite3.ProgrammingError as e:
                results.append(e)

        thread = threading.Thread(target=query)
        thread.start()
        thread.join()
        conn.close()
        assert results == [1]

    def test_init_db_is_idempotent(self, clean_db):
        app_db.init_db()
        app_db.init_db()
        conn = app_db.connect()
        try:
            tables = {
                r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
            }
        finally:
            conn.close()
        assert {"profile", "meals", "activities", "weight_logs", "day_ratings"} <= tables


class TestRequestLifecycle:
    def test_a_write_is_committed(self, onboarded):
        onboarded.post("/api/meals", json=meal_payload())
        assert len(onboarded.get(f"/api/meals?date={TODAY}").json()) == 1

    def test_concurrent_reads_all_succeed(self, onboarded):
        """Drives the endpoints that used to 500 from several threads at once."""
        onboarded.post("/api/meals", json=meal_payload())
        onboarded.post("/api/weight", json={"date": TODAY, "weight_kg": 69.5})

        statuses = []
        lock = threading.Lock()

        def hit(path):
            code = onboarded.get(path).status_code
            with lock:
                statuses.append((path, code))

        paths = [
            "/api/weight",
            "/api/days?start=2026-01-01&end=2026-01-31",
            f"/api/days/{TODAY}/summary",
            f"/api/meals?date={TODAY}",
            "/api/profile",
        ] * 4
        threads = [threading.Thread(target=hit, args=(p,)) for p in paths]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()

        failures = [entry for entry in statuses if entry[1] != 200]
        assert not failures, f"{len(failures)} request(s) failed: {failures[:5]}"

    def test_concurrent_writes_all_succeed(self, onboarded):
        statuses = []
        lock = threading.Lock()

        def write(index):
            code = onboarded.post(
                "/api/meals", json=meal_payload(description=f"Meal {index}")
            ).status_code
            with lock:
                statuses.append(code)

        threads = [threading.Thread(target=write, args=(i,)) for i in range(12)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()

        assert statuses == [200] * 12
        assert len(onboarded.get(f"/api/meals?date={TODAY}").json()) == 12
