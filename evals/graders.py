"""Graders for the eval cases.

Every grader here is **programmatic**: nutrition estimates are numbers with a
defensible reference value, so a cheap deterministic check measures the thing we
actually care about better than a judge would. The one place judgement is
genuinely needed -- whether a day rating's prose names the right problem -- is
graded by an optional LLM judge in `judge.py`, which is off unless asked for.

Tolerances are wide on purpose. Portion estimation from a text description has
real irreducible variance: two dietitians shown "a grilled chicken wrap" will
not agree within 10%. A tolerance tighter than the task's own noise floor would
report model noise as regression.
"""

from __future__ import annotations

# Calorie tolerance for a meal, as a fraction of the reference value. A wrap
# estimated at 500 or 820 kcal against a 660 reference is a usable answer for a
# daily tracker; 1500 is not.
MEAL_KCAL_TOLERANCE = 0.25

# Protein matters more than total calories for the app's own advice, but is also
# harder to pin down for mixed dishes, so it gets its own, looser band.
MEAL_PROTEIN_TOLERANCE = 0.35

# Absolute floor, for near-zero cases. A black coffee referenced at 5 kcal
# cannot be graded on a percentage.
MEAL_KCAL_FLOOR = 40

# MET-based burn estimation is looser still -- published MET values for "moderate
# resistance training" span roughly a factor of two.
ACTIVITY_KCAL_TOLERANCE = 0.40
ACTIVITY_KCAL_FLOOR = 60

# A day rating is a human-ish judgement on a 1-10 scale; agreement within two
# points is the realistic bar for two reasonable coaches.
DAY_SCORE_TOLERANCE = 2

# Internal coherence: protein*4 + carbs*4 + fat*9 should reconstruct the stated
# calorie total. This needs no ground truth, so it catches a whole class of
# arithmetic incoherence that a reference-value check can miss.
MACRO_COHERENCE_TOLERANCE = 0.20


def _within(actual: float, reference: float, tolerance: float, floor: float) -> bool:
    """True when `actual` is within `tolerance` of `reference`.

    `floor` provides an absolute allowance so near-zero references are gradeable
    at all: a 5 kcal reference with a 25% band would be a 1.25 kcal window.
    """
    allowed = max(reference * tolerance, floor)
    return abs(actual - reference) <= allowed


def grade_meal(case: dict, outcome: dict) -> tuple[dict, dict]:
    """Grade one meal-analysis case.

    `outcome` is `{"status": "question"|"analysis", ...}` as returned by
    `ai.run_analysis`. Returns `(grade, explanation)`, both keyed by metric id.
    """
    expected = case["expected"]
    wants_question = bool(expected.get("asks_question"))
    asked = outcome["status"] == "question"
    grade: dict[str, float] = {}
    why: dict[str, str] = {}

    # Did it make the right call about asking vs committing? This is scored on
    # every case, in both directions: inventing a number for "pizza" and
    # interrogating a fully specified meal are both failures.
    grade["ask_correct"] = float(asked == wants_question)
    why["ask_correct"] = (
        f"expected {'a question' if wants_question else 'a committed analysis'}, "
        f"got {'a question' if asked else 'a committed analysis'}"
    )

    if asked:
        # Nothing numeric to grade. Leave the numeric metrics unscored rather
        # than recording a 0, which would conflate "correctly asked" with "got
        # the calories wrong".
        why["detail"] = f"asked: {outcome.get('question', '')!r}"
        return grade, why

    analysis = outcome["analysis"]
    kcal = analysis["total_calories"]
    protein = analysis["total_protein_g"]

    if "kcal" in expected:
        grade["kcal_ok"] = float(
            _within(kcal, expected["kcal"], MEAL_KCAL_TOLERANCE, MEAL_KCAL_FLOOR)
        )
        why["kcal_ok"] = f"{kcal} kcal vs reference {expected['kcal']}"

    if "protein_g" in expected:
        grade["protein_ok"] = float(
            _within(protein, expected["protein_g"], MEAL_PROTEIN_TOLERANCE, 8)
        )
        why["protein_ok"] = f"{protein}g protein vs reference {expected['protein_g']}"

    # Internal coherence of the macro split against the calorie total.
    from_macros = protein * 4 + analysis["total_carbs_g"] * 4 + analysis["total_fat_g"] * 9
    coherent = _within(from_macros, kcal, MACRO_COHERENCE_TOLERANCE, MEAL_KCAL_FLOOR)
    grade["macros_coherent"] = float(coherent)
    why["macros_coherent"] = (
        f"macros reconstruct to {from_macros:.0f} kcal against a stated {kcal}"
    )

    # Every item in the breakdown should be named and non-negative, and the
    # breakdown should actually decompose the meal rather than returning one
    # catch-all row for a multi-component description.
    items = analysis.get("items") or []
    grade["items_itemised"] = float(len(items) >= 1 and all(i.get("name") for i in items))
    why["items_itemised"] = f"{len(items)} item(s) in the breakdown"

    return grade, why


