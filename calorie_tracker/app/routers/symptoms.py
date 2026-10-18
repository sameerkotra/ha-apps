"""Symptoms, food tags and Patterns (the Symptoms page). Like the food log, everything is the acting person's own."""
import csv
import io
from datetime import date as date_type, timedelta

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import Response

from .. import config, db, foodrows, patterns, schemas, settings
from ..auth import get_acting_user

router = APIRouter(tags=["symptoms"])

MAX_PER_DAY = 100           # symptom entries one person can log on one day
QUICK = 12                  # one-tap symptoms offered
RANGES = (30, 90, 180, 365)


@router.get("/api/symptoms")
def list_symptoms(log_date: date_type = Query(..., alias="date"), user: dict = Depends(get_acting_user)):
    with db.get_conn() as conn:
        rows = conn.execute("SELECT * FROM symptom_logs WHERE user_id = ? AND date = ? ORDER BY time, id",
                            (user["id"], str(log_date))).fetchall()
    return db.rows_to_list(rows)


@router.post("/api/symptoms")
def create_symptom(payload: schemas.SymptomCreate, user: dict = Depends(get_acting_user)):
    if payload.date > config.today():
        raise HTTPException(422, "date: a symptom can't be logged for a day still to come")
    name = " ".join(payload.symptom.split())
    with db.get_conn() as conn:
        n = conn.execute("SELECT COUNT(*) FROM symptom_logs WHERE user_id = ? AND date = ?",
                         (user["id"], str(payload.date))).fetchone()[0]
        if n >= MAX_PER_DAY:
            raise HTTPException(422, f"At most {MAX_PER_DAY} symptoms a day.")
        cur = conn.execute("INSERT INTO symptom_logs (user_id, date, time, symptom, severity, note) "
                           "VALUES (?, ?, ?, ?, ?, ?)",
                           (user["id"], str(payload.date), payload.time, name, payload.severity, payload.note or None))
        row = conn.execute("SELECT * FROM symptom_logs WHERE id = ?", (cur.lastrowid,)).fetchone()
    return db.row_to_dict(row)


@router.delete("/api/symptoms/{entry_id}")
def delete_symptom(entry_id: int, user: dict = Depends(get_acting_user)):
    with db.get_conn() as conn:
        cur = conn.execute("DELETE FROM symptom_logs WHERE id = ? AND user_id = ?", (entry_id, user["id"]))
        if cur.rowcount == 0:
            raise HTTPException(404, "Entry not found")
    return {"ok": True}


@router.get("/api/symptoms/choices")
def choices(user: dict = Depends(get_acting_user)):
    """What the page offers: one-tap symptoms (the person's most used in 90 days first, then App settings' list)
    and food tags (App settings' list, then the person's own)."""
    since = (config.today() - timedelta(days=90)).isoformat()
    with db.get_conn() as conn:
        used = [r["symptom"] for r in conn.execute(
            "SELECT symptom, COUNT(*) AS n, MAX(date) AS last FROM symptom_logs WHERE user_id = ? AND date >= ? "
            "GROUP BY lower(symptom) ORDER BY n DESC, last DESC LIMIT ?", (user["id"], since, QUICK))]
        own_tags = set()
        for (t,) in conn.execute("SELECT tags FROM food_logs WHERE user_id = ? AND tags IS NOT NULL "
                                 "UNION SELECT tags FROM saved_foods WHERE user_id = ? AND tags IS NOT NULL",
                                 (user["id"], user["id"])):
            own_tags.update(foodrows.tags_list(t))
    quick, seen = [], set()
    for s in used + settings.word_list(settings.get("symptom_list")):
        if s.lower() not in seen:
            seen.add(s.lower())
            quick.append(s)
    starter = [t.lower() for t in settings.word_list(settings.get("food_tag_list"))]
    tags = starter + sorted(own_tags - set(starter))
    return {"symptoms": quick[:max(QUICK, len(used))], "tags": tags}


def _range(days: int) -> tuple[date_type, date_type]:
    if days not in RANGES:
        raise HTTPException(422, "days: one of " + ", ".join(map(str, RANGES)))
    end = config.today()
    return end - timedelta(days=days - 1), end


@router.get("/api/patterns")
def get_patterns(days: int = Query(90), user: dict = Depends(get_acting_user)):
    start, end = _range(days)
    hours = int(settings.get("pattern_hours"))
    # food from the day before counts for a symptom early in the first day
    food_from = (start - timedelta(days=1)).isoformat()
    with db.get_conn() as conn:
        foods = db.rows_to_list(conn.execute(
            "SELECT date, eaten_at, meal_type, food_name, tags FROM food_logs WHERE user_id = ? AND date >= ? "
            "AND date <= ?", (user["id"], food_from, end.isoformat())))
        syms = db.rows_to_list(conn.execute(
            "SELECT date, time, symptom, severity FROM symptom_logs WHERE user_id = ? AND date >= ? AND date <= ?",
            (user["id"], start.isoformat(), end.isoformat())))
    out = patterns.analyse(foods, syms, start=start, end=end, hours=hours, min_count=int(settings.get("pattern_min")))
    out["days"] = days
    return out


def _cell(v) -> str:
    """A spreadsheet must show text as text: one starting like a formula gets a leading '."""
    s = "" if v is None else str(v)
    return "'" + s if s[:1] in ("=", "+", "-", "@", "\t", "\r") else s


@router.get("/api/journal.csv")
def journal_csv(days: int = Query(90), user: dict = Depends(get_acting_user)):
    """Food and symptoms in time order, for a doctor or a spreadsheet."""
    start, end = _range(days)
    with db.get_conn() as conn:
        foods = conn.execute("SELECT * FROM food_logs WHERE user_id = ? AND date >= ? AND date <= ?",
                             (user["id"], start.isoformat(), end.isoformat())).fetchall()
        syms = conn.execute("SELECT * FROM symptom_logs WHERE user_id = ? AND date >= ? AND date <= ?",
                            (user["id"], start.isoformat(), end.isoformat())).fetchall()
    rows = []
    for f in foods:
        f = foodrows.out(f)
        rows.append((f["date"], f["eaten_at"] or foodrows.MEAL_TIMES.get(f["meal_type"], ""), "Food", f["food_name"],
                     f"{f['meal_type']}, {f['servings']:g} serving{'s' if f['servings'] != 1 else ''}", "",
                     ", ".join(f["tags"]), "" if f["calories"] is None else round(f["calories"]), ""))
    for s in syms:
        rows.append((s["date"], s["time"], "Symptom", s["symptom"], "", s["severity"], "", "", s["note"] or ""))
    rows.sort(key=lambda r: (r[0], r[1], r[2] != "Food"))
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["Date", "Time", "Type", "What", "Details", "Severity (1-5)", "Tags", "Calories", "Note"])
    for r in rows:
        w.writerow([_cell(x) for x in r])
    name = f"food-and-symptoms-{start.isoformat()}-to-{end.isoformat()}.csv"
    return Response("﻿" + buf.getvalue(), media_type="text/csv; charset=utf-8",
                    headers={"Content-Disposition": f'attachment; filename="{name}"'})
