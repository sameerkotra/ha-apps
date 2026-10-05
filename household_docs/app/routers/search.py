"""Search (SPEC §10, §12): the Search page (and the header box, which opens it), the CSV of a result list, and saved
and recent searches. The access check is part of every search query (search/engine.py); nothing anyone can't open
is ever in an answer."""
from fastapi import APIRouter, Body, Depends, HTTPException, Query, Request
from fastapi.responses import StreamingResponse

from .. import config, db, files
from ..auth import require_user
from ..common import csv_export
from ..search import engine, filters as flt, regex_worker, saved

router = APIRouter(prefix="/api", tags=["search"])


def _raw(request: Request) -> dict:
    raw = {}
    for k in ("q",) + engine.OPTION_KEYS:
        vals = request.query_params.getlist(k)
        if not vals:
            continue
        raw[k] = [x for v in vals for x in v.split(",")] if k in engine.LIST_KEYS else vals[-1]
    return raw


@router.get("/search")
def search(request: Request, page: int = Query(default=1, ge=1, le=100),
           limit: int = Query(default=engine.MAX_RESULTS, ge=1, le=engine.MAX_RESULTS),
           user: dict = Depends(require_user)):
    """?q= plus the Search page's options (match, nameMode, contentMode, scope, sub, trash) and filters (modified,
    created, type, ext, size, by, owner, shared, access, is, path, tag, color), sort, dir, group, page."""
    o = engine.options(_raw(request))
    with db.get_conn() as conn:
        try:
            res = engine.run(conn, user, o, limit=limit)
        except regex_worker.Busy:
            raise HTTPException(429, "Your previous pattern search is still running — wait for it to finish.")
        out = engine.describe(conn, user, res, page)
    out["q"] = o["q"]
    out["options"] = {k: o[k] for k in o if k != "q"}
    return out


@router.get("/search/export")
def export(request: Request, user: dict = Depends(require_user)):
    """The result list as CSV — names, locations, types, dates, sizes; never any content."""
    o = engine.options(_raw(request))
    with db.get_conn() as conn:
        try:
            res = engine.run(conn, user, o)
        except regex_worker.Busy:
            raise HTTPException(429, "Your previous pattern search is still running — wait for it to finish.")
        names = {r["id"]: r["name"] for r in conn.execute("SELECT id, name FROM users")}
        rows = []
        for r, loc in engine.export_rows(conn, user, res):
            rows.append([csv_export.text(r["name"]), csv_export.text(loc),
                         flt.type_label_of(r["kind"], r["ext"]), r["mtime"] or "", r["ctime"] or "",
                         csv_export.text("outside the app" if r["updated_by"] is None else names.get(r["updated_by"], "")),
                         "" if r["size"] is None else r["size"]])
    header = ["Name", "Location", "Type", "Modified (UTC)", "Created (UTC)", "Modified by", "Size (bytes)"]
    name = f"Search results {config.now().strftime('%Y-%m-%d')}.csv"
    headers = files.download_headers(name)
    return StreamingResponse(csv_export.stream(header, rows, bom=True), media_type="text/csv; charset=utf-8",
                             headers=headers)


# ---------- saved and recent searches ----------
@router.get("/searches")
def list_saved(user: dict = Depends(require_user)):
    with db.get_conn() as conn:
        return {"searches": saved.listing(conn, user["id"])}


@router.post("/searches", status_code=201)
def save_search(body: dict = Body(...), user: dict = Depends(require_user)):
    """{name, q, filters, pinned?}"""
    if not isinstance(body, dict) or set(body) - {"name", "q", "filters", "pinned"}:
        raise HTTPException(422, "Send name, q, filters and pinned.")
    with db.get_conn() as conn:
        s = saved.save(conn, user["id"], body.get("name"), body.get("q"), body.get("filters"), bool(body.get("pinned")))
        return {"search": s, "searches": saved.listing(conn, user["id"])}


@router.patch("/searches/{sid}")
def change_search(sid: str, body: dict = Body(...), user: dict = Depends(require_user)):
    if not isinstance(body, dict) or not body or set(body) - {"name", "pinned"}:
        raise HTTPException(422, "Send name and/or pinned.")
    if "pinned" in body and not isinstance(body["pinned"], bool):
        raise HTTPException(422, "pinned is true or false.")
    with db.get_conn() as conn:
        s = saved.change(conn, user["id"], sid[:64], body.get("name"), body.get("pinned"))
        return {"search": s, "searches": saved.listing(conn, user["id"])}


@router.delete("/searches/recent")
def clear_recent(user: dict = Depends(require_user)):
    with db.get_conn() as conn:
        saved.clear_recent(conn, user["id"])
    return {"recent": []}


@router.delete("/searches/{sid}")
def delete_search(sid: str, user: dict = Depends(require_user)):
    with db.get_conn() as conn:
        saved.delete(conn, user["id"], sid[:64])
        return {"searches": saved.listing(conn, user["id"])}


@router.get("/searches/recent")
def recent(user: dict = Depends(require_user)):
    with db.get_conn() as conn:
        return {"recent": saved.recent(conn, user["id"])}


@router.post("/searches/recent")
def add_recent(body: dict = Body(...), user: dict = Depends(require_user)):
    """{q, filters}: a search someone ran (on Enter, or when they opened a result)."""
    if not isinstance(body, dict) or set(body) - {"q", "filters"}:
        raise HTTPException(422, "Send q and filters.")
    with db.get_conn() as conn:
        saved.record_recent(conn, user["id"], body.get("q"), body.get("filters"))
        return {"recent": saved.recent(conn, user["id"])}
