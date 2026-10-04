"""Pure functions for BMR / TDEE / daily macro target computation."""

ACTIVITY_MULTIPLIERS = {
    "sedentary": 1.2,
    "light": 1.375,
    "moderate": 1.55,
    "active": 1.725,
    "very_active": 1.9,
}

# Calorie adjustment as a fraction of TDEE
GOAL_ADJUSTMENT = {
    "lose_fat": -0.20,
    "gain_muscle": 0.10,
    "maintain": 0.0,
    "recomp": -0.05,
}

PROTEIN_PER_KG = {
    "lose_fat": 2.0,
    "gain_muscle": 1.8,
    "maintain": 1.6,
    "recomp": 2.2,
}


def bmr_mifflin(weight_kg: float, height_cm: float, age: int, gender: str) -> float:
    base = 10 * weight_kg + 6.25 * height_cm - 5 * age
    return base + (5 if gender == "male" else -161)


def compute_targets(
    age: int, gender: str, height_cm: float, weight_kg: float, activity_level: str, goal: str
) -> dict:
    bmr = bmr_mifflin(weight_kg, height_cm, age, gender)
    tdee = bmr * ACTIVITY_MULTIPLIERS[activity_level]
    calories = round(tdee * (1 + GOAL_ADJUSTMENT[goal]))
    if goal in ("lose_fat", "recomp"):
        # Never target below BMR (or an absolute floor of 1200 kcal)
        calories = max(calories, round(max(1200, bmr)))
    protein_g = round(PROTEIN_PER_KG[goal] * weight_kg)
    fat_g = round(0.25 * calories / 9)
    carbs_g = max(round((calories - protein_g * 4 - fat_g * 9) / 4), 0)
    return {
        "calorie_target": calories,
        "protein_target_g": protein_g,
        "carbs_target_g": carbs_g,
        "fat_target_g": fat_g,
    }
