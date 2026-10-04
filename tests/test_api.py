"""HTTP behaviour: status codes, persistence, and the day-summary arithmetic."""

import pytest

from tests.conftest import PROFILE, TODAY, activity_payload, meal_payload

pytestmark = pytest.mark.usefixtures("today")


class TestHealth:
    def test_reports_db_and_key_status(self, client):
        body = client.get("/api/health").json()
        assert body["db"] is True
        # conftest clears the key, so the AI must report itself unavailable.
        assert body["api_key_present"] is False

    def test_serves_the_single_page_app(self, client):
        response = client.get("/")
        assert response.status_code == 200
        assert "Macro Tracker" in response.text


class TestProfile:
    def test_404_before_onboarding(self, client):
        assert client.get("/api/profile").status_code == 404

    def test_create_returns_computed_targets(self, client):
        body = client.post("/api/profile", json=PROFILE).json()
        assert body["calorie_target"] > 1200
        assert body["protein_target_g"] == round(2.0 * PROFILE["weight_kg"])

    def test_create_is_idempotent_and_overwrites(self, onboarded):
        onboarded.post("/api/profile", json={**PROFILE, "age": 31})
        assert onboarded.get("/api/profile").json()["age"] == 31

    def test_update_recomputes_targets(self, onboarded):
        before = onboarded.get("/api/profile").json()["calorie_target"]
        after = onboarded.put(
            "/api/profile", json={"goal": "gain_muscle", "target_weight_kg": 75}
        ).json()
        assert after["calorie_target"] > before

    def test_update_leaves_unsent_fields_alone(self, onboarded):
        onboarded.put("/api/profile", json={"age": 29})
        body = onboarded.get("/api/profile").json()
        assert body["age"] == 29
        assert body["weight_kg"] == PROFILE["weight_kg"]
        assert body["timeline_weeks"] == PROFILE["timeline_weeks"]

    def test_an_explicit_null_clears_an_optional_field(self, onboarded):
        onboarded.put("/api/profile", json={"timeline_weeks": None})
        assert onboarded.get("/api/profile").json()["timeline_weeks"] is None

    def test_update_rejects_a_goal_change_that_contradicts_the_target(self, onboarded):
        # Profile targets 62kg from 70kg; switching to gain_muscle contradicts it.
        response = onboarded.put("/api/profile", json={"goal": "gain_muscle"})
        assert response.status_code == 422
        assert "gain muscle" in response.json()["detail"]

    def test_update_404s_before_onboarding(self, client):
        assert client.put("/api/profile", json={"age": 30}).status_code == 404


class TestWeight:
    def test_logging_weight_updates_the_profile_and_targets(self, onboarded):
        body = onboarded.post("/api/weight", json={"date": TODAY, "weight_kg": 68.0}).json()
        assert "targets" in body
        assert onboarded.get("/api/profile").json()["weight_kg"] == 68.0

    def test_one_entry_per_day_the_latest_wins(self, onboarded):
        onboarded.post("/api/weight", json={"date": TODAY, "weight_kg": 69.0})
        onboarded.post("/api/weight", json={"date": TODAY, "weight_kg": 68.5})
        entries = onboarded.get("/api/weight").json()
        assert len(entries) == 1
        assert entries[0]["weight_kg"] == 68.5

    def test_history_is_returned_oldest_first(self, onboarded):
        for date, kg in [("2026-01-10", 71.0), ("2026-01-05", 72.0), (TODAY, 70.0)]:
            onboarded.post("/api/weight", json={"date": date, "weight_kg": kg})
        dates = [e["date"] for e in onboarded.get("/api/weight").json()]
        assert dates == sorted(dates)

    def test_backdating_does_not_overwrite_current_weight(self, onboarded):
        """The newest log is the current weight, whatever order they arrive in."""
        onboarded.post("/api/weight", json={"date": TODAY, "weight_kg": 68.0})
        onboarded.post("/api/weight", json={"date": "2026-01-01", "weight_kg": 75.0})
        assert onboarded.get("/api/profile").json()["weight_kg"] == 68.0

    def test_rejects_an_implausible_weight(self, onboarded):
        assert (
            onboarded.post("/api/weight", json={"date": TODAY, "weight_kg": 900}).status_code
            == 422
        )


