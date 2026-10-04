"""Input validation: the layer that decides what is allowed into the database."""

import datetime as dt

import pytest
from pydantic import ValidationError

from app.models import ActivityIn, MealIn, MealUpdate, ProfileIn, ProfileUpdate
from app.validation import (
    MAX_MEAL_CALORIES,
    ValidationProblem,
    check_date_range,
    check_goal_consistency,
    coerce_number,
    parse_date,
)
from tests.conftest import TODAY, activity_payload, meal_payload


class TestParseDate:
    @pytest.mark.parametrize("value", ["2024-01-01", "2024-02-29", "2000-01-01"])
    def test_accepts_real_iso_dates(self, value):
        assert parse_date(value) == dt.date.fromisoformat(value)

    @pytest.mark.parametrize(
        "value",
        [
            "not-a-date",
            "2026-99-99",  # syntactically shaped like a date, no such day
            "2026-02-29",  # 2026 is not a leap year
            "2026-02-30",  # February has no 30th
            "2026-13-01",  # no 13th month
            "20260101",  # fromisoformat accepts this; we must not
            "2026-1-1",  # unpadded
            "2026-01-01T00:00:00",
            "",
            "0000-00-00",
        ],
    )
    def test_rejects_malformed_dates(self, value):
        with pytest.raises(ValidationProblem):
            parse_date(value)

    @pytest.mark.parametrize("value", [None, 20260101, [], {}])
    def test_rejects_non_strings(self, value):
        with pytest.raises(ValidationProblem):
            parse_date(value)

    def test_rejects_dates_before_the_epoch_we_support(self):
        with pytest.raises(ValidationProblem, match="before"):
            parse_date("1999-12-31")

    def test_rejects_dates_far_in_the_future(self):
        far = (dt.date.today() + dt.timedelta(days=30)).isoformat()
        with pytest.raises(ValidationProblem, match="future"):
            parse_date(far)

    def test_accepts_today(self):
        assert parse_date(dt.date.today().isoformat())

    def test_accepts_one_day_of_timezone_slack(self):
        """A user a timezone ahead of the server should still be able to log."""
        tomorrow = (dt.date.today() + dt.timedelta(days=1)).isoformat()
        assert parse_date(tomorrow)


class TestDateRange:
    def test_accepts_an_ordered_range(self):
        assert check_date_range("2026-01-01", "2026-01-31")

    def test_accepts_a_single_day_range(self):
        assert check_date_range("2026-01-01", "2026-01-01")

    def test_rejects_a_reversed_range(self):
        with pytest.raises(ValidationProblem, match="after"):
            check_date_range("2026-01-31", "2026-01-01")

    def test_rejects_a_malformed_bound(self):
        with pytest.raises(ValidationProblem):
            check_date_range("whenever", "2026-01-01")


class TestGoalConsistency:
    def test_no_target_weight_is_always_fine(self):
        check_goal_consistency("lose_fat", 70, None)

    def test_cut_toward_a_lower_target_is_fine(self):
        check_goal_consistency("lose_fat", 70, 62)

    def test_cut_toward_a_higher_target_is_rejected(self):
        with pytest.raises(ValidationProblem, match="lose fat"):
            check_goal_consistency("lose_fat", 70, 85)

    def test_bulk_toward_a_higher_target_is_fine(self):
        check_goal_consistency("gain_muscle", 70, 78)

    def test_bulk_toward_a_lower_target_is_rejected(self):
        with pytest.raises(ValidationProblem, match="gain muscle"):
            check_goal_consistency("gain_muscle", 70, 60)

    @pytest.mark.parametrize("goal", ["maintain", "recomp"])
    def test_maintain_and_recomp_allow_either_direction(self, goal):
        check_goal_consistency(goal, 70, 65)
        check_goal_consistency(goal, 70, 75)

    def test_half_a_kilo_of_slack_is_tolerated(self):
        """Rounding your own weight shouldn't be a validation error."""
        check_goal_consistency("lose_fat", 70, 70.4)


class TestCoerceNumber:
    def test_accepts_an_in_range_number(self):
        assert coerce_number(5, field="x", lo=0, hi=10) == 5.0

    @pytest.mark.parametrize("value", ["5", None, [], {}, True, False])
    def test_rejects_non_numbers_including_bools(self, value):
        with pytest.raises(ValidationProblem):
            coerce_number(value, field="x", lo=0, hi=10)

    @pytest.mark.parametrize("value", [float("nan"), float("inf"), float("-inf")])
    def test_rejects_non_finite_floats(self, value):
        with pytest.raises(ValidationProblem):
            coerce_number(value, field="x", lo=0, hi=10)

    @pytest.mark.parametrize("value", [-1, 11, 1e9])
    def test_rejects_out_of_range_values(self, value):
        with pytest.raises(ValidationProblem, match="outside"):
            coerce_number(value, field="x", lo=0, hi=10)

    def test_boundaries_are_inclusive(self):
        assert coerce_number(0, field="x", lo=0, hi=10) == 0.0
        assert coerce_number(10, field="x", lo=0, hi=10) == 10.0


