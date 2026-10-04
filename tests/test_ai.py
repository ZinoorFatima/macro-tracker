"""The Claude boundary.

A tool schema constrains the *shape* of a tool call, not the sanity of its
values: Claude can return a schema-valid 3,000g of protein, a score of 47, or
an empty item list. These tests pin down what the app does with each of those
rather than letting a bad row reach the database.

No test here makes a network call -- `ai._call` is stubbed, so the suite runs
offline and unbilled. The live checks live in `evals/`.
"""

import pytest

from app import ai, providers
from tests.conftest import PROFILE

pytestmark = pytest.mark.usefixtures("today")

GOOD_MEAL = {
    "items": [
        {
            "name": "Chicken wrap",
            "portion": "1 large",
            "calories": 520,
            "protein_g": 34,
            "carbs_g": 45,
            "fat_g": 22,
        },
        {
            "name": "Coke",
            "portion": "330ml can",
            "calories": 139,
            "protein_g": 0,
            "carbs_g": 35,
            "fat_g": 0,
        },
    ],
    "total_calories": 659,
    "total_protein_g": 34,
    "total_carbs_g": 80,
    "total_fat_g": 22,
    "score": 5,
    "feedback": "Swap the coke for water and add a side salad.",
}

GOOD_ACTIVITY = {
    "exercises": [
        {"name": "Bench press", "kind": "strength", "sets": 4, "reps": 8, "weight_kg": 60},
        {"name": "Treadmill", "kind": "cardio", "duration_min": 20},
    ],
    "total_calories_burned": 340,
    "notes": "Assumed moderate pace on the treadmill.",
}

GOOD_RATING = {
    "score": 7,
    "summary": "Protein on target, calories slightly over.",
    "progress_note": "On track for the 12-week timeline.",
}


@pytest.fixture
def stub_call(monkeypatch):
    """Replace `ai._call` with a canned (tool_name, tool_input) response."""

    def install(name, data):
        monkeypatch.setattr(ai, "_call", lambda *a, **k: (name, data))

    return install