class TestMeals:
    def test_save_then_list(self, onboarded):
        created = onboarded.post("/api/meals", json=meal_payload()).json()
        assert created["id"]
        listed = onboarded.get(f"/api/meals?date={TODAY}").json()
        assert len(listed) == 1
        assert listed[0]["description"] == "Chicken wrap and a coke"

    def test_list_is_scoped_to_one_date(self, onboarded):
        onboarded.post("/api/meals", json=meal_payload())
        onboarded.post("/api/meals", json=meal_payload(date="2026-01-14"))
        assert len(onboarded.get(f"/api/meals?date={TODAY}").json()) == 1

    def test_items_round_trip_as_json(self, onboarded):
        items = [{"name": "Wrap", "calories": 400, "protein_g": 20, "carbs_g": 40, "fat_g": 15}]
        onboarded.post("/api/meals", json=meal_payload(items=items))
        assert onboarded.get(f"/api/meals?date={TODAY}").json()[0]["items"] == items

    def test_a_meal_without_items_lists_items_as_null(self, onboarded):
        onboarded.post("/api/meals", json=meal_payload())
        assert onboarded.get(f"/api/meals?date={TODAY}").json()[0]["items"] is None

    def test_update_changes_only_what_was_sent(self, onboarded):
        meal_id = onboarded.post("/api/meals", json=meal_payload()).json()["id"]
        updated = onboarded.put(f"/api/meals/{meal_id}", json={"calories": 700}).json()
        assert updated["calories"] == 700
        assert updated["protein_g"] == 34.0

    def test_update_404s_on_an_unknown_meal(self, onboarded):
        assert onboarded.put("/api/meals/9999", json={"calories": 1}).status_code == 404

    def test_delete_removes_the_meal(self, onboarded):
        meal_id = onboarded.post("/api/meals", json=meal_payload()).json()["id"]
        assert onboarded.delete(f"/api/meals/{meal_id}").status_code == 200
        assert onboarded.get(f"/api/meals?date={TODAY}").json() == []

    def test_delete_404s_on_an_unknown_meal(self, onboarded):
        assert onboarded.delete("/api/meals/9999").status_code == 404

    def test_rejects_a_malformed_date_in_the_query(self, onboarded):
        assert onboarded.get("/api/meals?date=whenever").status_code == 422


class TestActivities:
    def test_save_then_list_a_workout(self, onboarded):
        onboarded.post("/api/activities", json=activity_payload())
        listed = onboarded.get(f"/api/activities?date={TODAY}").json()
        assert listed[0]["calories_burned"] == 310

    def test_steps_get_a_bodyweight_derived_estimate(self, onboarded):
        """With no explicit burn, steps are costed from the user's weight."""
        body = onboarded.post(
            "/api/activities",
            json={
                "date": TODAY,
                "activity_type": "steps",
                "steps": 10_000,
            },
        ).json()
        # 10000 steps * 70kg * 0.0005 kcal
        assert body["calories_burned"] == 350

    def test_an_explicit_burn_overrides_the_step_estimate(self, onboarded):
        body = onboarded.post(
            "/api/activities",
            json={
                "date": TODAY,
                "activity_type": "steps",
                "steps": 10_000,
                "calories_burned": 420,
            },
        ).json()
        assert body["calories_burned"] == 420

    def test_a_rest_day_is_logged_with_no_burn(self, onboarded):
        body = onboarded.post(
            "/api/activities", json={"date": TODAY, "activity_type": "rest"}
        ).json()
        assert body["calories_burned"] == 0

    def test_exercises_round_trip_as_json(self, onboarded):
        exercises = [{"name": "Bench press", "kind": "strength", "sets": 4, "reps": 8}]
        onboarded.post("/api/activities", json=activity_payload(exercises=exercises))
        listed = onboarded.get(f"/api/activities?date={TODAY}").json()
        assert listed[0]["exercises"] == exercises

    def test_update_and_delete(self, onboarded):
        activity_id = onboarded.post("/api/activities", json=activity_payload()).json()["id"]
        assert (
            onboarded.put(
                f"/api/activities/{activity_id}", json={"calories_burned": 400}
            ).json()["calories_burned"]
            == 400
        )
        assert onboarded.delete(f"/api/activities/{activity_id}").status_code == 200
        assert onboarded.get(f"/api/activities?date={TODAY}").json() == []

    def test_delete_404s_on_an_unknown_activity(self, onboarded):
        assert onboarded.delete("/api/activities/9999").status_code == 404