class TestMealModel:
    def test_accepts_a_valid_meal(self):
        assert MealIn(**meal_payload()).calories == 620

    def test_trims_the_description(self):
        assert MealIn(**meal_payload(description="  toast  ")).description == "toast"

    @pytest.mark.parametrize("description", ["", "   ", "\n\t"])
    def test_rejects_a_blank_description(self, description):
        with pytest.raises(ValidationError):
            MealIn(**meal_payload(description=description))

    def test_rejects_an_overlong_description(self):
        with pytest.raises(ValidationError):
            MealIn(**meal_payload(description="x" * 501))

    def test_rejects_absurd_calories(self):
        with pytest.raises(ValidationError):
            MealIn(**meal_payload(calories=MAX_MEAL_CALORIES + 1))

    def test_rejects_negative_macros(self):
        with pytest.raises(ValidationError):
            MealIn(**meal_payload(protein_g=-1))

    def test_rejects_an_unknown_meal_type(self):
        with pytest.raises(ValidationError):
            MealIn(**meal_payload(meal_type="brunch"))

    @pytest.mark.parametrize("score", [0, 11, -3])
    def test_rejects_an_out_of_range_score(self, score):
        with pytest.raises(ValidationError):
            MealIn(**meal_payload(score=score))

    def test_zero_calories_is_allowed(self):
        """Black coffee is a real log entry."""
        assert MealIn(**meal_payload(calories=0, protein_g=0, carbs_g=0, fat_g=0))


class TestPartialUpdates:
    def test_an_absent_field_is_not_in_the_dump(self):
        update = MealUpdate(calories=500)
        assert update.model_dump(exclude_unset=True) == {"calories": 500}

    def test_an_explicit_null_is_kept_so_it_can_clear_a_value(self):
        update = ProfileUpdate(timeline_weeks=None)
        assert update.model_dump(exclude_unset=True) == {"timeline_weeks": None}

    def test_an_empty_update_is_rejected(self):
        with pytest.raises(ValidationError):
            MealUpdate()

    def test_an_empty_profile_update_is_rejected(self):
        with pytest.raises(ValidationError):
            ProfileUpdate()


class TestProfileModel:
    def test_accepts_a_valid_profile(self):
        from tests.conftest import PROFILE

        assert ProfileIn(**PROFILE).age == 28

    @pytest.mark.parametrize("age", [9, 101, -1])
    def test_rejects_an_implausible_age(self, age):
        from tests.conftest import PROFILE

        with pytest.raises(ValidationError):
            ProfileIn(**{**PROFILE, "age": age})

    @pytest.mark.parametrize("height", [49, 281])
    def test_rejects_an_implausible_height(self, height):
        from tests.conftest import PROFILE

        with pytest.raises(ValidationError):
            ProfileIn(**{**PROFILE, "height_cm": height})

    def test_rejects_a_target_that_contradicts_the_goal(self):
        from tests.conftest import PROFILE

        with pytest.raises(ValidationError):
            ProfileIn(**{**PROFILE, "goal": "lose_fat", "target_weight_kg": 90})


class TestActivityModel:
    def test_accepts_a_workout(self):
        assert ActivityIn(**activity_payload()).calories_burned == 310

    def test_a_workout_needs_a_description(self):
        with pytest.raises(ValidationError, match="description"):
            ActivityIn(**activity_payload(description=""))

    def test_a_steps_entry_needs_a_step_count(self):
        with pytest.raises(ValidationError, match="step count"):
            ActivityIn(date=TODAY, activity_type="steps", description="walked")

    def test_a_steps_entry_with_a_count_is_accepted(self):
        assert ActivityIn(date=TODAY, activity_type="steps", steps=8400).steps == 8400

    def test_a_rest_day_cannot_burn_calories(self):
        with pytest.raises(ValidationError, match="rest day"):
            ActivityIn(date=TODAY, activity_type="rest", calories_burned=400)

    def test_a_rest_day_is_otherwise_valid(self):
        assert ActivityIn(date=TODAY, activity_type="rest").calories_burned == 0

    def test_rejects_an_implausible_step_count(self):
        with pytest.raises(ValidationError):
            ActivityIn(date=TODAY, activity_type="steps", steps=500_000)
