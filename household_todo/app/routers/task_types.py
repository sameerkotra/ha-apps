"""Task types — a household-wide vocabulary, one per task (SPEC §8g)."""
import re

from fastapi import APIRouter, Body, Depends, HTTPException, Response

from .. import config, db, taskview
from ..auth import get_acting_user

router = APIRouter(prefix="/api", tags=["task-types"])

COLOR_RE = re.compile(r"^#[0-9A-Fa-f]{6}$")


def _name(value) -> str:
    if not isinstance(value, str) or not (1 <= len(value.strip()) <= 30):
        raise HTTPException(422, "A type name must be 1–30 characters.")
    return value.strip()


def _icon(value):
    if value is None or value == "":
        return None
    # "an optional single emoji": a few code points at most (emoji can be sequences)
    if not isinstance(value, str) or len(value.strip()) > 8:
        raise HTTPException(422, "icon must be a single emoji.")
    return value.strip()


def _color(value):
    if value is None or value == "":
        return None
    if not isinstance(value, str) or not COLOR_RE.match(value):
        raise HTTPException(422, "color must look like #RRGGBB.")
    return value


def _json(row, usage: int) -> dict:
    return {"id": row["id"], "name": row["name"], "icon": row["icon"], "color": row["color"],
            "position": row["position"], "usageCount": usage}


def _usage(conn, type_id: str, acting_id: str) -> int:
    return conn.execute(
        f"SELECT COUNT(*) FROM tasks t JOIN lists l ON l.id = t.list_id WHERE t.type_id = ? AND {taskview.VISIBLE_SQL}",
        (type_id, acting_id),
    ).fetchone()[0]


@router.get("/task-types")
def get_types(acting: dict = Depends(get_acting_user)):
    with db.get_conn() as conn:
        rows = conn.execute("SELECT * FROM task_types ORDER BY position, name COLLATE NOCASE").fetchall()
        return [_json(r, _usage(conn, r["id"], acting["id"])) for r in rows]


@router.post("/task-types", status_code=201)
def create_type(body: dict = Body(...), acting: dict = Depends(get_acting_user)):
    name, icon, color = _name(body.get("name")), _icon(body.get("icon")), _color(body.get("color"))
    with db.get_conn() as conn:
        if conn.execute("SELECT 1 FROM task_types WHERE name = ? COLLATE NOCASE", (name,)).fetchone():
            raise HTTPException(409, "A type with that name already exists.")
        pos = conn.execute("SELECT COALESCE(MAX(position), -1) + 1 FROM task_types").fetchone()[0]
        type_id = db.new_id()
        conn.execute(
            "INSERT INTO task_types (id, name, icon, color, position, created_at) VALUES (?, ?, ?, ?, ?, ?)",
            (type_id, name, icon, color, pos, config.now_iso()),
        )
        return _json(conn.execute("SELECT * FROM task_types WHERE id = ?", (type_id,)).fetchone(), 0)


@router.patch("/task-types/{type_id}")
def update_type(type_id: str, body: dict = Body(...), acting: dict = Depends(get_acting_user)):
    with db.get_conn() as conn:
        row = conn.execute("SELECT * FROM task_types WHERE id = ?", (type_id,)).fetchone()
        if not row:
            raise HTTPException(404, "Type not found.")
        updates = {}
        if "name" in body:
            updates["name"] = _name(body["name"])
            if conn.execute("SELECT 1 FROM task_types WHERE name = ? COLLATE NOCASE AND id != ?", (updates["name"], type_id)).fetchone():
                raise HTTPException(409, "A type with that name already exists.")
        if "icon" in body:
            updates["icon"] = _icon(body["icon"])
        if "color" in body:
            updates["color"] = _color(body["color"])
        if updates:
            conn.execute(f"UPDATE task_types SET {', '.join(k + ' = ?' for k in updates)} WHERE id = ?", [*updates.values(), type_id])
        return _json(conn.execute("SELECT * FROM task_types WHERE id = ?", (type_id,)).fetchone(), _usage(conn, type_id, acting["id"]))


@router.delete("/task-types/{type_id}", status_code=204)
def delete_type(type_id: str, acting: dict = Depends(get_acting_user)):
    with db.get_conn() as conn:
        if not conn.execute("SELECT 1 FROM task_types WHERE id = ?", (type_id,)).fetchone():
            raise HTTPException(404, "Type not found.")
        conn.execute("DELETE FROM task_types WHERE id = ?", (type_id,))   # tasks become untyped (SET NULL)
    return Response(status_code=204)
