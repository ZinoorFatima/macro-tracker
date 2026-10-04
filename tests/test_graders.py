"""Tests for the eval graders themselves.

The offline eval run scores 100% against the bundled fixtures, which on its own
proves nothing: a grader that returned 1.0 unconditionally would look identical.
These tests drive each grader in both directions, so a regression that makes the
eval permanently green fails the build here instead.
"""

import json
from pathlib import Path

import pytest

from evals.graders import GRADERS, METRICS, grade_activity, grade_day, grade_meal

CASES = {
    case["id"]: case
    for case in (
        json.loads(line)
        for line in (Path(__file__).resolve().parent.parent / "evals" / "cases.jsonl")
        .read_text(encoding="utf-8")
        .splitlines()
        if line.strip()
    )
}


def analysis(**overrides):
    base = {
        "items": [
            {
                "name": "Chicken wrap",
                "calories": 528,
                "protein_g": 33,
                "carbs_g": 46,
                "fat_g": 23,
            },
            {"name": "Coke", "calories": 139, "protein_g": 0, "carbs_g": 35, "fat_g": 0},
        ],
        "total_calories": 667,
        "total_protein_g": 33,
        "total_carbs_g": 81,
        "total_fat_g": 23,
        "score": 5,
        "feedback": "Swap the coke.",
    }
    return {"status": "analysis", "analysis": {**base, **overrides}}


class TestMealCalorieGrading:
    CASE = CASES["meal-01-wrap-coke"]  # reference 660 kcal

    def test_an_accurate_estimate_passes(self):
        grade, _ = grade_meal(self.CASE, analysis())
        assert grade["kcal_ok"] == 1.0

    @pytest.mark.parametrize("kcal", [520, 800])
    def test_estimates_inside_the_band_pass(self, kcal):
        grade, _ = grade_meal(self.CASE, analysis(total_calories=kcal))
        assert grade["kcal_ok"] == 1.0

    @pytest.mark.parametrize("kcal", [300, 1500, 0])
    def test_estimates_outside_the_band_fail(self, kcal):
        grade, _ = grade_meal(self.CASE, analysis(total_calories=kcal))
        assert grade["kcal_ok"] == 0.0

    def test_the_explanation_names_both_numbers(self):
        _, why = grade_meal(self.CASE, analysis(total_calories=1500))
        assert "1500" in why["kcal_ok"] and "660" in why["kcal_ok"]

    def test_a_near_zero_reference_uses_the_absolute_floor(self):
        """A 5 kcal reference with a 25% band would be a 1.25 kcal window."""
        case = CASES["meal-07-black-coffee"]
        passing = analysis(
            total_calories=4,
            total_protein_g=0,
            total_carbs_g=0,
            total_fat_g=0,
            items=[
                {"name": "Americano", "calories": 4, "protein_g": 0, "carbs_g": 0, "fat_g": 0}
            ],
        )
        assert grade_meal(case, passing)[0]["kcal_ok"] == 1.0
        invented = analysis(
            total_calories=250,
            total_protein_g=2,
            total_carbs_g=30,
            total_fat_g=12,
            items=[
                {"name": "Latte", "calories": 250, "protein_g": 2, "carbs_g": 30, "fat_g": 12}
            ],
        )
        assert grade_meal(case, invented)[0]["kcal_ok"] == 0.0


class TestMealProteinGrading:
    CASE = CASES["meal-03-chicken-rice-broccoli"]  # reference 68g protein

    def test_an_accurate_protein_estimate_passes(self):
        grade, _ = grade_meal(
            self.CASE,
            analysis(total_calories=660, total_protein_g=66, total_carbs_g=61, total_fat_g=14),
        )
        assert grade["protein_ok"] == 1.0

    def test_badly_underestimated_protein_fails(self):
        grade, _ = grade_meal(
            self.CASE,
            analysis(total_calories=660, total_protein_g=12, total_carbs_g=61, total_fat_g=14),
        )
        assert grade["protein_ok"] == 0.0


