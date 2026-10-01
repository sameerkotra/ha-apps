from fastapi import APIRouter, Depends
from .. import db, schemas
from ..auth import get_acting_user

router = APIRouter(prefix="/api/goals", tags=["goals"])


def _read(user_id: str):
    with db.get_conn() as conn:
        row = conn.execute("SELECT * FROM goals WHERE user_id = ?", (user_id,)).fetchone()
    return db.row_to_dict(row)


@router.get("")
async def get_goal(user: dict = Depends(get_acting_user)):
    return _read(user["id"])


@router.put("")
async def update_goal(payload: schemas.GoalUpdate, user: dict = Depends(get_acting_user)):
    fields = payload.model_dump(exclude_unset=True)
    if fields:
        set_clause = ", ".join(f"{k} = ?" for k in fields)
        with db.get_conn() as conn:
            conn.execute(
                f"UPDATE goals SET {set_clause} WHERE user_id = ?",
                (*fields.values(), user["id"]),
            )
    return _read(user["id"])