class TestDaySummary:
    def test_empty_day_totals_are_zero(self, onboarded):
        body = onboarded.get(f"/api/days/{TODAY}/summary").json()
        assert body["totals"]["calories"] == 0
        assert body["meals"] == []
        assert body["rating"] is None

    def test_totals_sum_every_meal(self, onboarded):
        onboarded.post(
            "/api/meals", json=meal_payload(calories=600, protein_g=30, carbs_g=50, fat_g=20)
        )
        onboarded.post(
            "/api/meals",
            json=meal_payload(
                meal_type="dinner", calories=800, protein_g=45, carbs_g=70, fat_g=28
            ),
        )
        totals = onboarded.get(f"/api/days/{TODAY}/summary").json()["totals"]
        assert totals["calories"] == 1400
        assert totals["protein_g"] == 75.0
        assert totals["carbs_g"] == 120.0
        assert totals["fat_g"] == 48.0

    def test_activity_burn_is_tracked_separately_from_intake(self, onboarded):
        onboarded.post("/api/meals", json=meal_payload(calories=600))
        onboarded.post("/api/activities", json=activity_payload(calories_burned=300))
        totals = onboarded.get(f"/api/days/{TODAY}/summary").json()["totals"]
        assert totals["calories"] == 600
        assert totals["calories_burned"] == 300

    def test_targets_come_from_the_profile(self, onboarded):
        body = onboarded.get(f"/api/days/{TODAY}/summary").json()
        profile = onboarded.get("/api/profile").json()
        assert body["targets"]["calories"] == profile["calorie_target"]
        assert body["targets"]["protein_g"] == profile["protein_target_g"]

    def test_the_days_weight_is_included_when_logged(self, onboarded):
        onboarded.post("/api/weight", json={"date": TODAY, "weight_kg": 69.2})
        assert onboarded.get(f"/api/days/{TODAY}/summary").json()["weight_kg"] == 69.2

    def test_404s_before_onboarding(self, client):
        assert client.get(f"/api/days/{TODAY}/summary").status_code == 404

    def test_rejects_a_malformed_date(self, onboarded):
        assert onboarded.get("/api/days/whenever/summary").status_code == 422


class TestDayList:
    def test_only_days_with_something_logged_appear(self, onboarded):
        onboarded.post("/api/meals", json=meal_payload())
        onboarded.post("/api/meals", json=meal_payload(date="2026-01-13"))
        days = onboarded.get("/api/days?start=2026-01-01&end=2026-01-31").json()
        assert [d["date"] for d in days] == [TODAY, "2026-01-13"]

    def test_days_are_newest_first(self, onboarded):
        for date in ["2026-01-10", "2026-01-12", "2026-01-11"]:
            onboarded.post("/api/meals", json=meal_payload(date=date))
        dates = [
            d["date"] for d in onboarded.get("/api/days?start=2026-01-01&end=2026-01-31").json()
        ]
        assert dates == sorted(dates, reverse=True)

    def test_the_range_is_inclusive_at_both_ends(self, onboarded):
        onboarded.post("/api/meals", json=meal_payload(date="2026-01-10"))
        onboarded.post("/api/meals", json=meal_payload(date="2026-01-12"))
        days = onboarded.get("/api/days?start=2026-01-10&end=2026-01-12").json()
        assert len(days) == 2

    def test_a_day_carries_its_entry_counts(self, onboarded):
        onboarded.post("/api/meals", json=meal_payload())
        onboarded.post("/api/activities", json=activity_payload())
        day = onboarded.get("/api/days?start=2026-01-01&end=2026-01-31").json()[0]
        assert day["meal_count"] == 1
        assert day["activity_count"] == 1

    def test_a_weight_only_day_still_appears(self, onboarded):
        onboarded.post("/api/weight", json={"date": "2026-01-09", "weight_kg": 70.5})
        days = onboarded.get("/api/days?start=2026-01-01&end=2026-01-31").json()
        assert [d["date"] for d in days] == ["2026-01-09"]

    def test_rejects_a_reversed_range(self, onboarded):
        response = onboarded.get("/api/days?start=2026-01-31&end=2026-01-01")
        assert response.status_code == 422
