"""Admin → Shared folders (SPEC §9.1): add an existing /share folder with access per person (No access / Read only /
Read and write, plus Everyone), change its name or access, Rescan now, Stop sharing (nothing on disk changes). Admins
see the folder's file count and size — never its contents (unless they gave themselves access)."""
from fastapi import APIRouter, BackgroundTasks, Body, Depends, HTTPException

from .. import config, db, share_folders
from ..auth import require_admin
from ..store import index

router = APIRouter(prefix="/api/admin/shared-folders", tags=["admin"])


def _payload(conn) -> dict:
    people = [{"id": r["id"], "name": r["name"], "disabled": bool(r["disabled"])}
              for r in conn.execute("SELECT id, name, disabled FROM users ORDER BY name COLLATE NOCASE")]
    return {"folders": share_folders.admin_listing(conn), "people": people}


@router.get("")
def listing(admin: dict = Depends(require_admin)):
    with db.get_conn() as conn:
        return _payload(conn)


@router.post("/inspect")
def inspect(body: dict = Body(...), admin: dict = Depends(require_admin)):
    """Can this folder be shared? Changes nothing (the page shows the verdict while you browse or type)."""
    with db.get_conn() as conn:
        out = share_folders.check(conn, (body or {}).get("path"), (body or {}).get("exclude"))
    out.pop("rel", None)
    return out


@router.post("", status_code=201)
def add(background: BackgroundTasks, body: dict = Body(...), admin: dict = Depends(require_admin)):
    """{path: "/share/…", label, access: {personId | "*": none | ro | rw}, kids?: bool}. The folder is indexed
    straight after. `kids`: a Kids folder (§17.20) — children with access see it."""
    if not isinstance(body, dict) or set(body) - {"path", "label", "access", "kids"}:
        raise HTTPException(422, "Send path, label and access.")
    if "kids" in body and not isinstance(body["kids"], bool):
        raise HTTPException(422, "kids must be true or false.")
    with db.get_conn() as conn:
        rid = share_folders.create(conn, admin, body.get("path"), body.get("label"), body.get("access"),
                                   kids=bool(body.get("kids")))
    background.add_task(index.scan_root, rid)
    with db.get_conn() as conn:
        out = _payload(conn)
    out["id"] = rid
    return out


@router.patch("/{root_id}")
def change(root_id: str, body: dict = Body(...), admin: dict = Depends(require_admin)):
    """{label?, access?, kids?} — access replaces the whole list (people left out get No access)."""
    if not isinstance(body, dict) or not body or set(body) - {"label", "access", "kids"}:
        raise HTTPException(422, "Send label and/or access.")
    if "kids" in body and not isinstance(body["kids"], bool):
        raise HTTPException(422, "kids must be true or false.")
    with db.get_conn() as conn:
        share_folders.update(conn, admin, root_id[:64], body.get("label"), body.get("access"), kids=body.get("kids"))
        return _payload(conn)


@router.delete("/{root_id}")
def stop(root_id: str, admin: dict = Depends(require_admin)):
    with db.get_conn() as conn:
        share_folders.remove(conn, admin, root_id[:64])
        return _payload(conn)


@router.post("/{root_id}/rescan")
def rescan(root_id: str, admin: dict = Depends(require_admin)):
    with db.get_conn() as conn:
        share_folders.row(conn, root_id[:64])
    res = index.scan_root(root_id[:64])
    with db.get_conn() as conn:
        out = _payload(conn)
    out["scan"] = res
    out["ok"] = res is not None
    out["at"] = config.now_iso()
    return out
