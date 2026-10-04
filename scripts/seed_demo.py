#!/usr/bin/env python
"""Fill the database with two weeks of plausible demo data.

    python scripts/seed_demo.py              # seed, keeping any existing rows
    python scripts/seed_demo.py --reset      # wipe first
    python scripts/seed_demo.py --days 30

Exists for two reasons. An empty tracker is a bad first impression -- the
dashboard, the history chart and the weight trend all need data before they show
anything. And the AI features need an API key, so without seeded data someone
cloning this repo to look at it would see nothing but empty states.

Everything written here is hand-authored: the meal feedback and day ratings are
the kind of text the model produces, not model output. No API calls are made.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import random
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.db import DB_PATH, init_db  # noqa: E402
from app.targets import compute_targets  # noqa: E402

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

# (description, kcal, protein, carbs, fat, score, feedback)
BREAKFASTS = [
    (
        "Oats with semi-skimmed milk, banana and honey",
        610,
        19,
        105,
        12,
        7,
        "Good volume and fibre, but carb-heavy for your protein target. Stir in a scoop "
        "of whey or swap half the milk for greek yoghurt to add about 20g of protein.",
    ),
    (
        "Three-egg omelette with spinach and feta",
        420,
        28,
        6,
        32,
        8,
        "Strong protein start and very satiating. The feta is most of the fat here -- "
        "halving it would save about 80 kcal without changing the dish much.",
    ),
    (
        "Greek yoghurt with berries and granola",
        390,
        22,
        48,
        10,
        8,
        "Well balanced. Granola is the hidden calorie here at roughly 450 kcal per 100g, "
        "so weigh it rather than pouring.",
    ),
    (
        "Two slices of buttered toast with jam",
        380,
        8,
        58,
        14,
        4,
        "Almost no protein and you will be hungry by eleven. Adding two poached eggs "
        "would take this to 20g of protein for about 160 more calories.",
    ),
    (
        "Protein shake and a banana",
        265,
        25,
        30,
        3,
        7,
        "Efficient but low on fibre and volume. Fine on a rushed morning; not something "
        "to rely on every day.",
    ),
]

LUNCHES = [
    (
        "Grilled chicken wrap with mayo and a diet coke",
        530,
        33,
        46,
        23,
        6,
        "Solid protein. Asking for the mayo on the side saves roughly 90 kcal, and the "
        "diet coke is already the right call over the full-sugar version.",
    ),
    (
        "Chicken, rice and broccoli",
        660,
        68,
        62,
        14,
        9,
        "Close to ideal for your cut: 68g of protein, plenty of volume, controlled fat. "
        "Drop the rice to 120g if you want to trim further.",
    ),
    (
        "Tuna and sweetcorn jacket potato",
        540,
        34,
        72,
        10,
        7,
        "Good protein-to-calorie ratio and very filling. Check whether the tuna was in "
        "oil or brine -- oil adds about 120 kcal per tin.",
    ),
    (
        "Leftover chilli with rice",
        620,
        38,
        70,
        18,
        7,
        "Good protein from the beans and mince together. Swapping half the rice for "
        "cauliflower rice would cut about 120 kcal with no loss of volume.",
    ),
    (
        "Cheese and tomato pasta",
        700,
        22,
        95,
        24,
        5,
        "Tasty but light on protein for the calories. Adding 150g of chicken would take "
        "it to 55g of protein for about 200 more calories -- a much better trade.",
    ),
    (
        "Chicken caesar salad",
        480,
        36,
        14,
        30,
        7,
        "Protein is good. The dressing and croutons are carrying most of the fat -- "
        "asking for half the dressing saves about 100 kcal.",
    ),
]

DINNERS = [
    (
        "Salmon fillet with sweet potato and greens",
        520,
        40,
        38,
        22,
        9,
        "Excellent: complete protein, slow carbs and omega-3 fats. Nothing to change.",
    ),
    (
        "Chicken biryani with raita",
        820,
        38,
        88,
        36,
        4,
        "Calorie-dense -- the ghee is doing most of the damage. A 250g portion with extra "
        "raita would fit your target comfortably.",
    ),
    (
        "Stir-fried beef with noodles and vegetables",
        680,
        42,
        72,
        24,
        7,
        "Good balance. Watch the sauce: a tablespoon of hoisin is about 60 kcal of sugar.",
    ),
    (
        "Homemade lentil dahl with roti",
        590,
        24,
        82,
        18,
        7,
        "Great fibre and a decent plant protein base. Pairing it with yoghurt would push "
        "protein up another 10g for very little cost.",
    ),
    (
        "Takeaway burger and chips",
        1180,
        45,
        120,
        58,
        2,
        "Two thirds of your daily calories in one meal. If this is a weekly thing, "
        "ordering it without the mayo and sharing the chips halves the damage.",
    ),
    (
        "Roast chicken with potatoes and vegetables",
        640,
        52,
        54,
        22,
        8,
        "Good protein and real food. Roasting the potatoes in a teaspoon of oil rather "
        "than a few tablespoons saves about 150 kcal.",
    ),
]

SNACKS = [
    (
        "Greek yoghurt with blueberries",
        140,
        18,
        12,
        0,
        9,
        "Excellent snack for your goal: 18g of protein for 140 kcal.",
    ),
    (
        "Apple and a handful of almonds",
        230,
        6,
        26,
        14,
        7,
        "Good fibre and fats, but almonds are easy to over-pour -- 30g is a handful, "
        "60g is 350 kcal.",
    ),
    (
        "Protein bar",
        210,
        20,
        22,
        7,
        6,
        "Convenient protein. Check the sugar alcohols if they upset your stomach.",
    ),
    ("Black americano", 5, 0, 0, 0, 9, "Effectively free against your targets."),
    (
        "Two squares of dark chocolate",
        110,
        1,
        9,
        8,
        7,
        "Perfectly fine as a planned treat -- it fits inside your target and helps you "
        "stick to the plan.",
    ),
]

WORKOUTS = [
    (
        "Push day: bench press, overhead press, dips",
        245,
        [
            {"name": "Bench press", "kind": "strength", "sets": 4, "reps": 8, "weight_kg": 60},
            {
                "name": "Overhead press",
                "kind": "strength",
                "sets": 3,
                "reps": 10,
                "weight_kg": 30,
            },
            {"name": "Dips", "kind": "strength", "sets": 3, "reps": 10},
        ],
    ),
    (
        "Pull day: deadlifts, rows, lat pulldowns",
        280,
        [
            {"name": "Deadlift", "kind": "strength", "sets": 4, "reps": 5, "weight_kg": 90},
            {"name": "Barbell row", "kind": "strength", "sets": 4, "reps": 8, "weight_kg": 50},
            {
                "name": "Lat pulldown",
                "kind": "strength",
                "sets": 3,
                "reps": 12,
                "weight_kg": 40,
            },
        ],
    ),
    (
        "Leg day: squats, lunges, calf raises",
        310,
        [
            {"name": "Back squat", "kind": "strength", "sets": 5, "reps": 5, "weight_kg": 80},
            {"name": "Walking lunges", "kind": "strength", "sets": 3, "reps": 20},
            {"name": "Calf raises", "kind": "strength", "sets": 4, "reps": 15, "weight_kg": 40},
        ],
    ),
    ("5k run", 300, [{"name": "Running, 5k", "kind": "cardio", "duration_min": 28}]),
    (
        "Spin class",
        420,
        [{"name": "Indoor cycling class", "kind": "cardio", "duration_min": 45}],
    ),
    ("Hatha yoga class", 175, [{"name": "Hatha yoga", "kind": "cardio", "duration_min": 60}]),
]

RATINGS = {
    "great": (
        9,
        "Strong day. Calories landed close to target and you cleared your "
        "protein goal, with good food quality across the board -- whole foods "
        "and no liquid sugar. Training and steps both logged.",
        "Weight is trending down at a rate that protects muscle. At this pace "
        "you will reach your target comfortably inside the 12-week timeline.",
    ),
    "good": (
        7,
        "Good day overall. Calories were inside a sensible range and protein "
        "came close to target. One meal was heavier than planned, but the rest "
        "of the day absorbed it.",
        "Still on track. The weekly average is what matters, and this sits "
        "comfortably inside it.",
    ),
    "mixed": (
        5,
        "Calories were controlled but protein fell well short of target, "
        "because the day was carb-led. Swapping one carb-heavy meal for a "
        "protein-led one would close most of the gap for similar calories.",
        "On a cut, consistently missing protein is the main risk to the muscle "
        "you are trying to keep. Calorie control clearly is not your problem.",
    ),
    "poor": (
        3,
        "A hard day against your targets: calories ran well over and most of "
        "the intake was fried or takeaway, with no activity to offset it. "
        "Protein was actually fine -- the problem is the calories around it.",
        "One day will not undo your progress, but a run of these will stall the "
        "timeline. Get back to your usual pattern tomorrow rather than trying to "
        "compensate with an extreme deficit.",
    ),
}


def seed(connection: sqlite3.Connection, days: int, seed_value: int) -> dict:
    rng = random.Random(seed_value)
    targets = compute_targets(
        PROFILE["age"],
        PROFILE["gender"],
        PROFILE["height_cm"],
        PROFILE["weight_kg"],
        PROFILE["activity_level"],
        PROFILE["goal"],
    )

    today = dt.date.today()
    # Weight drifts down with day-to-day noise, the way a real log looks.
    start_weight = 71.8
    end_weight = 69.6
    counts = {"meals": 0, "activities": 0, "weights": 0, "ratings": 0}

    for offset in range(days - 1, -1, -1):
        day = today - dt.timedelta(days=offset)
        date = day.isoformat()
        progress = (days - 1 - offset) / max(days - 1, 1)
        is_weekend = day.weekday() >= 5

        # A weekend day every so often goes badly, which is what makes the
        # history view and the day ratings worth looking at.
        blowout = is_weekend and rng.random() < 0.5

        meals = [rng.choice(BREAKFASTS), rng.choice(LUNCHES), rng.choice(DINNERS)]
        if rng.random() < 0.75:
            meals.append(rng.choice(SNACKS))
        if blowout:
            meals[2] = DINNERS[4]  # takeaway burger and chips

        # `meals` is 3 or 4 entries against 4 labels, so the zip is meant to stop
        # at the shorter one -- a snack is not logged every day.
        meal_types = ("breakfast", "lunch", "dinner", "snack")
        for meal_type, meal in zip(meal_types, meals, strict=False):
            description, kcal, protein, carbs, fat, score, feedback = meal
            connection.execute(
                """INSERT INTO meals (date, meal_type, description, calories, protein_g,
                   carbs_g, fat_g, items_json, score, feedback)
                   VALUES (?,?,?,?,?,?,?,?,?,?)""",
                (
                    date,
                    meal_type,
                    description,
                    kcal,
                    protein,
                    carbs,
                    fat,
                    None,
                    score,
                    feedback,
                ),
            )
            counts["meals"] += 1

        if blowout:
            connection.execute(
                "INSERT INTO activities (date, activity_type, description, "
                "calories_burned) VALUES (?,?,?,?)",
                (date, "rest", "", 0),
            )
            counts["activities"] += 1
        else:
            if rng.random() < 0.75:
                description, burned, exercises = rng.choice(WORKOUTS)
                connection.execute(
                    """INSERT INTO activities (date, activity_type, description,
                       exercises_json, calories_burned) VALUES (?,?,?,?,?)""",
                    (date, "workout", description, json.dumps(exercises), burned),
                )
                counts["activities"] += 1
            steps = rng.randint(5200, 12800)
            connection.execute(
                """INSERT INTO activities (date, activity_type, description, steps,
                   calories_burned) VALUES (?,?,?,?,?)""",
                (date, "steps", "", steps, round(steps * PROFILE["weight_kg"] * 0.0005)),
            )
            counts["activities"] += 1

        # Weighed most mornings, not every one.
        if rng.random() < 0.8:
            trend = start_weight + (end_weight - start_weight) * progress
            weight = round(trend + rng.uniform(-0.35, 0.35), 1)
            connection.execute(
                "INSERT INTO weight_logs (date, weight_kg) VALUES (?,?) "
                "ON CONFLICT(date) DO UPDATE SET weight_kg=excluded.weight_kg",
                (date, weight),
            )
            counts["weights"] += 1

        # Older days are rated; the last couple are left unrated so the
        # "Rate my day" button has something to do.
        if offset >= 2:
            total_kcal = sum(m[1] for m in meals)
            total_protein = sum(m[2] for m in meals)
            if blowout or total_kcal > targets["calorie_target"] * 1.5:
                band = "poor"
            elif total_protein < targets["protein_target_g"] * 0.6:
                band = "mixed"
            elif total_kcal <= targets["calorie_target"] * 1.15:
                band = "great"
            else:
                band = "good"
            score, summary, progress_note = RATINGS[band]
            connection.execute(
                """INSERT INTO day_ratings (date, score, summary, progress_note)
                   VALUES (?,?,?,?) ON CONFLICT(date) DO UPDATE SET
                   score=excluded.score, summary=excluded.summary,
                   progress_note=excluded.progress_note""",
                (date, score, summary, progress_note),
            )
            counts["ratings"] += 1

    # The profile's current weight is the most recent log, as the app maintains it.
    latest = connection.execute(
        "SELECT weight_kg FROM weight_logs ORDER BY date DESC LIMIT 1"
    ).fetchone()
    current_weight = latest[0] if latest else PROFILE["weight_kg"]
    targets = compute_targets(
        PROFILE["age"],
        PROFILE["gender"],
        PROFILE["height_cm"],
        current_weight,
        PROFILE["activity_level"],
        PROFILE["goal"],
    )
    connection.execute(
        """INSERT INTO profile (id, age, gender, height_cm, weight_kg, activity_level,
           goal, timeline_weeks, target_weight_kg, calorie_target, protein_target_g,
           carbs_target_g, fat_target_g) VALUES (1,?,?,?,?,?,?,?,?,?,?,?,?)
           ON CONFLICT(id) DO UPDATE SET age=excluded.age, gender=excluded.gender,
           height_cm=excluded.height_cm, weight_kg=excluded.weight_kg,
           activity_level=excluded.activity_level, goal=excluded.goal,
           timeline_weeks=excluded.timeline_weeks,
           target_weight_kg=excluded.target_weight_kg,
           calorie_target=excluded.calorie_target,
           protein_target_g=excluded.protein_target_g,
           carbs_target_g=excluded.carbs_target_g,
           fat_target_g=excluded.fat_target_g""",
        (
            PROFILE["age"],
            PROFILE["gender"],
            PROFILE["height_cm"],
            current_weight,
            PROFILE["activity_level"],
            PROFILE["goal"],
            PROFILE["timeline_weeks"],
            PROFILE["target_weight_kg"],
            targets["calorie_target"],
            targets["protein_target_g"],
            targets["carbs_target_g"],
            targets["fat_target_g"],
        ),
    )
    return counts | {"targets": targets, "current_weight": current_weight}


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--days", type=int, default=14, help="days of history to write")
    parser.add_argument(
        "--reset", action="store_true", help="delete existing rows before seeding"
    )
    parser.add_argument(
        "--seed", type=int, default=7, help="RNG seed, so a given run is reproducible"
    )
    args = parser.parse_args()

    if args.days < 1:
        print("--days must be at least 1", file=sys.stderr)
        return 2

    init_db()
    with sqlite3.connect(DB_PATH) as connection:
        if args.reset:
            for table in ("meals", "activities", "weight_logs", "day_ratings", "profile"):
                connection.execute(f"DELETE FROM {table}")
            print("Cleared existing data.")
        result = seed(connection, args.days, args.seed)

    print(f"Seeded {args.days} days into {DB_PATH}")
    print(
        f"  {result['meals']} meals, {result['activities']} activities, "
        f"{result['weights']} weigh-ins, {result['ratings']} rated days"
    )
    print(
        f"  Demo profile: 28yo female, 165cm, {result['current_weight']}kg, "
        f"cutting to 62kg over 12 weeks"
    )
    print(
        f"  Daily targets: {result['targets']['calorie_target']} kcal, "
        f"{result['targets']['protein_target_g']}g protein, "
        f"{result['targets']['carbs_target_g']}g carbs, "
        f"{result['targets']['fat_target_g']}g fat"
    )
    print("\nStart the app with run.bat (Windows) or:")
    print("  python -m uvicorn app.main:app --reload")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