class TestMacroCoherence:
    """Catches arithmetic incoherence without needing any ground truth."""

    CASE = CASES["meal-01-wrap-coke"]

    def test_a_coherent_split_passes(self):
        # 33*4 + 81*4 + 23*9 = 132 + 324 + 207 = 663, against a stated 667
        grade, _ = grade_meal(self.CASE, analysis())
        assert grade["macros_coherent"] == 1.0

    def test_a_split_that_does_not_reconstruct_the_total_fails(self):
        # Calories stated as 667 but the macros only account for ~130
        grade, _ = grade_meal(
            self.CASE, analysis(total_protein_g=1, total_carbs_g=1, total_fat_g=13)
        )
        assert grade["macros_coherent"] == 0.0

    def test_it_is_independent_of_the_reference_value(self):
        """A total that is wrong but internally consistent still passes here."""
        grade, _ = grade_meal(
            self.CASE,
            analysis(total_calories=132, total_protein_g=33, total_carbs_g=0, total_fat_g=0),
        )
        assert grade["kcal_ok"] == 0.0
        assert grade["macros_coherent"] == 1.0


class TestClarificationGrading:
    def test_asking_on_an_ambiguous_case_passes(self):
        outcome = {"status": "question", "question": "What size pizza?"}
        grade, _ = grade_meal(CASES["meal-04-pizza-ambiguous"], outcome)
        assert grade["ask_correct"] == 1.0

    def test_guessing_on_an_ambiguous_case_fails(self):
        grade, _ = grade_meal(CASES["meal-04-pizza-ambiguous"], analysis())
        assert grade["ask_correct"] == 0.0

    def test_committing_on_a_clear_case_passes(self):
        grade, _ = grade_meal(CASES["meal-01-wrap-coke"], analysis())
        assert grade["ask_correct"] == 1.0

    def test_interrogating_a_fully_specified_meal_fails(self):
        """Over-asking is a failure too -- it makes the app tedious to use."""
        outcome = {"status": "question", "question": "What kind of oats?"}
        grade, _ = grade_meal(CASES["meal-02-oats-banana"], outcome)
        assert grade["ask_correct"] == 0.0

    def test_numeric_metrics_are_unscored_on_a_question_not_zeroed(self):
        """Otherwise 'correctly asked' would be indistinguishable from 'wrong'."""
        outcome = {"status": "question", "question": "What size pizza?"}
        grade, _ = grade_meal(CASES["meal-04-pizza-ambiguous"], outcome)
        assert "kcal_ok" not in grade
        assert "protein_ok" not in grade


class TestItemisation:
    CASE = CASES["meal-11-full-english"]

    def test_a_named_breakdown_passes(self):
        grade, _ = grade_meal(self.CASE, analysis())
        assert grade["items_itemised"] == 1.0

    def test_an_unnamed_item_fails(self):
        grade, _ = grade_meal(
            self.CASE,
            analysis(
                items=[{"name": "", "calories": 100, "protein_g": 1, "carbs_g": 1, "fat_g": 1}]
            ),
        )
        assert grade["items_itemised"] == 0.0


class TestActivityGrading:
    CASE = CASES["act-01-push-day"]  # reference 230 kcal, 3 exercises

    def activity_outcome(self, **overrides):
        base = {
            "exercises": [
                {"name": "Bench press", "kind": "strength"},
                {"name": "Overhead press", "kind": "strength"},
                {"name": "Dips", "kind": "strength"},
            ],
            "total_calories_burned": 245,
            "notes": "MET-based.",
        }
        return {"status": "analysis", "analysis": {**base, **overrides}}

    def test_an_accurate_burn_passes(self):
        assert grade_activity(self.CASE, self.activity_outcome())[0]["kcal_ok"] == 1.0

    @pytest.mark.parametrize("kcal", [20, 1200])
    def test_a_wild_burn_estimate_fails(self, kcal):
        grade, _ = grade_activity(self.CASE, self.activity_outcome(total_calories_burned=kcal))
        assert grade["kcal_ok"] == 0.0

    def test_all_expected_exercises_present_passes(self):
        grade, _ = grade_activity(self.CASE, self.activity_outcome())
        assert grade["exercises_found"] == 1.0

    def test_a_missing_exercise_fails(self):
        grade, why = grade_activity(
            self.CASE,
            self.activity_outcome(exercises=[{"name": "Bench press", "kind": "strength"}]),
        )
        assert grade["exercises_found"] == 0.0
        assert "overhead press" in why["exercises_found"]

    def test_exercise_matching_is_substring_and_case_insensitive(self):
        """'bench' in the reference should match 'Barbell Bench Press'."""
        grade, _ = grade_activity(
            self.CASE,
            self.activity_outcome(
                exercises=[
                    {"name": "Barbell Bench Press", "kind": "strength"},
                    {"name": "Seated Overhead Press", "kind": "strength"},
                    {"name": "Weighted Dips", "kind": "strength"},
                ]
            ),
        )
        assert grade["exercises_found"] == 1.0

    def test_guessing_on_a_vague_session_fails(self):
        grade, _ = grade_activity(CASES["act-04-vague-gym"], self.activity_outcome())
        assert grade["ask_correct"] == 0.0


