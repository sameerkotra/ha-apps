"""Quick tasks: the Calendar tab's second view, a person's own simple list — add an item, remove it (SPEC §5, §6, §9).
Whose: the acting person's (an admin acting as someone sees theirs, as everywhere else). Never anyone else's."""
import uuid

from fastapi import APIRouter, Body, Depends, HTTPException

from .. import config, db
from ..auth import get_acting_user

router = APIRouter(prefix="/api", tags=["quick-tasks"])

MAX_TEXT = 200
MAX_ITEMS = 200


def _items(conn, uid: str) -> list[dict]:
    return [{"id": r["id"], "text": r["text"], "createdAt": r["created_at"]} for r in conn.execute(
        "SELECT id, text, created_at FROM quick_tasks WHERE user_id = ? ORDER BY created_at, rowid", (uid,))]


@router.get("/quick-tasks")
def list_quick_tasks(acting: dict = Depends(get_acting_user)):
    with db.get_conn() as conn:
        return {"items": _items(conn, acting["id"])}


@router.post("/quick-tasks")
def add_quick_task(body: dict = Body(...), acting: dict = Depends(get_acting_user)):
    text = " ".join(str(body.get("text") or "").split()) if isinstance(body, dict) else ""
    if not text:
        raise HTTPException(422, "Type something to add.")
    if len(text) > MAX_TEXT:
        raise HTTPException(422, f"A quick task is at most {MAX_TEXT} characters.")
    with db.get_conn() as conn:
        n = conn.execute("SELECT COUNT(*) FROM quick_tasks WHERE user_id = ?", (acting["id"],)).fetchone()[0]
        if n >= MAX_ITEMS:
            raise HTTPException(409, f"Quick tasks hold at most {MAX_ITEMS} items — remove some first.")
        conn.execute("INSERT INTO quick_tasks (id, user_id, text, created_at) VALUES (?, ?, ?, ?)",
                     (uuid.uuid4().hex, acting["id"], text, config.now_iso()))
        return {"items": _items(conn, acting["id"])}


@router.delete("/quick-tasks/{item_id}")
def remove_quick_task(item_id: str, acting: dict = Depends(get_acting_user)):
    with db.get_conn() as conn:
        n = conn.execute("DELETE FROM quick_tasks WHERE id = ? AND user_id = ?", (item_id, acting["id"])).rowcount
        if not n:
            raise HTTPException(404, "That quick task isn't there any more.")
        return {"items": _items(conn, acting["id"])}
