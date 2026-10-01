from fastapi import APIRouter, Depends, HTTPException
from .. import db, schemas
from ..auth import get_acting_user

router = APIRouter(prefix="/api/saved-foods", tags=["saved-foods"])


@router.get("")
async def list_saved_foods(user: dict = Depends(get_acting_user)):
    with db.get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM saved_foods WHERE user_id = ? ORDER BY name ASC",
            (user["id"],),
        ).fetchall()
    return db.rows_to_list(rows)


@router.post("")
async def create_saved_food(payload: schemas.SavedFoodCreate, user: dict = Depends(get_acting_user)):
    with db.get_conn() as conn:
        cur = conn.execute(
            """INSERT INTO saved_foods
               (user_id, name, serving_size, serving_unit, calories, protein, carbs, fat, notes)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                user["id"], payload.name, payload.serving_size, payload.serving_unit,
                payload.calories, payload.protein, payload.carbs, payload.fat,
                (payload.notes or None),
            ),
        )
        new_id = cur.lastrowid
        row = conn.execute("SELECT * FROM saved_foods WHERE id = ?", (new_id,)).fetchone()
    return db.row_to_dict(row)


@router.put("/{food_id}")
async def update_saved_food(food_id: int, payload: schemas.SavedFoodCreate, user: dict = Depends(get_acting_user)):
    with db.get_conn() as conn:
        cur = conn.execute(
            """UPDATE saved_foods SET name=?, serving_size=?, serving_unit=?,
               calories=?, protein=?, carbs=?, fat=?, notes=? WHERE id=? AND user_id=?""",
            (
                payload.name, payload.serving_size, payload.serving_unit,
                payload.calories, payload.protein, payload.carbs, payload.fat,
                (payload.notes or None), food_id, user["id"],
            ),
        )
        if cur.rowcount == 0:
            raise HTTPException(404, "Saved food not found")
        row = conn.execute("SELECT * FROM saved_foods WHERE id = ?", (food_id,)).fetchone()
    return db.row_to_dict(row)


@router.delete("/{food_id}")
async def delete_saved_food(food_id: int, user: dict = Depends(get_acting_user)):
    with db.get_conn() as conn:
        cur = conn.execute(
            "DELETE FROM saved_foods WHERE id = ? AND user_id = ?",
            (food_id, user["id"]),
        )
        if cur.rowcount == 0:
            raise HTTPException(404, "Saved food not found")
    return {"ok": True}
