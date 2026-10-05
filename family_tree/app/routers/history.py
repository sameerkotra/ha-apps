"""History, undo and trash (§7)."""
import json

from fastapi import APIRouter, Depends, HTTPException, Query

from .. import names as names_mod, db, graph as graph_mod, housekeeping
from ..auth import require_admin, require_user
from ..models import people_in_loops, reject_new_loops
from ..history import Batch, Conflict, NotFound, undo
from .families import family_people

router = APIRouter(prefix="/api", tags=["history"])

ENTITY_WORDS = {"people": "person", "families": "family", "events": "event", "family_children": "child link",
                "media": "photo", "media_links": "photo link", "stories": "story", "kin_terms": "relationship name",
                "media_regions": "tag on a photo"}
HIDDEN_FIELDS = {"x", "y", "w", "h", "photo_region_id", "updated_at", "updated_by", "source", "created_at", "created_by", "gedcom_extra", "sort_key", "date_y", "date_m", "date_d",
                 "date_approx"}



def _batch_out(conn, r, names) -> dict:
    people = [p["person_id"] for p in conn.execute("SELECT person_id FROM batch_people WHERE batch_id = ?", (r["id"],))]
    return {
        "id": r["id"], "label": r["label"], "at": r["created_at"], "userId": r["user_id"],
        "userName": names.get(r["user_id"]) or ("Home Assistant user" if r["user_id"] else "System"),
        "undoOf": r["undo_of"], "undoneBy": r["undone_by"], "changes": r["n"],
        "people": [{"id": p} for p in people[:12]],
    }


@router.get("/history")
def history(personId: str | None = None, userId: str | None = None, page: int = Query(1, ge=1, le=1_000_000),
            page_size: int = Query(50, ge=1, le=200), user: dict = Depends(require_user)):
    with db.get_conn() as conn:
        where, args = [], []
        if personId:
            where.append("b.id IN (SELECT batch_id FROM batch_people WHERE person_id = ?)")
            args.append(personId)
        if userId:
            where.append("b.user_id = ?")
            args.append(userId)
        clause = ("WHERE " + " AND ".join(where)) if where else ""
        total = conn.execute(f"SELECT COUNT(*) FROM batches b {clause}", args).fetchone()[0]
        rows = conn.execute(
            f"SELECT b.*, (SELECT COUNT(*) FROM changes c WHERE c.batch_id = b.id) AS n FROM batches b {clause} "
            "ORDER BY b.created_at DESC, b.rowid DESC LIMIT ? OFFSET ?", args + [page_size, (page - 1) * page_size]).fetchall()
        user_names = {u["id"]: u["name"] for u in conn.execute("SELECT id, name FROM users")}
        items = [_batch_out(conn, r, user_names) for r in rows]
        pids = {p["id"] for it in items for p in it["people"]}
        pn = names_mod.people_names(conn, pids)
        for it in items:
            it["people"] = [{"id": p["id"], "name": pn.get(p["id"], "a deleted person")} for p in it["people"]]
        users = [{"id": u["id"], "name": u["name"]} for u in conn.execute(
            "SELECT DISTINCT u.id, u.name FROM users u JOIN batches b ON b.user_id = u.id ORDER BY u.name")]
        return {"items": items, "total": total, "page": page, "pageSize": page_size, "users": users}


@router.get("/history/{batch_id}")
def batch_detail(batch_id: str, user: dict = Depends(require_user)):
    """Field-level before → after for each change in a batch."""
    with db.get_conn() as conn:
        b = conn.execute("SELECT * FROM batches WHERE id = ?", (batch_id,)).fetchone()
        if not b:
            raise HTTPException(404, "That change isn't in the history.")
        out = []
        rows = conn.execute("SELECT * FROM changes WHERE batch_id = ? ORDER BY seq", (batch_id,)).fetchall()
        person_cols = ("partner1_id", "partner2_id", "child_id")
        ids = set()
        for c in rows:
            for snap in (c["before"], c["after"]):
                if snap:
                    d = json.loads(snap)
                    ids.update(d.get(k) for k in person_cols if d.get(k))
        names = names_mod.people_names(conn, ids)
        for c in rows:
            before = json.loads(c["before"]) if c["before"] else {}
            after = json.loads(c["after"]) if c["after"] else {}
            fields = []
            for k in sorted(set(before) | set(after)):
                if k in HIDDEN_FIELDS or k == "id" or (k.endswith("_id") and k not in person_cols + ("photo_media_id",)):
                    continue
                if before.get(k) != after.get(k):
                    old, new = before.get(k), after.get(k)
                    if k in person_cols:
                        old = names.get(old, old and "a deleted person")
                        new = names.get(new, new and "a deleted person")
                    elif k == "photo_media_id":
                        k, old, new = "photo", "a photo" if old else None, "a new photo" if new else None
                    fields.append({"field": k, "before": old, "after": new})
            out.append({"entity": ENTITY_WORDS.get(c["entity"], c["entity"]), "op": c["op"], "fields": fields})
        return {"id": batch_id, "label": b["label"], "changes": out}


