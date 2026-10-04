"""Target computation: Mifflin-St Jeor BMR, TDEE and the macro split.

This is the one piece of the app with a published reference formula, so the
tests check it against hand-computed values rather than against itself.
"""

import pytest

from app.targets import (
    ACTIVITY_MULTIPLIERS,
    GOAL_ADJUSTMENT,
    PROTEIN_PER_KG,
    bmr_mifflin,
    compute_targets,
)


class TestBMR:
    def test_male_matches_reference_value(self):
        # 10(80) + 6.25(180) - 5(30) + 5 = 800 + 1125 - 150 + 5
        assert bmr_mifflin(80, 180, 30, "male") == pytest.approx(1780.0)

    def test_female_matches_reference_value(self):
        # 10(70) + 6.25(165) - 5(28) - 161 = 700 + 1031.25 - 140 - 161
        assert bmr_mifflin(70, 165, 28, "female") == pytest.approx(1430.25)

    def test_female_is_lower_than_male_at_identical_stats(self):
        male = bmr_mifflin(75, 175, 30, "male")
        female = bmr_mifflin(75, 175, 30, "female")
        assert female < male
        assert male - female == pytest.approx(166.0)

    @pytest.mark.parametrize("age", [20, 40, 60, 80])
    def test_bmr_falls_with_age(self, age):
        assert bmr_mifflin(75, 175, age, "male") > bmr_mifflin(75, 175, age + 10, "male")


class TestComputeTargets:
    def test_maintain_equals_tdee(self):
        targets = compute_targets(30, "male", 180, 80, "moderate", "maintain")
        expected = bmr_mifflin(80, 180, 30, "male") * ACTIVITY_MULTIPLIERS["moderate"]
        assert targets["calorie_target"] == round(expected)

    def test_lose_fat_is_a_deficit_against_maintain(self):
        maintain = compute_targets(30, "male", 180, 80, "moderate", "maintain")
        cut = compute_targets(30, "male", 180, 80, "moderate", "lose_fat")
        assert cut["calorie_target"] < maintain["calorie_target"]

    def test_gain_muscle_is_a_surplus_against_maintain(self):
        maintain = compute_targets(30, "male", 180, 80, "moderate", "maintain")
        bulk = compute_targets(30, "male", 180, 80, "moderate", "gain_muscle")
        assert bulk["calorie_target"] > maintain["calorie_target"]

    @pytest.mark.parametrize("level", sorted(ACTIVITY_MULTIPLIERS))
    def test_every_activity_level_is_supported(self, level):
        targets = compute_targets(30, "male", 180, 80, level, "maintain")
        assert targets["calorie_target"] > 0

    @pytest.mark.parametrize("goal", sorted(GOAL_ADJUSTMENT))
    def test_every_goal_is_supported(self, goal):
        targets = compute_targets(30, "male", 180, 80, "moderate", goal)
        assert set(targets) == {
            "calorie_target",
            "protein_target_g",
            "carbs_target_g",
            "fat_target_g",
        }
        assert all(value >= 0 for value in targets.values())

    def test_more_activity_means_more_calories(self):
        levels = ["sedentary", "light", "moderate", "active", "very_active"]
        calories = [
            compute_targets(30, "male", 180, 80, level, "maintain")["calorie_target"]
            for level in levels
        ]
        assert calories == sorted(calories)
        assert len(set(calories)) == len(calories)

    @pytest.mark.parametrize("goal", sorted(PROTEIN_PER_KG))
    def test_protein_scales_with_bodyweight(self, goal):
        targets = compute_targets(30, "male", 180, 80, "moderate", goal)
        assert targets["protein_target_g"] == round(PROTEIN_PER_KG[goal] * 80)

    def test_macros_reconcile_with_the_calorie_target(self):
        """Protein*4 + carbs*4 + fat*9 should land on the calorie target.

        Rounding each macro to a whole gram costs a few calories; anything
        larger than that means the split is wrong, not just rounded.
        """
        targets = compute_targets(30, "male", 180, 80, "moderate", "maintain")
        from_macros = (
            targets["protein_target_g"] * 4
            + targets["carbs_target_g"] * 4
            + targets["fat_target_g"] * 9
        )
        assert abs(from_macros - targets["calorie_target"]) <= 10

    def test_fat_is_about_a_quarter_of_calories(self):
        targets = compute_targets(30, "male", 180, 80, "moderate", "maintain")
        fat_calories = targets["fat_target_g"] * 9
        assert fat_calories / targets["calorie_target"] == pytest.approx(0.25, abs=0.01)


class TestSafetyFloors:
    """A cut must never be prescribed below BMR, or below 1200 kcal."""

    def test_cut_never_goes_below_bmr(self):
        # A small, sedentary person on a cut is the case that trips the floor:
        # 20% off a low TDEE lands under BMR.
        bmr = bmr_mifflin(45, 150, 25, "female")
        targets = compute_targets(25, "female", 150, 45, "sedentary", "lose_fat")
        assert targets["calorie_target"] >= round(bmr)

    def test_absolute_floor_of_1200_kcal(self):
        targets = compute_targets(80, "female", 140, 40, "sedentary", "lose_fat")
        assert targets["calorie_target"] >= 1200

    def test_recomp_also_respects_the_floor(self):
        bmr = bmr_mifflin(45, 150, 25, "female")
        targets = compute_targets(25, "female", 150, 45, "sedentary", "recomp")
        assert targets["calorie_target"] >= round(max(1200, bmr))

    def test_surplus_goals_are_not_floored_upward(self):
        """The floor only applies to cuts -- a bulk should be pure TDEE math."""
        bulk = compute_targets(30, "male", 180, 80, "moderate", "gain_muscle")
        tdee = bmr_mifflin(80, 180, 30, "male") * ACTIVITY_MULTIPLIERS["moderate"]
        assert bulk["calorie_target"] == round(tdee * 1.10)

    def test_carbs_never_go_negative(self):
        """A high-protein cut on a small frame can exhaust the calorie budget."""
        targets = compute_targets(80, "female", 140, 40, "sedentary", "recomp")
        assert targets["carbs_target_g"] >= 0
