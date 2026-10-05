"""Sheets (SPEC §8.3–§8.4, §12): export (.xlsx, or CSV of a tab — values or formulas), import a .csv / .xlsx
into a new sheet, Convert to .xlsx (a .csv sheet), Save a copy to edit (an .xlsx made elsewhere). Opening and
saving go through /api/docs like every document."""
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import Response

from .. import db, files, settings, sheets
from ..auth import require_user
from .nodes import node_info_json

router = APIRouter(prefix="/api", tags=["sheets"])

XLSX_TYPE = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


@router.get("/docs/{node_id}/export")
def export(node_id: str, format: str = Query(default="xlsx", pattern="^(xlsx|csv)$"),
           tab: str | None = Query(default=None, max_length=40),
           mode: str = Query(default="values", pattern="^(values|formulas)$"), user: dict = Depends(require_user)):
    with db.get_conn() as conn:
        if format == "xlsx":
            name, data = sheets.export_xlsx(conn, user, node_id)
            mime = XLSX_TYPE
        else:
            name, data = sheets.export_csv(conn, user, node_id, tab, mode)
            mime = "text/csv; charset=utf-8"
    return Response(data, media_type=mime, headers=files.download_headers(name))


@router.post("/docs/{node_id}/convert")
def convert(node_id: str, user: dict = Depends(require_user)):
    """A .csv sheet becomes an .xlsx (the .csv is kept under History); a note switches between plain text (.txt)
    and Markdown (.md) (§17.2)."""
    from .. import documents, sharing
    with db.get_conn() as conn:
        node, _role = sharing.require(conn, user, node_id, "viewer")
        if node["kind"] in documents.TEXT_KINDS:
            nid = documents.convert_note(conn, user, node_id)
        else:
            from .. import kids
            kids.refuse_sheet(user, node["kind"])
            nid = sheets.convert_to_xlsx(conn, user, node_id)
        return node_info_json(conn, user, nid)


@router.post("/docs/{node_id}/edit-copy", status_code=201)
def edit_copy(node_id: str, user: dict = Depends(require_user)):
    """Save a copy to edit: a new .xlsx the app owns, beside the original (or in My docs)."""
    with db.get_conn() as conn:
        nid = sheets.save_copy(conn, user, node_id)
        return node_info_json(conn, user, nid)


@router.post("/import", status_code=201)
async def import_sheet(request: Request, name: str = Query(min_length=1, max_length=255),
                       parentId: str | None = Query(default=None, max_length=70),
                       format: str = Query(default="xlsx", pattern="^(xlsx|csv)$"), user: dict = Depends(require_user)):
    """A .csv or .xlsx (the request body is the file) into a new sheet in a folder you can edit."""
    with db.get_conn() as conn:
        limit = int(settings.get("max_doc_mb", conn)) * 1024 * 1024
    length = request.headers.get("content-length")
    if length and length.isdigit() and int(length) > limit:
        raise HTTPException(413, f"A sheet can be at most {limit // (1024 * 1024)} MB.")
    chunks, size = [], 0
    async for chunk in request.stream():
        size += len(chunk)
        if size > limit:
            raise HTTPException(413, f"A sheet can be at most {limit // (1024 * 1024)} MB.")
        chunks.append(chunk)
    data = b"".join(chunks)

    def run():
        with db.get_conn() as conn:
            nid, left_out = sheets.import_file(conn, user, name, data, parentId or None, format)
            out = node_info_json(conn, user, nid)
            out["leftOut"] = left_out
            return out
    from starlette.concurrency import run_in_threadpool
    return await run_in_threadpool(run)