@router.post("/history/{batch_id}/undo")
def undo_batch(batch_id: str, user: dict = Depends(require_user)):
    with db.get_conn() as conn:
        pids = [r["person_id"] for r in conn.execute("SELECT person_id FROM batch_people WHERE batch_id = ?", (batch_id,))]
        before = people_in_loops(graph_mod.get(conn), pids)      # before undo's Batch bumps tree_version
        try:
            nb = undo(conn, batch_id, user["id"])
        except NotFound as e:
            raise HTTPException(404, str(e))
        except Conflict as e:
            raise HTTPException(409, str(e))
        reject_new_loops(conn, pids, before, status=409)
        return {"ok": True, "batchId": nb.id}


# ---------- trash (Admin → Trash; admins only) ----------
# Deleting stays open to everyone (it moves things here), and so does undoing a
# delete from History or the toast; browsing, restoring and emptying the trash
# are admin-only.
@router.get("/trash")
def trash(user: dict = Depends(require_admin)):
    with db.get_conn() as conn:
        people = conn.execute("SELECT id, given_names, surname, nickname, name_order, deleted_at FROM people "
                              "WHERE deleted_at IS NOT NULL ORDER BY deleted_at DESC").fetchall()
        fams = conn.execute("SELECT id, partner1_id, partner2_id, deleted_at FROM families "
                            "WHERE deleted_at IS NOT NULL ORDER BY deleted_at DESC").fetchall()
        media = conn.execute("SELECT id, title, kind, deleted_at FROM media WHERE deleted_at IS NOT NULL "
                             "ORDER BY deleted_at DESC").fetchall()
        ids = {x for f in fams for x in (f["partner1_id"], f["partner2_id"]) if x}
        names = names_mod.people_names(conn, ids)
        items = [{"entity": "people", "id": r["id"], "deletedAt": r["deleted_at"],
                  "name": names_mod.row_name(r)}
                 for r in people]
        items += [{"entity": "families", "id": r["id"], "deletedAt": r["deleted_at"],
                   "name": "Family of " + (" & ".join(names.get(x, "?") for x in (r["partner1_id"], r["partner2_id"]) if x)
                                           or "unknown partners")} for r in fams]
        items += [{"entity": "media", "id": r["id"], "deletedAt": r["deleted_at"], "kind": r["kind"],
                   "name": r["title"] or ("Document" if r["kind"] == "document" else "Photo")}
                  for r in media]
        items.sort(key=lambda x: x["deletedAt"], reverse=True)
        return {"items": items, "trashDays": housekeeping.trash_days()}


@router.post("/trash/{entity}/{eid}/restore")
def restore(entity: str, eid: str, user: dict = Depends(require_admin)):
    if entity not in ("people", "families", "media"):
        raise HTTPException(404, "Unknown kind of item.")
    with db.get_conn() as conn:
        row = conn.execute(f"SELECT deleted_at FROM {entity} WHERE id = ?", (eid,)).fetchone()
        if not row:
            raise HTTPException(404, "That item is no longer in the trash.")
        if not row["deleted_at"]:
            raise HTTPException(409, "That item isn't in the trash.")
        pids = [eid] if entity == "people" else family_people(conn, eid) if entity == "families" else []
        before = people_in_loops(graph_mod.get(conn), pids)      # before the Batch bumps tree_version
        b = Batch(conn, user["id"], f"Restored a {ENTITY_WORDS[entity]} from the trash")
        b.restore(entity, eid)
        reject_new_loops(conn, pids, before)
        if entity == "people":
            b.touch(eid)
        return {"ok": True, "batchId": b.id}


@router.delete("/trash")
def empty_trash(admin: dict = Depends(require_admin)):
    return {"purged": housekeeping.purge(older_than_days=None)}
