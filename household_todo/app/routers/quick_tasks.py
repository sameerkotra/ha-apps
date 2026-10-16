"""Quick tasks: a view of the Calendar tab, a person's own simple list — add an item, remove it, reorder (SPEC §5, §6,
§9). Moving one to a list is the page's own two steps: the task is made with the lists' API, then this one removed.
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
        "SELECT id, text, created_at FROM quick_tasks WHERE user_id = ? ORDER BY position, created_at, rowid", (uid,))]


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
        last = conn.execute("SELECT COALESCE(MAX(position), 0) FROM quick_tasks WHERE user_id = ?",
                            (acting["id"],)).fetchone()[0]
        conn.execute("INSERT INTO quick_tasks (id, user_id, text, position, created_at) VALUES (?, ?, ?, ?, ?)",
                     (uuid.uuid4().hex, acting["id"], text, last + 1, config.now_iso()))
        return {"items": _items(conn, acting["id"])}


@router.post("/quick-tasks/reorder")
def reorder_quick_tasks(body: dict = Body(...), acting: dict = Depends(get_acting_user)):
    """`{ordered_ids}`: every one of the person's quick tasks, in the new order (409 when the list changed meanwhile)."""
    ids = body.get("ordered_ids") if isinstance(body, dict) else None
    if not isinstance(ids, list) or not all(isinstance(i, str) for i in ids) or len(ids) > MAX_ITEMS:
        raise HTTPException(422, "ordered_ids must be a list of ids.")
    with db.get_conn() as conn:
        mine = {r["id"] for r in conn.execute("SELECT id FROM quick_tasks WHERE user_id = ?", (acting["id"],))}
        if set(ids) != mine or len(ids) != len(mine):
            raise HTTPException(409, "The list changed meanwhile — reload and try again.")
        conn.executemany("UPDATE quick_tasks SET position = ? WHERE id = ? AND user_id = ?",
                         [(i + 1, qid, acting["id"]) for i, qid in enumerate(ids)])
        return {"items": _items(conn, acting["id"])}


@router.delete("/quick-tasks/{item_id}")
def remove_quick_task(item_id: str, acting: dict = Depends(get_acting_user)):
    with db.get_conn() as conn:
        n = conn.execute("DELETE FROM quick_tasks WHERE id = ? AND user_id = ?", (item_id, acting["id"])).rowcount
        if not n:
            raise HTTPException(404, "That quick task isn't there any more.")
        return {"items": _items(conn, acting["id"])}