class TestMealAnalysisValidation:
    def test_accepts_a_well_formed_analysis(self):
        result = ai.validate_meal_analysis(GOOD_MEAL)
        assert result["score"] == 5
        assert len(result["items"]) == 2

    def test_totals_are_recomputed_from_the_items(self):
        """A total that disagrees with the breakdown is replaced, not trusted.

        The user reviews the per-item table before saving, so the stored totals
        have to be the ones that add up to what they saw.
        """
        lying = {**GOOD_MEAL, "total_calories": 99, "total_protein_g": 999}
        result = ai.validate_meal_analysis(lying)
        assert result["total_calories"] == 520 + 139
        assert result["total_protein_g"] == 34.0

    def test_portion_is_carried_through_when_present(self):
        assert ai.validate_meal_analysis(GOOD_MEAL)["items"][0]["portion"] == "1 large"

    def test_a_missing_portion_is_simply_absent(self):
        payload = {
            **GOOD_MEAL,
            "items": [
                {"name": "Toast", "calories": 80, "protein_g": 3, "carbs_g": 15, "fat_g": 1}
            ],
        }
        assert "portion" not in ai.validate_meal_analysis(payload)["items"][0]

    @pytest.mark.parametrize("items", [[], None, "chicken", {}])
    def test_rejects_an_empty_or_malformed_item_list(self, items):
        with pytest.raises(ai.AIOutputError):
            ai.validate_meal_analysis({**GOOD_MEAL, "items": items})

    def test_rejects_an_unnamed_item(self):
        with pytest.raises(ai.AIOutputError, match="no name"):
            ai.validate_meal_analysis(
                {
                    **GOOD_MEAL,
                    "items": [
                        {"name": "  ", "calories": 10, "protein_g": 1, "carbs_g": 1, "fat_g": 1}
                    ],
                }
            )

    def test_rejects_an_impossible_macro(self):
        with pytest.raises(ai.AIOutputError, match="impossible"):
            ai.validate_meal_analysis(
                {
                    **GOOD_MEAL,
                    "items": [
                        {
                            "name": "Steak",
                            "calories": 500,
                            "protein_g": 3000,
                            "carbs_g": 0,
                            "fat_g": 20,
                        }
                    ],
                }
            )

    def test_rejects_a_negative_macro(self):
        with pytest.raises(ai.AIOutputError):
            ai.validate_meal_analysis(
                {
                    **GOOD_MEAL,
                    "items": [
                        {
                            "name": "Steak",
                            "calories": 500,
                            "protein_g": -5,
                            "carbs_g": 0,
                            "fat_g": 20,
                        }
                    ],
                }
            )

    def test_rejects_a_non_numeric_macro(self):
        with pytest.raises(ai.AIOutputError):
            ai.validate_meal_analysis(
                {
                    **GOOD_MEAL,
                    "items": [
                        {
                            "name": "Steak",
                            "calories": "about 500",
                            "protein_g": 40,
                            "carbs_g": 0,
                            "fat_g": 20,
                        }
                    ],
                }
            )

    def test_rejects_a_meal_whose_items_total_beyond_the_ceiling(self):
        huge = [
            {
                "name": f"Pizza {i}",
                "calories": 3000,
                "protein_g": 100,
                "carbs_g": 300,
                "fat_g": 120,
            }
            for i in range(10)
        ]
        with pytest.raises(ai.AIOutputError, match="beyond"):
            ai.validate_meal_analysis({**GOOD_MEAL, "items": huge})

    @pytest.mark.parametrize("score", [0, 11, 47, -1, None, "good", 7.5, True])
    def test_rejects_an_out_of_range_or_wrong_typed_score(self, score):
        with pytest.raises(ai.AIOutputError, match="score"):
            ai.validate_meal_analysis({**GOOD_MEAL, "score": score})

    @pytest.mark.parametrize("feedback", ["", "   ", None, 42])
    def test_rejects_missing_feedback(self, feedback):
        with pytest.raises(ai.AIOutputError, match="feedback"):
            ai.validate_meal_analysis({**GOOD_MEAL, "feedback": feedback})

    def test_overlong_feedback_is_truncated_not_rejected(self):
        result = ai.validate_meal_analysis({**GOOD_MEAL, "feedback": "x" * 99_999})
        assert len(result["feedback"]) <= 4_000

    def test_an_overlong_item_name_is_truncated(self):
        result = ai.validate_meal_analysis(
            {
                **GOOD_MEAL,
                "items": [
                    {
                        "name": "y" * 999,
                        "calories": 10,
                        "protein_g": 1,
                        "carbs_g": 1,
                        "fat_g": 1,
                    }
                ],
            }
        )
        assert len(result["items"][0]["name"]) == 200

    def test_the_output_is_storable_by_the_meal_model(self):
        """Closes the loop: a validated analysis must satisfy MealIn."""
        from app.models import MealIn
        from tests.conftest import TODAY

        analysis = ai.validate_meal_analysis(GOOD_MEAL)
        assert MealIn(
            date=TODAY,
            meal_type="lunch",
            description="chicken wrap and a coke",
            calories=analysis["total_calories"],
            protein_g=analysis["total_protein_g"],
            carbs_g=analysis["total_carbs_g"],
            fat_g=analysis["total_fat_g"],
            items=analysis["items"],
            score=analysis["score"],
            feedback=analysis["feedback"],
        )