def grade_activity(case: dict, outcome: dict) -> tuple[dict, dict]:
    """Grade one activity-parsing case."""
    expected = case["expected"]
    wants_question = bool(expected.get("asks_question"))
    asked = outcome["status"] == "question"
    grade: dict[str, float] = {}
    why: dict[str, str] = {}

    grade["ask_correct"] = float(asked == wants_question)
    why["ask_correct"] = (
        f"expected {'a question' if wants_question else 'a committed analysis'}, "
        f"got {'a question' if asked else 'a committed analysis'}"
    )
    if asked:
        why["detail"] = f"asked: {outcome.get('question', '')!r}"
        return grade, why

    analysis = outcome["analysis"]
    burned = analysis["total_calories_burned"]

    if "kcal" in expected:
        grade["kcal_ok"] = float(
            _within(burned, expected["kcal"], ACTIVITY_KCAL_TOLERANCE, ACTIVITY_KCAL_FLOOR)
        )
        why["kcal_ok"] = f"{burned} kcal burned vs reference {expected['kcal']}"

    if "exercises" in expected:
        # Substring match, case-folded: the reference says "bench", and
        # "Bench press" or "Barbell bench press" should both count.
        found = " | ".join(e["name"] for e in analysis.get("exercises", [])).lower()
        missing = [name for name in expected["exercises"] if name.lower() not in found]
        grade["exercises_found"] = float(not missing)
        why["exercises_found"] = f"found {found!r}" + (
            f"; missing {missing}" if missing else ""
        )

    return grade, why


def grade_day(case: dict, outcome: dict) -> tuple[dict, dict]:
    """Grade one day-rating case."""
    rating = outcome["analysis"]
    expected = case["expected"]
    score = rating["score"]
    grade = {
        "score_ok": float(abs(score - expected["score"]) <= DAY_SCORE_TOLERANCE),
        "has_progress_note": float(bool(rating.get("progress_note"))),
    }
    why = {
        "score_ok": f"rated {score}/10 vs reference {expected['score']}/10",
        "has_progress_note": "progress note present"
        if rating.get("progress_note")
        else "no progress note returned",
    }
    return grade, why


GRADERS = {
    "meal_macros": grade_meal,
    "activity_parse": grade_activity,
    "day_rating": grade_day,
}

# Metric declarations per flow, headline metric first. The runner's summary and
# the exit code both track the first entry.
METRICS = {
    "meal_macros": [
        ("kcal_ok", "Calories within 25% of the reference"),
        ("ask_correct", "Asked for clarification exactly when warranted"),
        ("protein_ok", "Protein within 35% of the reference"),
        ("macros_coherent", "Macro split reconstructs the calorie total"),
        ("items_itemised", "Returned a named per-item breakdown"),
    ],
    "activity_parse": [
        ("kcal_ok", "Burn within 40% of the MET-based reference"),
        ("exercises_found", "Every expected exercise appears"),
        ("ask_correct", "Asked for clarification exactly when warranted"),
    ],
    "day_rating": [
        ("score_ok", "Rating within 2 points of the reference"),
        ("has_progress_note", "Returned a progress note"),
    ],
}
