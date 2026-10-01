from calendar import monthrange
from datetime import date as date_type, timedelta

from fastapi import APIRouter, Depends, HTTPException, Query

from .. import config, db, ha_sync, schemas
from ..auth import get_acting_user

router = APIRouter(tags=["logs"])

# Used when a person has no goals row yet (same as the goals table's column defaults).
DEFAULT_GOALS = {"calorie_goal": 2000, "protein_goal": 150, "carb_goal": 200, "fat_goal": 65}


def _totals(logs) -> dict:
    """Calories and macros summed over food-log rows."""
    keys = ("calories", "protein", "carbs", "fat")
    return {k: float(sum(row[k] for row in logs)) for k in keys}


def _goals(goal) -> dict:
    return {k: goal[k] if goal else v for k, v in DEFAULT_GOALS.items()}



async def _sync_today(user: dict, log_date: date_type):
    if str(log_date) == str(config.today()):
        await ha_sync.push_for_user(user["id"], user["slug"], user["name"])


@router.get("/api/logs")
async def list_logs(log_date: date_type = Query(..., alias="date"), user: dict = Depends(get_acting_user)):
    with db.get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM food_logs WHERE user_id = ? AND date = ? ORDER BY created_at ASC",
            (user["id"], str(log_date)),
        ).fetchall()
    return db.rows_to_list(rows)


