import sqlite3

from fastapi import APIRouter, Depends, HTTPException

from ..ai import AIUnavailableError, day_rating_system_prompt, run_day_rating
from ..db import get_db
from ..validation import DateBound, DateStr, ValidationProblem, check_date_range
from .activities import _activity_out
from .meals import _meal_out
from .profile import get_profile_or_404

router = APIRouter()


def _day_totals(db: sqlite3.Connection, date: str) -> dict:
    m = db.execute(
        """SELECT COALESCE(SUM(calories),0) c, COALESCE(SUM(protein_g),0) p,
           COALESCE(SUM(carbs_g),0) cb, COALESCE(SUM(fat_g),0) f
           FROM meals WHERE date=?""",
        (date,),
    ).fetchone()
    a = db.execute(
        "SELECT COALESCE(SUM(calories_burned),0) b FROM activities WHERE date=?", (date,)
    ).fetchone()
    return {
        "calories": round(m["c"]),
        "protein_g": round(m["p"], 1),
        "carbs_g": round(m["cb"], 1),
        "fat_g": round(m["f"], 1),
        "calories_burned": round(a["b"]),
    }


@router.get("/days/{date}/summary")
def day_summary(date: DateStr, db: sqlite3.Connection = Depends(get_db)):
    profile = get_profile_or_404(db)
    meals = [
        _meal_out(r)
        for r in db.execute(
            "SELECT * FROM meals WHERE date=? ORDER BY id ASC", (date,)
        ).fetchall()
    ]
    activities = [
        _activity_out(r)
        for r in db.execute(
            "SELECT * FROM activities WHERE date=? ORDER BY id ASC", (date,)
        ).fetchall()
    ]
    rating_row = db.execute("SELECT * FROM day_ratings WHERE date=?", (date,)).fetchone()
    weight_row = db.execute(
        "SELECT weight_kg FROM weight_logs WHERE date=?", (date,)
    ).fetchone()
    return {
        "date": date,
        "totals": _day_totals(db, date),
        "targets": {
            "calories": profile["calorie_target"],
            "protein_g": profile["protein_target_g"],
            "carbs_g": profile["carbs_target_g"],
            "fat_g": profile["fat_target_g"],
        },
        "meals": meals,
        "activities": activities,
        "rating": dict(rating_row) if rating_row else None,
        "weight_kg": weight_row["weight_kg"] if weight_row else None,
    }


@router.post("/days/{date}/rate")
def rate_day(date: DateStr, db: sqlite3.Connection = Depends(get_db)):
    profile = get_profile_or_404(db)
    totals = _day_totals(db, date)
    meals = db.execute(
        "SELECT meal_type, description, calories, protein_g, carbs_g, fat_g, score "
        "FROM meals WHERE date=? ORDER BY id ASC",
        (date,),
    ).fetchall()
    activities = db.execute(
        "SELECT activity_type, description, steps, calories_burned "
        "FROM activities WHERE date=? ORDER BY id ASC",
        (date,),
    ).fetchall()
    weights = db.execute(
        "SELECT date, weight_kg FROM weight_logs WHERE date<=? ORDER BY date DESC LIMIT 14",
        (date,),
    ).fetchall()

    if not meals and not activities:
        raise HTTPException(status_code=400, detail="Nothing logged for this day yet")

    lines = [f"DAY: {date}", "", "MEALS:"]
    if meals:
        for m in meals:
            score = f", meal score {m['score']}/10" if m["score"] else ""
            lines.append(
                f"- [{m['meal_type']}] {m['description']}: {m['calories']} kcal, "
                f"{m['protein_g']}g P, {m['carbs_g']}g C, {m['fat_g']}g F{score}"
            )
    else:
        lines.append("- (no meals logged)")
    lines += ["", "ACTIVITIES:"]
    if activities:
        for a in activities:
            if a["activity_type"] == "steps":
                lines.append(f"- {a['steps']} steps (~{a['calories_burned']} kcal)")
            elif a["activity_type"] == "rest":
                lines.append("- Rest day (no training)")
            else:
                lines.append(f"- Workout: {a['description']} (~{a['calories_burned']} kcal)")
    else:
        lines.append("- (no activity logged)")
    lines += [
        "",
        f"DAY TOTALS: {totals['calories']} kcal eaten, {totals['protein_g']}g protein, "
        f"{totals['carbs_g']}g carbs, {totals['fat_g']}g fat, "
        f"~{totals['calories_burned']} kcal burned through activity.",
    ]
    if weights:
        lines += ["", "RECENT WEIGHT LOGS (newest first):"]
        lines += [f"- {w['date']}: {w['weight_kg']}kg" for w in weights]

    try:
        rating = run_day_rating(day_rating_system_prompt(profile), "\n".join(lines))
    except AIUnavailableError as e:
        raise HTTPException(status_code=503, detail=str(e)) from e

    db.execute(
        """INSERT INTO day_ratings (date, score, summary, progress_note) VALUES (?,?,?,?)
           ON CONFLICT(date) DO UPDATE SET score=excluded.score, summary=excluded.summary,
           progress_note=excluded.progress_note, created_at=datetime('now')""",
        (date, rating["score"], rating["summary"], rating.get("progress_note")),
    )
    return {"date": date, **rating}


@router.get("/days")
def list_days(start: DateBound, end: DateBound, db: sqlite3.Connection = Depends(get_db)):
    profile = get_profile_or_404(db)
    try:
        check_date_range(start, end)
    except ValidationProblem as e:
        raise HTTPException(status_code=422, detail=str(e)) from e
    rows = db.execute(
        """SELECT date FROM (
             SELECT date FROM meals UNION SELECT date FROM activities
             UNION SELECT date FROM weight_logs UNION SELECT date FROM day_ratings)
           WHERE date BETWEEN ? AND ? ORDER BY date DESC""",
        (start, end),
    ).fetchall()
    days = []
    for r in rows:
        date = r["date"]
        totals = _day_totals(db, date)
        rating = db.execute("SELECT score FROM day_ratings WHERE date=?", (date,)).fetchone()
        counts = db.execute(
            "SELECT (SELECT COUNT(*) FROM meals WHERE date=?) m, "
            "(SELECT COUNT(*) FROM activities WHERE date=?) a",
            (date, date),
        ).fetchone()
        days.append(
            {
                "date": date,
                "totals": totals,
                "calorie_target": profile["calorie_target"],
                "score": rating["score"] if rating else None,
                "meal_count": counts["m"],
                "activity_count": counts["a"],
            }
        )
    return days
