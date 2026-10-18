from fastapi import APIRouter, Depends, HTTPException
from .. import db, foodrows, schemas
from ..auth import get_acting_user

router = APIRouter(prefix="/api/saved-foods", tags=["saved-foods"])


@router.get("")
def list_saved_foods(user: dict = Depends(get_acting_user)):
    with db.get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM saved_foods WHERE user_id = ? ORDER BY name ASC",
            (user["id"],),
        ).fetchall()
    return [foodrows.out(r) for r in rows]


@router.post("")
def create_saved_food(payload: schemas.SavedFoodCreate, user: dict = Depends(get_acting_user)):
    with db.get_conn() as conn:
        cur = conn.execute(
            """INSERT INTO saved_foods
               (user_id, name, serving_size, serving_unit, calories, no_calories, protein, carbs, fat, notes, tags)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                user["id"], payload.name, payload.serving_size, payload.serving_unit,
                payload.calories or 0, 1 if payload.calories is None else 0, payload.protein, payload.carbs,
                payload.fat, (payload.notes or None), foodrows.tags_text(payload.tags),
            ),
        )
        new_id = cur.lastrowid
        row = conn.execute("SELECT * FROM saved_foods WHERE id = ?", (new_id,)).fetchone()
    return foodrows.out(row)


@router.put("/{food_id}")
def update_saved_food(food_id: int, payload: schemas.SavedFoodCreate, user: dict = Depends(get_acting_user)):
    with db.get_conn() as conn:
        cur = conn.execute(
            """UPDATE saved_foods SET name=?, serving_size=?, serving_unit=?,
               calories=?, no_calories=?, protein=?, carbs=?, fat=?, notes=?, tags=? WHERE id=? AND user_id=?""",
            (
                payload.name, payload.serving_size, payload.serving_unit,
                payload.calories or 0, 1 if payload.calories is None else 0, payload.protein, payload.carbs,
                payload.fat, (payload.notes or None), foodrows.tags_text(payload.tags), food_id, user["id"],
            ),
        )
        if cur.rowcount == 0:
            raise HTTPException(404, "Saved food not found")
        row = conn.execute("SELECT * FROM saved_foods WHERE id = ?", (food_id,)).fetchone()
    return foodrows.out(row)


@router.delete("/{food_id}")
def delete_saved_food(food_id: int, user: dict = Depends(get_acting_user)):
    with db.get_conn() as conn:
        cur = conn.execute(
            "DELETE FROM saved_foods WHERE id = ? AND user_id = ?",
            (food_id, user["id"]),
        )
        if cur.rowcount == 0:
            raise HTTPException(404, "Saved food not found")
    return {"ok": True}