@router.post("/api/logs")
async def create_log(payload: schemas.FoodLogCreate, user: dict = Depends(get_acting_user)):
    with db.get_conn() as conn:
        cur = conn.execute(
            """INSERT INTO food_logs
               (user_id, date, meal_type, food_name, servings, calories, protein, carbs, fat, saved_food_id)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                user["id"], str(payload.date), payload.meal_type, payload.food_name,
                payload.servings, payload.calories, payload.protein, payload.carbs,
                payload.fat, payload.saved_food_id,
            ),
        )
        new_id = cur.lastrowid
        row = conn.execute("SELECT * FROM food_logs WHERE id = ?", (new_id,)).fetchone()
    await _sync_today(user, payload.date)
    return db.row_to_dict(row)


@router.delete("/api/logs/{entry_id}")
async def delete_log(entry_id: int, user: dict = Depends(get_acting_user)):
    with db.get_conn() as conn:
        row = conn.execute(
            "SELECT date FROM food_logs WHERE id = ? AND user_id = ?",
            (entry_id, user["id"]),
        ).fetchone()
        if not row:
            raise HTTPException(404, "Entry not found")
        conn.execute("DELETE FROM food_logs WHERE id = ?", (entry_id,))
    await _sync_today(user, date_type.fromisoformat(row["date"]))
    return {"ok": True}


@router.get("/api/summary")
async def get_summary(log_date: date_type = Query(..., alias="date"), user: dict = Depends(get_acting_user)):
    with db.get_conn() as conn:
        logs = conn.execute(
            "SELECT calories, protein, carbs, fat FROM food_logs WHERE user_id = ? AND date = ?",
            (user["id"], str(log_date)),
        ).fetchall()
        goal = conn.execute(
            "SELECT * FROM goals WHERE user_id = ?", (user["id"],)
        ).fetchone()
        latest_weight = conn.execute(
            "SELECT weight_kg FROM weight_logs WHERE user_id = ? ORDER BY date DESC LIMIT 1",
            (user["id"],),
        ).fetchone()

    totals = _totals(logs)

    goal_out = _goals(goal)

    return {
        "date": str(log_date),
        "totals": totals,
        "goal": goal_out,
        "remaining": {
            "calories": goal_out["calorie_goal"] - totals["calories"],
            "protein": goal_out["protein_goal"] - totals["protein"],
            "carbs": goal_out["carb_goal"] - totals["carbs"],
            "fat": goal_out["fat_goal"] - totals["fat"],
        },
        "latest_weight_kg": latest_weight["weight_kg"] if latest_weight else None,
    }


@router.get("/api/summary/month")
async def get_month_summary(
    month: str = Query(..., pattern=r"^\d{4}-\d{2}$"),
    user: dict = Depends(get_acting_user),
):
    """Backs the Dashboard's Monthly Breakdown card: one calendar month,
    picked by the user (not a rolling window like /api/history), split out
    per macro plus weight — /api/history only ever tracks calories."""
    year, mon = int(month[:4]), int(month[5:7])
    month_start = date_type(year, mon, 1)
    days_in_month = monthrange(year, mon)[1]
    month_end = month_start.replace(day=days_in_month)
    today = config.today()

    is_future = month_start > today
    effective_end = min(month_end, today)
    days_elapsed = 0 if is_future else (effective_end - month_start).days + 1

    with db.get_conn() as conn:
        goal = conn.execute("SELECT * FROM goals WHERE user_id = ?", (user["id"],)).fetchone()
        logs = conn.execute(
            "SELECT calories, protein, carbs, fat FROM food_logs WHERE user_id = ? AND date >= ? AND date <= ?",
            (user["id"], str(month_start), str(month_end)),
        ).fetchall()
        weight_rows = conn.execute(
            "SELECT weight_kg FROM weight_logs WHERE user_id = ? AND date >= ? AND date <= ? ORDER BY date ASC",
            (user["id"], str(month_start), str(month_end)),
        ).fetchall()

    totals = _totals(logs)
    avg_per_day = {k: (round(v / days_elapsed, 1) if days_elapsed else 0.0) for k, v in totals.items()}

    goal_out = _goals(goal)

    weight_values = [r["weight_kg"] for r in weight_rows]
    start_kg = round(weight_values[0], 1) if weight_values else None
    end_kg = round(weight_values[-1], 1) if weight_values else None
    weight_out = {
        "avg_kg": round(sum(weight_values) / len(weight_values), 1) if weight_values else None,
        "start_kg": start_kg,
        "end_kg": end_kg,
        "change_kg": round(end_kg - start_kg, 1) if (start_kg is not None and end_kg is not None) else None,
        "entries": len(weight_values),
    }

    return {
        "month": month,
        "label": month_start.strftime("%B %Y"),
        "is_current": month_start.year == today.year and month_start.month == today.month,
        "is_future": is_future,
        "days_in_month": days_in_month,
        "days_elapsed": days_elapsed,
        "totals": {k: round(v, 1) for k, v in totals.items()},
        "avg_per_day": avg_per_day,
        "goal": goal_out,
        "weight": weight_out,
    }


@router.get("/api/history")
async def get_history(
    granularity: str = Query("daily", pattern="^(daily|weekly|monthly)$"),
    user: dict = Depends(get_acting_user),
):
    today = config.today()
    with db.get_conn() as conn:
        goal = conn.execute(
            "SELECT calorie_goal FROM goals WHERE user_id = ?", (user["id"],)
        ).fetchone()
    calorie_goal = goal["calorie_goal"] if goal else DEFAULT_GOALS["calorie_goal"]

    if granularity == "daily":
        n = 14
        range_start = today - timedelta(days=n - 1)
    elif granularity == "weekly":
        n = 8
        range_start = today - timedelta(days=n * 7 - 1)
    else:
        n = 6
        range_start = config.add_months(today.replace(day=1), -(n - 1))

    with db.get_conn() as conn:
        rows = conn.execute(
            "SELECT date, calories FROM food_logs WHERE user_id = ? AND date >= ? AND date <= ?",
            (user["id"], str(range_start), str(today)),
        ).fetchall()

    by_day: dict[date_type, float] = {}
    for r in rows:
        d = date_type.fromisoformat(r["date"])
        by_day[d] = by_day.get(d, 0.0) + r["calories"]

    points = []

    if granularity == "daily":
        for i in range(n):
            d = range_start + timedelta(days=i)
            points.append({
                "label": f"{d.strftime('%a')} {d.month}/{d.day}",
                "date": str(d),
                "calories": round(by_day.get(d, 0.0), 1),
                "goal": calorie_goal,
            })

    elif granularity == "weekly":
        for i in range(n):
            wk_start = range_start + timedelta(days=i * 7)
            wk_end = min(wk_start + timedelta(days=6), today)
            days_elapsed = (wk_end - wk_start).days + 1
            total = sum(by_day.get(wk_start + timedelta(days=j), 0.0) for j in range(days_elapsed))
            avg = total / days_elapsed if days_elapsed else 0
            points.append({
                "label": f"{wk_start.month}/{wk_start.day}",
                "date": str(wk_start),
                "calories": round(avg, 1),
                "goal": calorie_goal,
            })

    else:
        for i in range(n):
            month_start = config.add_months(range_start, i).replace(day=1)
            last_day_num = monthrange(month_start.year, month_start.month)[1]
            month_end = min(month_start.replace(day=last_day_num), today)
            days_elapsed = (month_end - month_start).days + 1
            total = sum(v for d, v in by_day.items() if month_start <= d <= month_end)
            avg = total / days_elapsed if days_elapsed > 0 else 0
            points.append({
                "label": month_start.strftime("%b %Y"),
                "date": str(month_start),
                "calories": round(avg, 1),
                "goal": calorie_goal,
            })

    return {"granularity": granularity, "goal": calorie_goal, "points": points}