class TestActivityAnalysisValidation:
    def test_accepts_a_well_formed_analysis(self):
        result = ai.validate_activity_analysis(GOOD_ACTIVITY)
        assert result["total_calories_burned"] == 340
        assert len(result["exercises"]) == 2

    def test_rejects_an_impossible_burn(self):
        with pytest.raises(ai.AIOutputError, match="impossible"):
            ai.validate_activity_analysis({**GOOD_ACTIVITY, "total_calories_burned": 99_999})

    def test_rejects_a_missing_burn(self):
        with pytest.raises(ai.AIOutputError):
            ai.validate_activity_analysis({**GOOD_ACTIVITY, "total_calories_burned": None})

    def test_an_unknown_exercise_kind_falls_back_to_cardio(self):
        result = ai.validate_activity_analysis(
            {**GOOD_ACTIVITY, "exercises": [{"name": "Yoga", "kind": "flexibility"}]}
        )
        assert result["exercises"][0]["kind"] == "cardio"

    def test_an_unusable_detail_field_is_dropped_not_fatal(self):
        """One stray set count shouldn't cost the user the whole analysis."""
        result = ai.validate_activity_analysis(
            {
                **GOOD_ACTIVITY,
                "exercises": [
                    {"name": "Bench press", "kind": "strength", "sets": "four", "reps": 8}
                ],
            }
        )
        assert "sets" not in result["exercises"][0]
        assert result["exercises"][0]["reps"] == 8

    def test_an_empty_exercise_list_is_allowed(self):
        """A pure steps or rest day legitimately has no exercises."""
        result = ai.validate_activity_analysis({**GOOD_ACTIVITY, "exercises": []})
        assert result["exercises"] == []

    def test_rejects_an_unnamed_exercise(self):
        with pytest.raises(ai.AIOutputError, match="no name"):
            ai.validate_activity_analysis({**GOOD_ACTIVITY, "exercises": [{"kind": "cardio"}]})

    def test_missing_notes_become_an_empty_string(self):
        payload = {k: v for k, v in GOOD_ACTIVITY.items() if k != "notes"}
        assert ai.validate_activity_analysis(payload)["notes"] == ""


class TestDayRatingValidation:
    def test_accepts_a_well_formed_rating(self):
        assert ai.validate_day_rating(GOOD_RATING)["score"] == 7

    @pytest.mark.parametrize("score", [0, 11, None, "seven", 7.5])
    def test_rejects_an_out_of_range_score(self, score):
        with pytest.raises(ai.AIOutputError, match="range"):
            ai.validate_day_rating({**GOOD_RATING, "score": score})

    @pytest.mark.parametrize("summary", ["", "  ", None])
    def test_rejects_a_missing_summary(self, summary):
        with pytest.raises(ai.AIOutputError, match="summary"):
            ai.validate_day_rating({**GOOD_RATING, "summary": summary})

    def test_a_missing_progress_note_becomes_null(self):
        payload = {k: v for k, v in GOOD_RATING.items() if k != "progress_note"}
        assert ai.validate_day_rating(payload)["progress_note"] is None


class TestClarificationQuestions:
    def test_a_question_is_returned_as_a_question(self, stub_call):
        stub_call("ask_clarification", {"question": "How big was the wrap?"})
        status, value = ai.run_analysis(
            "sys", [{"role": "user", "content": "a wrap"}], ai.SUBMIT_MEAL_TOOL
        )
        assert status == "question"
        assert value == "How big was the wrap?"

    @pytest.mark.parametrize("question", ["", "   ", None, 7])
    def test_rejects_an_empty_question(self, stub_call, question):
        stub_call("ask_clarification", {"question": question})
        with pytest.raises(ai.AIOutputError):
            ai.run_analysis("sys", [{"role": "user", "content": "x"}], ai.SUBMIT_MEAL_TOOL)

    def test_an_overlong_question_is_truncated(self, stub_call):
        stub_call("ask_clarification", {"question": "q" * 9_999})
        _, value = ai.run_analysis(
            "sys", [{"role": "user", "content": "x"}], ai.SUBMIT_MEAL_TOOL
        )
        assert len(value) == 500


