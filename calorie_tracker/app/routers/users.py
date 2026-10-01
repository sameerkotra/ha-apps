from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from .. import db
from ..auth import require_admin

router = APIRouter(prefix="/api/users", tags=["users"])


class UserToggle(BaseModel):
    enabled: bool


@router.get("")
async def list_users(user: dict = Depends(require_admin)):
    """Every Home Assistant user who has opened this app at least once —
    used to populate the switcher and the Users tab. Admin-only: this list
    (and the enable/disable toggle below) is how switching is managed, so
    it's gated the same way switching itself is. This never creates a user;
    rows only exist because someone already logged in via HA."""
    with db.get_conn() as conn:
        rows = conn.execute(
            "SELECT id, name, enabled FROM users ORDER BY name COLLATE NOCASE ASC"
        ).fetchall()
    return [
        {"id": r["id"], "name": r["name"], "enabled": bool(r["enabled"]), "is_you": r["id"] == user["id"]}
        for r in rows
    ]


@router.put("/{target_id}")
async def set_enabled(target_id: str, payload: UserToggle, user: dict = Depends(require_admin)):
    """Enable/disable a user in the switcher dropdown. This only affects
    visibility in the dropdown — it does not lock anyone out of their own
    data under their own Home Assistant login, and does not delete anything."""
    with db.get_conn() as conn:
        cur = conn.execute(
            "UPDATE users SET enabled = ? WHERE id = ?", (1 if payload.enabled else 0, target_id)
        )
        if cur.rowcount == 0:
            raise HTTPException(404, "User not found")
        row = conn.execute(
            "SELECT id, name, enabled FROM users WHERE id = ?", (target_id,)
        ).fetchone()
    return {"id": row["id"], "name": row["name"], "enabled": bool(row["enabled"]), "is_you": row["id"] == user["id"]}
