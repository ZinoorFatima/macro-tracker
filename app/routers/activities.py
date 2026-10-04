import json
import sqlite3

from fastapi import APIRouter, Depends, HTTPException

from ..ai import SUBMIT_ACTIVITY_TOOL, AIUnavailableError, activity_system_prompt, run_analysis
from ..db import get_db
from ..models import ActivityAnalyzeIn, ActivityIn, ActivityUpdate
from ..validation import DateStr
from .profile import get_profile_or_404

router = APIRouter()

STEPS_KCAL_PER_STEP_PER_KG = 0.0005


@router.post("/activities/analyze")
def analyze_activity(body: ActivityAnalyzeIn, db: sqlite3.Connection = Depends(get_db)):
    profile = get_profile_or_404(db)
    system = activity_system_prompt(profile)
    messages = [{"role": m.role, "content": m.content} for m in body.messages]
    try:
        status, data = run_analysis(system, messages, SUBMIT_ACTIVITY_TOOL)
    except AIUnavailableError as e:
        raise HTTPException(status_code=503, detail=str(e)) from e
    if status == "question":
        return {"status": "question", "question": data}
    return {"status": "analysis", "analysis": data}


@router.post("/activities")
def save_activity(body: ActivityIn, db: sqlite3.Connection = Depends(get_db)):
    calories = body.calories_burned
    if body.activity_type == "steps" and body.steps and not calories:
        profile = get_profile_or_404(db)
        calories = round(body.steps * profile["weight_kg"] * STEPS_KCAL_PER_STEP_PER_KG)
    cur = db.execute(
        """INSERT INTO activities (date, activity_type, description, exercises_json, steps,
           calories_burned) VALUES (?,?,?,?,?,?)""",
        (
            body.date,
            body.activity_type,
            body.description,
            json.dumps(body.exercises) if body.exercises is not None else None,
            body.steps,
            calories,
        ),
    )
    return {"id": cur.lastrowid, **body.model_dump(), "calories_burned": calories}


@router.get("/activities")
def list_activities(date: DateStr, db: sqlite3.Connection = Depends(get_db)):
    rows = db.execute(
        "SELECT * FROM activities WHERE date=? ORDER BY id ASC", (date,)
    ).fetchall()
    return [_activity_out(r) for r in rows]


def _activity_out(row) -> dict:
    d = dict(row)
    d["exercises"] = json.loads(d.pop("exercises_json")) if d.get("exercises_json") else None
    return d


@router.put("/activities/{activity_id}")
def update_activity(
    activity_id: int, body: ActivityUpdate, db: sqlite3.Connection = Depends(get_db)
):
    row = db.execute("SELECT * FROM activities WHERE id=?", (activity_id,)).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="Activity not found")
    updates = body.model_dump(exclude_unset=True)
    if updates:
        set_clause = ", ".join(f"{k}=?" for k in updates)
        db.execute(
            f"UPDATE activities SET {set_clause} WHERE id=?", (*updates.values(), activity_id)
        )
    updated = db.execute("SELECT * FROM activities WHERE id=?", (activity_id,)).fetchone()
    return _activity_out(updated)


@router.delete("/activities/{activity_id}")
def delete_activity(activity_id: int, db: sqlite3.Connection = Depends(get_db)):
    cur = db.execute("DELETE FROM activities WHERE id=?", (activity_id,))
    if cur.rowcount == 0:
        raise HTTPException(status_code=404, detail="Activity not found")
    return {"deleted": activity_id}
