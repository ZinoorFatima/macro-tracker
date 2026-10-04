import sqlite3

from fastapi import APIRouter, Depends, HTTPException

from ..db import get_db
from ..models import ProfileIn, ProfileUpdate, WeightIn
from ..targets import compute_targets
from ..validation import ValidationProblem, check_goal_consistency

router = APIRouter()


def _row_to_profile(row) -> dict:
    return dict(row)


def _recompute_and_store(db: sqlite3.Connection, p: dict) -> dict:
    targets = compute_targets(
        p["age"], p["gender"], p["height_cm"], p["weight_kg"], p["activity_level"], p["goal"]
    )
    db.execute(
        """UPDATE profile SET age=?, gender=?, height_cm=?, weight_kg=?, activity_level=?,
           goal=?, timeline_weeks=?, target_weight_kg=?, calorie_target=?, protein_target_g=?,
           carbs_target_g=?, fat_target_g=? WHERE id=1""",
        (
            p["age"],
            p["gender"],
            p["height_cm"],
            p["weight_kg"],
            p["activity_level"],
            p["goal"],
            p["timeline_weeks"],
            p["target_weight_kg"],
            targets["calorie_target"],
            targets["protein_target_g"],
            targets["carbs_target_g"],
            targets["fat_target_g"],
        ),
    )
    return {**p, **targets}


def get_profile_or_404(db: sqlite3.Connection) -> dict:
    row = db.execute("SELECT * FROM profile WHERE id=1").fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="Profile not set up yet")
    return dict(row)


@router.get("/profile")
def get_profile(db: sqlite3.Connection = Depends(get_db)):
    return get_profile_or_404(db)


@router.post("/profile")
def create_profile(body: ProfileIn, db: sqlite3.Connection = Depends(get_db)):
    targets = compute_targets(
        body.age, body.gender, body.height_cm, body.weight_kg, body.activity_level, body.goal
    )
    db.execute(
        """INSERT INTO profile (id, age, gender, height_cm, weight_kg, activity_level, goal,
           timeline_weeks, target_weight_kg, calorie_target, protein_target_g, carbs_target_g,
           fat_target_g)
           VALUES (1,?,?,?,?,?,?,?,?,?,?,?,?)
           ON CONFLICT(id) DO UPDATE SET age=excluded.age, gender=excluded.gender,
           height_cm=excluded.height_cm, weight_kg=excluded.weight_kg,
           activity_level=excluded.activity_level, goal=excluded.goal,
           timeline_weeks=excluded.timeline_weeks, target_weight_kg=excluded.target_weight_kg,
           calorie_target=excluded.calorie_target, protein_target_g=excluded.protein_target_g,
           carbs_target_g=excluded.carbs_target_g, fat_target_g=excluded.fat_target_g""",
        (
            body.age,
            body.gender,
            body.height_cm,
            body.weight_kg,
            body.activity_level,
            body.goal,
            body.timeline_weeks,
            body.target_weight_kg,
            targets["calorie_target"],
            targets["protein_target_g"],
            targets["carbs_target_g"],
            targets["fat_target_g"],
        ),
    )
    return {**body.model_dump(), **targets}


@router.put("/profile")
def update_profile(body: ProfileUpdate, db: sqlite3.Connection = Depends(get_db)):
    profile = get_profile_or_404(db)
    # exclude_unset distinguishes "clear the timeline" (an explicit null) from
    # "don't touch the timeline" (field absent).
    profile.update(body.model_dump(exclude_unset=True))
    try:
        check_goal_consistency(
            profile["goal"], profile["weight_kg"], profile["target_weight_kg"]
        )
    except ValidationProblem as e:
        raise HTTPException(status_code=422, detail=str(e)) from e
    return _recompute_and_store(db, profile)


@router.post("/weight")
def log_weight(body: WeightIn, db: sqlite3.Connection = Depends(get_db)):
    db.execute(
        """INSERT INTO weight_logs (date, weight_kg) VALUES (?,?)
           ON CONFLICT(date) DO UPDATE SET weight_kg=excluded.weight_kg""",
        (body.date, body.weight_kg),
    )
    # Latest weight (by date) becomes the profile's current weight; targets recompute
    latest = db.execute(
        "SELECT weight_kg FROM weight_logs ORDER BY date DESC LIMIT 1"
    ).fetchone()
    profile_row = db.execute("SELECT * FROM profile WHERE id=1").fetchone()
    result = {"date": body.date, "weight_kg": body.weight_kg}
    if profile_row is not None and latest is not None:
        profile = dict(profile_row)
        profile["weight_kg"] = latest["weight_kg"]
        updated = _recompute_and_store(db, profile)
        result["targets"] = {
            k: updated[k]
            for k in ("calorie_target", "protein_target_g", "carbs_target_g", "fat_target_g")
        }
    return result


@router.get("/weight")
def list_weights(db: sqlite3.Connection = Depends(get_db)):
    rows = db.execute("SELECT date, weight_kg FROM weight_logs ORDER BY date ASC").fetchall()
    return [dict(r) for r in rows]
