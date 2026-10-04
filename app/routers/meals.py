import json
import sqlite3

from fastapi import APIRouter, Depends, HTTPException

from ..ai import SUBMIT_MEAL_TOOL, AIUnavailableError, meal_system_prompt, run_analysis
from ..db import get_db
from ..models import MealAnalyzeIn, MealIn, MealUpdate
from ..validation import DateStr
from .profile import get_profile_or_404

router = APIRouter()


@router.post("/meals/analyze")
def analyze_meal(body: MealAnalyzeIn, db: sqlite3.Connection = Depends(get_db)):
    profile = get_profile_or_404(db)
    system = meal_system_prompt(profile, body.meal_type)
    messages = [{"role": m.role, "content": m.content} for m in body.messages]
    try:
        status, data = run_analysis(system, messages, SUBMIT_MEAL_TOOL)
    except AIUnavailableError as e:
        raise HTTPException(status_code=503, detail=str(e)) from e
    if status == "question":
        return {"status": "question", "question": data}
    # `data` is already validated and its totals recomputed from the item
    # breakdown by ai.validate_meal_analysis.
    return {"status": "analysis", "analysis": data}


@router.post("/meals")
def save_meal(body: MealIn, db: sqlite3.Connection = Depends(get_db)):
    cur = db.execute(
        """INSERT INTO meals (date, meal_type, description, calories, protein_g, carbs_g,
           fat_g, items_json, score, feedback) VALUES (?,?,?,?,?,?,?,?,?,?)""",
        (
            body.date,
            body.meal_type,
            body.description,
            body.calories,
            body.protein_g,
            body.carbs_g,
            body.fat_g,
            json.dumps(body.items) if body.items is not None else None,
            body.score,
            body.feedback,
        ),
    )
    return {"id": cur.lastrowid, **body.model_dump()}


@router.get("/meals")
def list_meals(date: DateStr, db: sqlite3.Connection = Depends(get_db)):
    rows = db.execute("SELECT * FROM meals WHERE date=? ORDER BY id ASC", (date,)).fetchall()
    return [_meal_out(r) for r in rows]


def _meal_out(row) -> dict:
    d = dict(row)
    d["items"] = json.loads(d.pop("items_json")) if d.get("items_json") else None
    return d


@router.put("/meals/{meal_id}")
def update_meal(meal_id: int, body: MealUpdate, db: sqlite3.Connection = Depends(get_db)):
    row = db.execute("SELECT * FROM meals WHERE id=?", (meal_id,)).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="Meal not found")
    # exclude_unset so a field the caller did not send is left alone, rather
    # than every `null` in a full-body PUT being read as "no change".
    updates = body.model_dump(exclude_unset=True)
    if updates:
        set_clause = ", ".join(f"{k}=?" for k in updates)
        db.execute(f"UPDATE meals SET {set_clause} WHERE id=?", (*updates.values(), meal_id))
    updated = db.execute("SELECT * FROM meals WHERE id=?", (meal_id,)).fetchone()
    return _meal_out(updated)


@router.delete("/meals/{meal_id}")
def delete_meal(meal_id: int, db: sqlite3.Connection = Depends(get_db)):
    cur = db.execute("DELETE FROM meals WHERE id=?", (meal_id,))
    if cur.rowcount == 0:
        raise HTTPException(status_code=404, detail="Meal not found")
    return {"deleted": meal_id}
