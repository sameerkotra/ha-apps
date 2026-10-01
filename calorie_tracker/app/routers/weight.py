from datetime import date as date_type, timedelta

from fastapi import APIRouter, Depends, HTTPException, Query
from .. import config, db, schemas
from ..auth import get_acting_user

router = APIRouter(prefix="/api/weight", tags=["weight"])



@router.get("")
async def list_weight(user: dict = Depends(get_acting_user)):
    with db.get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM weight_logs WHERE user_id = ? ORDER BY date ASC",
            (user["id"],),
        ).fetchall()
    return db.rows_to_list(rows)


@router.get("/history")
async def get_weight_history(
    granularity: str = Query("daily", pattern="^(daily|weekly|monthly)$"),
    user: dict = Depends(get_acting_user),
):
    """Weight trend for the chart, bucketed by day/week/month. Unlike the
    calorie history, empty periods are simply skipped rather than
    zero-filled — a day with no weigh-in isn't "0 kg", it's just missing."""
    today = config.today()

    with db.get_conn() as conn:
        goal_row = conn.execute(
            "SELECT target_weight_kg FROM goals WHERE user_id = ?", (user["id"],)
        ).fetchone()
    target = goal_row["target_weight_kg"] if goal_row else None

    if granularity == "daily":
        range_start = today - timedelta(days=29)  # last 30 days
    elif granularity == "weekly":
        range_start = today - timedelta(days=12 * 7 - 1)  # last 12 weeks
    else:
        range_start = config.add_months(today.replace(day=1), -11)  # last 12 months

    with db.get_conn() as conn:
        rows = conn.execute(
            "SELECT date, weight_kg FROM weight_logs WHERE user_id = ? AND date >= ? ORDER BY date ASC",
            (user["id"], str(range_start)),
        ).fetchall()

    points = []

    if granularity == "daily":
        for r in rows:
            d = date_type.fromisoformat(r["date"])
            points.append({"label": f"{d.month}/{d.day}", "date": r["date"], "weight": round(r["weight_kg"], 1)})

    elif granularity == "weekly":
        buckets: dict[date_type, list] = {}
        for r in rows:
            d = date_type.fromisoformat(r["date"])
            week_index = (d - range_start).days // 7
            week_start = range_start + timedelta(days=week_index * 7)
            buckets.setdefault(week_start, []).append(r["weight_kg"])
        for week_start in sorted(buckets):
            values = buckets[week_start]
            points.append({
                "label": f"{week_start.month}/{week_start.day}",
                "date": str(week_start),
                "weight": round(sum(values) / len(values), 1),
            })

    else:  # monthly
        buckets: dict[date_type, list] = {}
        for r in rows:
            d = date_type.fromisoformat(r["date"])
            month_start = d.replace(day=1)
            buckets.setdefault(month_start, []).append(r["weight_kg"])
        for month_start in sorted(buckets):
            values = buckets[month_start]
            points.append({
                "label": month_start.strftime("%b %Y"),
                "date": str(month_start),
                "weight": round(sum(values) / len(values), 1),
            })

    return {"granularity": granularity, "target": target, "points": points}


@router.post("")
async def add_weight(payload: schemas.WeightCreate, user: dict = Depends(get_acting_user)):
    with db.get_conn() as conn:
        cur = conn.execute(
            "INSERT INTO weight_logs (user_id, date, weight_kg, note) VALUES (?, ?, ?, ?)",
            (user["id"], str(payload.date), payload.weight_kg, payload.note),
        )
        new_id = cur.lastrowid
        row = conn.execute(
            "SELECT * FROM weight_logs WHERE id = ?", (new_id,)
        ).fetchone()
    return db.row_to_dict(row)


@router.delete("/{entry_id}")
async def delete_weight(entry_id: int, user: dict = Depends(get_acting_user)):
    with db.get_conn() as conn:
        cur = conn.execute(
            "DELETE FROM weight_logs WHERE id = ? AND user_id = ?",
            (entry_id, user["id"]),
        )
        if cur.rowcount == 0:
            raise HTTPException(404, "Entry not found")
    return {"ok": True}
