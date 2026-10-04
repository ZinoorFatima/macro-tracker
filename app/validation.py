"""Shared validators and domain limits.

Every value that reaches the database passes through here, whether it came from
a form, a URL query string, or a Claude tool call. The AI path is the reason the
limits live in one module instead of inline in the Pydantic models: the model
chooses the numbers in a tool call, so the same ceilings have to be enforceable
outside a request body (see `ai.validate_meal_analysis`).
"""

from __future__ import annotations

import datetime as _dt
import re
from typing import Annotated, Any

from pydantic import AfterValidator, Field, StringConstraints

DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")

# Earliest date the app accepts a log for. Anything older is a typo, not history.
MIN_DATE = _dt.date(2000, 1, 1)
# Days into the future a log may be dated. One day of slack covers a user whose
# clock is in a timezone ahead of the server's.
FUTURE_DAYS_ALLOWED = 1

# Per-entry ceilings. Generous enough for a genuine feast, tight enough that a
# fat-fingered "5000" in the wrong box or a hallucinated macro gets rejected.
MAX_MEAL_CALORIES = 20_000
MAX_MEAL_MACRO_G = 2_000
MAX_ACTIVITY_CALORIES = 10_000
MAX_STEPS = 200_000
MAX_DESCRIPTION_LEN = 500
MAX_FEEDBACK_LEN = 4_000


class ValidationProblem(ValueError):
    """A domain rule was broken. Carries a message meant for the end user."""


def today() -> _dt.date:
    """The current date, as a single seam the tests can freeze."""
    return _dt.date.today()


def parse_calendar_date(value: str) -> _dt.date:
    """Parse a strict ``YYYY-MM-DD`` date without any window check.

    `datetime.date.fromisoformat` alone is too permissive here: on 3.11+ it
    accepts ``YYYYMMDD`` and other ISO 8601 spellings, and every query in this
    app compares date *strings* lexically, so a row stored as ``20260115``
    would silently never match ``2026-01-15`` again.
    """
    if not isinstance(value, str) or not DATE_RE.match(value):
        raise ValidationProblem(f"Date must look like YYYY-MM-DD, got {value!r}")
    try:
        parsed = _dt.date.fromisoformat(value)
    except ValueError:
        raise ValidationProblem(f"{value!r} is not a real calendar date") from None
    if parsed < MIN_DATE:
        raise ValidationProblem(f"Date {value} is before {MIN_DATE.isoformat()}")
    return parsed


def parse_date(value: str) -> _dt.date:
    """Parse a date that something is being *logged against*.

    Adds the future-date rule on top of `parse_calendar_date`: you cannot log a
    meal you have not eaten yet. Query bounds use `parse_calendar_date` instead,
    since asking for "this month" before the month is over is perfectly normal.
    """
    parsed = parse_calendar_date(value)
    latest = today() + _dt.timedelta(days=FUTURE_DAYS_ALLOWED)
    if parsed > latest:
        raise ValidationProblem(f"Date {value} is in the future")
    return parsed


def _validate_date_str(value: str) -> str:
    parse_date(value)
    return value


def _validate_date_bound(value: str) -> str:
    parse_calendar_date(value)
    return value


# A date something is logged against: a real calendar date, not in the future.
DateStr = Annotated[str, AfterValidator(_validate_date_str)]

# A date used as a query bound: a real calendar date, future allowed.
DateBound = Annotated[str, AfterValidator(_validate_date_bound)]

# A human-written label: trimmed, non-empty, bounded.
Description = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1, max_length=MAX_DESCRIPTION_LEN),
]

# Same, but allowed to be blank (activities may legitimately have no description).
OptionalDescription = Annotated[
    str, StringConstraints(strip_whitespace=True, max_length=MAX_DESCRIPTION_LEN)
]

Calories = Annotated[int, Field(ge=0, le=MAX_MEAL_CALORIES)]
MacroGrams = Annotated[float, Field(ge=0, le=MAX_MEAL_MACRO_G)]
BurnedCalories = Annotated[int, Field(ge=0, le=MAX_ACTIVITY_CALORIES)]
Steps = Annotated[int, Field(ge=0, le=MAX_STEPS)]
Score = Annotated[int, Field(ge=1, le=10)]


def check_goal_consistency(goal: str, weight_kg: float, target_weight_kg: float | None) -> None:
    """Reject a target weight that contradicts the stated goal.

    Catches the common onboarding slip of picking "lose fat" and then typing a
    target heavier than the current weight (or vice versa), which would other-
    wise produce a coherent-looking but meaningless progress narrative.
    """
    if target_weight_kg is None:
        return
    delta = target_weight_kg - weight_kg
    if goal == "lose_fat" and delta > 0.5:
        raise ValidationProblem(
            f"Goal is to lose fat but the target weight ({target_weight_kg}kg) is "
            f"above the current weight ({weight_kg}kg)."
        )
    if goal == "gain_muscle" and delta < -0.5:
        raise ValidationProblem(
            f"Goal is to gain muscle but the target weight ({target_weight_kg}kg) is "
            f"below the current weight ({weight_kg}kg)."
        )


def check_date_range(start: str, end: str) -> tuple[_dt.date, _dt.date]:
    """Validate a start/end query range and return it parsed."""
    start_d, end_d = parse_calendar_date(start), parse_calendar_date(end)
    if start_d > end_d:
        raise ValidationProblem(f"Start date {start} is after end date {end}")
    return start_d, end_d


def coerce_number(value: Any, *, field: str, lo: float, hi: float) -> float:
    """Clamp-or-reject a number that came from outside the Pydantic layer.

    Used on Claude tool output, where a value is far more likely to be a
    plausible-but-slightly-off estimate than a deliberate attack. Non-numbers
    and wildly out-of-range values are rejected; the caller decides what to do.
    """
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValidationProblem(f"{field} must be a number, got {value!r}")
    if value != value or value in (float("inf"), float("-inf")):  # NaN / inf
        raise ValidationProblem(f"{field} is not a finite number")
    if not (lo <= value <= hi):
        raise ValidationProblem(f"{field} is {value}, outside the allowed range {lo}-{hi}")
    return float(value)
