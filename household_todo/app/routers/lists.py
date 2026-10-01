"""Lists: every shared list plus the acting user's own personal lists (SPEC §6, §8d)."""
from fastapi import APIRouter, Body, Depends, HTTPException, Response

from .. import config, db, settings, taskview
from ..auth import get_acting_user

router = APIRouter(prefix="/api", tags=["lists"])


def _clean_name(value) -> str:
    if not isinstance(value, str) or not (1 <= len(value.strip()) <= 60):
        raise HTTPException(422, "A list name must be 1–60 characters.")
    return value.strip()


def _list_json(row, open_count: int) -> dict:
    return {
        "id": row["id"], "name": row["name"], "kind": row["kind"], "ownerUserId": row["owner_user_id"],
        "position": row["position"], "openCount": open_count, "createdAt": row["created_at"],
        "role": row["role"],
    }


def _owner_check(lst: dict, acting_id: str) -> None:
    """A shared list can be renamed/deleted by anyone; a personal one only
    by its owner (an admin acting as the owner counts as the owner)."""
    if lst["kind"] == "personal" and lst["owner_user_id"] != acting_id:
        raise HTTPException(403, "Only the owner can change a personal list.")


@router.get("/lists")
def get_lists(acting: dict = Depends(get_acting_user)):
    with db.get_conn() as conn:
        rows = conn.execute(
            """
            SELECT l.*, (SELECT COUNT(*) FROM tasks t WHERE t.list_id = l.id AND t.completed = 0) AS open_count
            FROM lists l
            WHERE l.kind = 'shared' OR l.owner_user_id = ?
            ORDER BY (l.kind = 'personal'), l.position, l.created_at
            """,
            (acting["id"],),
        ).fetchall()
    return [_list_json(r, r["open_count"]) for r in rows]


@router.post("/lists", status_code=201)
def create_list(body: dict = Body(...), acting: dict = Depends(get_acting_user)):
    name = _clean_name(body.get("name"))
    kind = body.get("kind")
    if kind not in ("personal", "shared"):
        raise HTTPException(422, "kind must be 'personal' or 'shared'.")
    owner = acting["id"] if kind == "personal" else None
    with db.get_conn() as conn:
        list_id = db.new_id()
        conn.execute(
            "INSERT INTO lists (id, name, kind, owner_user_id, created_at, position) VALUES (?, ?, ?, ?, ?, ?)",
            (list_id, name, kind, owner, config.now_iso(), taskview.next_list_position(conn, kind, owner)),
        )
        row = conn.execute("SELECT * FROM lists WHERE id = ?", (list_id,)).fetchone()
    return _list_json(row, 0)


# NB: declared before the `/lists/{list_id}` routes so "reorder" is never read as an id.
@router.post("/lists/reorder")
def reorder_lists(body: dict = Body(...), acting: dict = Depends(get_acting_user)):
    kind = body.get("kind")
    ids = body.get("ordered_ids")
    if kind not in ("personal", "shared"):
        raise HTTPException(422, "kind must be 'personal' or 'shared'.")
    if not isinstance(ids, list) or not all(isinstance(i, str) for i in ids) or len(set(ids)) != len(ids):
        raise HTTPException(400, "ordered_ids must be a list of distinct list ids.")
    with db.get_conn() as conn:
        for i in ids:
            row = conn.execute("SELECT kind, owner_user_id FROM lists WHERE id = ?", (i,)).fetchone()
            if not row or row["kind"] != kind or (kind == "personal" and row["owner_user_id"] != acting["id"]):
                raise HTTPException(400, "ordered_ids contains a list that isn't in that group.")
        for pos, i in enumerate(ids):
            conn.execute("UPDATE lists SET position = ? WHERE id = ?", (pos, i))
    return {"status": "ok"}


@router.patch("/lists/{list_id}")
def rename_list(list_id: str, body: dict = Body(...), acting: dict = Depends(get_acting_user)):
    name = _clean_name(body.get("name"))
    with db.get_conn() as conn:
        lst = taskview.load_list(conn, list_id)
        _owner_check(lst, acting["id"])
        conn.execute("UPDATE lists SET name = ? WHERE id = ?", (name, list_id))
        row = conn.execute("SELECT * FROM lists WHERE id = ?", (list_id,)).fetchone()
        open_count = conn.execute(
            "SELECT COUNT(*) FROM tasks WHERE list_id = ? AND completed = 0", (list_id,)
        ).fetchone()[0]
    return _list_json(row, open_count)


@router.delete("/lists/{list_id}", status_code=204)
def delete_list(list_id: str, acting: dict = Depends(get_acting_user)):
    with db.get_conn() as conn:
        lst = taskview.load_list(conn, list_id)
        _owner_check(lst, acting["id"])
        if lst.get("role") == "maintenance" and settings.get("maintenance_enabled"):
            raise HTTPException(409, "The Maintenance list holds the one-off maintenance jobs, so it can't be deleted "
                                     "while Maintenance is on. You can rename it.")
        conn.execute("DELETE FROM lists WHERE id = ?", (list_id,))   # cascades its tasks
    return Response(status_code=204)