class TestDayRatingGrading:
    def test_a_rating_near_the_reference_passes(self):
        outcome = {
            "status": "analysis",
            "analysis": {"score": 8, "summary": "Good day.", "progress_note": "On track."},
        }
        assert grade_day(CASES["day-01-on-target"], outcome)[0]["score_ok"] == 1.0

    def test_a_rating_far_from_the_reference_fails(self):
        outcome = {
            "status": "analysis",
            "analysis": {"score": 3, "summary": "Bad day.", "progress_note": "Off track."},
        }
        assert grade_day(CASES["day-01-on-target"], outcome)[0]["score_ok"] == 0.0

    def test_rewarding_an_under_eating_day_fails(self):
        """The case that matters most: a huge deficit must not score as success."""
        outcome = {
            "status": "analysis",
            "analysis": {
                "score": 10,
                "summary": "Huge deficit, great work!",
                "progress_note": "Flying.",
            },
        }
        assert grade_day(CASES["day-03-under-eating"], outcome)[0]["score_ok"] == 0.0

    def test_a_missing_progress_note_fails_its_metric(self):
        outcome = {
            "status": "analysis",
            "analysis": {"score": 9, "summary": "Good day.", "progress_note": None},
        }
        grade, _ = grade_day(CASES["day-01-on-target"], outcome)
        assert grade["score_ok"] == 1.0
        assert grade["has_progress_note"] == 0.0

    @pytest.mark.parametrize("delta", [-2, -1, 0, 1, 2])
    def test_the_tolerance_band_is_two_points_each_way(self, delta):
        reference = CASES["day-04-protein-short"]["expected"]["score"]
        outcome = {
            "status": "analysis",
            "analysis": {"score": reference + delta, "summary": "s", "progress_note": "p"},
        }
        assert grade_day(CASES["day-04-protein-short"], outcome)[0]["score_ok"] == 1.0


class TestDatasetIntegrity:
    def test_every_case_has_a_grader(self):
        for case in CASES.values():
            assert case["flow"] in GRADERS, case["id"]

    def test_every_case_has_an_offline_fixture(self):
        fixtures = Path(__file__).resolve().parent.parent / "evals" / "fixtures"
        for case_id in CASES:
            assert (fixtures / f"{case_id}.json").exists(), case_id

    def test_case_ids_are_unique(self):
        path = Path(__file__).resolve().parent.parent / "evals" / "cases.jsonl"
        lines = [line for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
        assert len(lines) == len(CASES)

    def test_every_flow_declares_its_metrics_headline_first(self):
        for flow, metrics in METRICS.items():
            assert flow in GRADERS
            assert metrics, flow
            assert all(len(entry) == 2 for entry in metrics)

    def test_every_graded_metric_is_declared(self):
        """A metric a grader emits but METRICS omits never reaches the summary."""
        emitted = {
            "meal_macros": {
                "ask_correct",
                "kcal_ok",
                "protein_ok",
                "macros_coherent",
                "items_itemised",
            },
            "activity_parse": {"ask_correct", "kcal_ok", "exercises_found"},
            "day_rating": {"score_ok", "has_progress_note"},
        }
        for flow, names in emitted.items():
            assert names == {metric for metric, _ in METRICS[flow]}, flow

    def test_the_dataset_has_both_ambiguous_and_clear_cases(self):
        """A set of only-clear cases cannot measure over-asking."""
        for flow in ("meal_macros", "activity_parse"):
            cases = [c for c in CASES.values() if c["flow"] == flow]
            asks = [c for c in cases if c["expected"].get("asks_question")]
            commits = [c for c in cases if not c["expected"].get("asks_question")]
            assert asks, flow
            assert commits, flow

    def test_day_cases_span_the_rating_scale(self):
        """Otherwise a model that always says '7' would look calibrated."""
        scores = [c["expected"]["score"] for c in CASES.values() if c["flow"] == "day_rating"]
        assert min(scores) <= 3
        assert max(scores) >= 8
