"""Shared fixtures.

Every test gets its own SQLite file. The app resolves `app.db.DB_PATH` at
import time from `MACRO_TRACKER_DB`, so the environment variable is set before
the app package is imported anywhere in the session.
"""

import os
import sys
import tempfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

# Must happen before `app.db` is imported, so no app import sits above this.
_TMP_DB = Path(tempfile.mkdtemp(prefix="macro-tracker-tests-")) / "test.db"
os.environ["MACRO_TRACKER_DB"] = str(_TMP_DB)

# Every variable that selects or configures a model provider. The suite stubs
# the provider, so a developer's own .env must not reach it -- otherwise the
# tests pass or fail depending on whose machine they run on, and a stray live
# call would be slow and, on a paid provider, billed.
PROVIDER_ENV = (
    "MACRO_TRACKER_PROVIDER",
    "MACRO_TRACKER_MODEL",
    "MACRO_TRACKER_BASE_URL",
    "MACRO_TRACKER_API_KEY",
    "MACRO_TRACKER_STRUCTURED_OUTPUT",
    "ANTHROPIC_API_KEY",
    "GROQ_API_KEY",
    "GEMINI_API_KEY",
    "OPENROUTER_API_KEY",
    "OLLAMA_API_KEY",
)
for _name in PROVIDER_ENV:
    os.environ.pop(_name, None)

from fastapi.testclient import TestClient  # noqa: E402

from app import db as app_db  # noqa: E402

TABLES = ("meals", "activities", "weight_logs", "day_ratings", "profile")

PROFILE = {
    "age": 28,
    "gender": "female",
    "height_cm": 165.0,
    "weight_kg": 70.0,
    "activity_level": "moderate",
    "goal": "lose_fat",
    "timeline_weeks": 12,
    "target_weight_kg": 62.0,
}

TODAY = "2026-01-15"


@pytest.fixture(autouse=True)
def no_ambient_provider(monkeypatch):
    """Re-clear the provider variables for every test.

    Importing `app.main` runs `load_dotenv`, which puts the developer's .env
    back into the environment, so clearing once at module import is not enough.
    """
    from app import providers

    for name in PROVIDER_ENV:
        monkeypatch.delenv(name, raising=False)
    providers.reset_provider()
    yield
    providers.reset_provider()


@pytest.fixture(autouse=True)
def clean_db(tmp_path, monkeypatch):
    """Point the app at an empty database for each test."""
    path = tmp_path / "test.db"
    monkeypatch.setattr(app_db, "DB_PATH", path)
    app_db.init_db()
    yield path


@pytest.fixture
def client():
    from app.main import app

    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture
def onboarded(client):
    """A client whose profile is already set up."""
    response = client.post("/api/profile", json=PROFILE)
    assert response.status_code == 200, response.text
    return client


@pytest.fixture
def today(monkeypatch):
    """Freeze the app's notion of today so date-window checks are deterministic.

    Without this, fixtures dated `TODAY` would start failing the future-date
    rule the day after they were written.
    """
    import datetime as real_datetime

    import app.validation as validation

    frozen = real_datetime.date.fromisoformat(TODAY)
    monkeypatch.setattr(validation, "today", lambda: frozen)
    return TODAY


def meal_payload(**overrides):
    """A valid meal body, with overrides applied."""
    return {
        "date": TODAY,
        "meal_type": "lunch",
        "description": "Chicken wrap and a coke",
        "calories": 620,
        "protein_g": 34.0,
        "carbs_g": 58.0,
        "fat_g": 24.0,
        **overrides,
    }


def activity_payload(**overrides):
    return {
        "date": TODAY,
        "activity_type": "workout",
        "description": "Push day: bench, overhead press, dips",
        "calories_burned": 310,
        **overrides,
    }