class TestRunAnalysisDispatch:
    def test_a_submitted_analysis_is_validated(self, stub_call):
        stub_call("submit_meal_analysis", {**GOOD_MEAL, "total_calories": 1})
        status, analysis = ai.run_analysis(
            "sys", [{"role": "user", "content": "x"}], ai.SUBMIT_MEAL_TOOL
        )
        assert status == "analysis"
        assert analysis["total_calories"] == 659

    def test_an_unexpected_tool_name_is_an_error(self, stub_call):
        stub_call("submit_activity_analysis", GOOD_ACTIVITY)
        with pytest.raises(ai.AIOutputError, match="unexpected tool"):
            ai.run_analysis("sys", [{"role": "user", "content": "x"}], ai.SUBMIT_MEAL_TOOL)


class TestAvailability:
    def test_a_missing_key_is_reported_as_unavailable(self, monkeypatch):
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
        monkeypatch.delenv("MACRO_TRACKER_PROVIDER", raising=False)
        available, detail = ai.is_configured()
        assert available is False
        assert "ANTHROPIC_API_KEY" in detail

    def test_provider_failures_surface_as_ai_unavailable(self, monkeypatch):
        """Routers catch one exception type regardless of the provider."""
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
        providers.reset_provider()
        with pytest.raises(ai.AIUnavailableError):
            ai.call_with_meta(
                "sys",
                [{"role": "user", "content": "x"}],
                [ai.SUBMIT_MEAL_TOOL],
                {"type": "any"},
            )

    def test_ai_output_errors_are_a_kind_of_unavailable(self):
        """So routers that already catch AIUnavailableError handle both."""
        assert issubclass(ai.AIOutputError, ai.AIUnavailableError)


class TestPromptAssembly:
    """The system prompts carry the user's profile; a silent drop would make
    every analysis generic without anything failing."""

    def test_the_profile_block_includes_targets_and_goal(self):
        profile = {
            **PROFILE,
            "calorie_target": 1774,
            "protein_target_g": 140,
            "carbs_target_g": 193,
            "fat_target_g": 49,
        }
        block = ai.profile_block(profile)
        assert "1774 kcal" in block
        assert "140g protein" in block
        assert "lose fat" in block
        assert "12 weeks" in block
        assert "62.0kg" in block

    def test_the_timeline_is_omitted_when_not_set(self):
        profile = {
            **PROFILE,
            "timeline_weeks": None,
            "target_weight_kg": None,
            "calorie_target": 1774,
            "protein_target_g": 140,
            "carbs_target_g": 193,
            "fat_target_g": 49,
        }
        block = ai.profile_block(profile)
        assert "weeks" not in block
        assert "target weight" not in block

    def test_the_meal_prompt_names_the_meal_type(self):
        profile = {
            **PROFILE,
            "calorie_target": 1774,
            "protein_target_g": 140,
            "carbs_target_g": 193,
            "fat_target_g": 49,
        }
        assert "breakfast" in ai.meal_system_prompt(profile, "breakfast")

    def test_the_activity_prompt_carries_bodyweight_for_met_math(self):
        profile = {
            **PROFILE,
            "calorie_target": 1774,
            "protein_target_g": 140,
            "carbs_target_g": 193,
            "fat_target_g": 49,
        }
        assert "70.0kg" in ai.activity_system_prompt(profile)


class TestRouterAIPaths:
    """End-to-end through HTTP, with the model stubbed."""

    def test_analyze_meal_returns_a_question(self, onboarded, monkeypatch):
        monkeypatch.setattr(
            ai,
            "_call",
            lambda *a, **k: ("ask_clarification", {"question": "Grilled or fried?"}),
        )
        response = onboarded.post(
            "/api/meals/analyze",
            json={
                "meal_type": "lunch",
                "messages": [{"role": "user", "content": "chicken and rice"}],
            },
        )
        assert response.json() == {"status": "question", "question": "Grilled or fried?"}

    def test_analyze_meal_returns_an_analysis(self, onboarded, monkeypatch):
        monkeypatch.setattr(ai, "_call", lambda *a, **k: ("submit_meal_analysis", GOOD_MEAL))
        body = onboarded.post(
            "/api/meals/analyze",
            json={
                "meal_type": "lunch",
                "messages": [{"role": "user", "content": "chicken wrap and a coke"}],
            },
        ).json()
        assert body["status"] == "analysis"
        assert body["analysis"]["total_calories"] == 659

    def test_a_bad_analysis_becomes_a_503_not_a_bad_row(self, onboarded, monkeypatch):
        monkeypatch.setattr(
            ai, "_call", lambda *a, **k: ("submit_meal_analysis", {**GOOD_MEAL, "score": 99})
        )
        response = onboarded.post(
            "/api/meals/analyze",
            json={"meal_type": "lunch", "messages": [{"role": "user", "content": "x"}]},
        )
        assert response.status_code == 503
        assert "score" in response.json()["detail"]

    def test_analyze_without_a_key_is_a_503(self, onboarded):
        response = onboarded.post(
            "/api/meals/analyze",
            json={"meal_type": "lunch", "messages": [{"role": "user", "content": "x"}]},
        )
        assert response.status_code == 503
        assert "API key" in response.json()["detail"]

    def test_analyze_requires_a_profile(self, client):
        response = client.post(
            "/api/meals/analyze",
            json={"meal_type": "lunch", "messages": [{"role": "user", "content": "x"}]},
        )
        assert response.status_code == 404

    def test_analyze_rejects_an_empty_conversation(self, onboarded):
        assert (
            onboarded.post(
                "/api/meals/analyze", json={"meal_type": "lunch", "messages": []}
            ).status_code
            == 422
        )

    def test_analyze_rejects_a_conversation_starting_with_the_assistant(self, onboarded):
        response = onboarded.post(
            "/api/meals/analyze",
            json={
                "meal_type": "lunch",
                "messages": [{"role": "assistant", "content": "hello"}],
            },
        )
        assert response.status_code == 422

    def test_rate_day_persists_the_rating(self, onboarded, monkeypatch):
        from tests.conftest import TODAY, meal_payload

        onboarded.post("/api/meals", json=meal_payload())
        monkeypatch.setattr(ai, "_call", lambda *a, **k: ("submit_day_rating", GOOD_RATING))
        body = onboarded.post(f"/api/days/{TODAY}/rate").json()
        assert body["score"] == 7
        assert onboarded.get(f"/api/days/{TODAY}/summary").json()["rating"]["score"] == 7

    def test_rating_the_same_day_twice_overwrites(self, onboarded, monkeypatch):
        from tests.conftest import TODAY, meal_payload

        onboarded.post("/api/meals", json=meal_payload())
        monkeypatch.setattr(ai, "_call", lambda *a, **k: ("submit_day_rating", GOOD_RATING))
        onboarded.post(f"/api/days/{TODAY}/rate")
        monkeypatch.setattr(
            ai, "_call", lambda *a, **k: ("submit_day_rating", {**GOOD_RATING, "score": 9})
        )
        onboarded.post(f"/api/days/{TODAY}/rate")
        assert onboarded.get(f"/api/days/{TODAY}/summary").json()["rating"]["score"] == 9

    def test_rating_an_empty_day_is_rejected(self, onboarded, monkeypatch):
        from tests.conftest import TODAY

        monkeypatch.setattr(ai, "_call", lambda *a, **k: ("submit_day_rating", GOOD_RATING))
        response = onboarded.post(f"/api/days/{TODAY}/rate")
        assert response.status_code == 400
        assert "Nothing logged" in response.json()["detail"]

    def test_analyze_activity_returns_an_analysis(self, onboarded, monkeypatch):
        monkeypatch.setattr(
            ai, "_call", lambda *a, **k: ("submit_activity_analysis", GOOD_ACTIVITY)
        )
        body = onboarded.post(
            "/api/activities/analyze",
            json={
                "messages": [{"role": "user", "content": "bench 4x8 at 60kg, 20min treadmill"}]
            },
        ).json()
        assert body["analysis"]["total_calories_burned"] == 340
