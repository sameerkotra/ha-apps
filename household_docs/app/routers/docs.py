"""Documents (SPEC §7, §12): create, open, poll the etag, save a note, checklist operations, versions."""
import os

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import JSONResponse
from pydantic import Field

from .. import db, docops, documents, files, links, sharing, sheets
from ..auth import require_user
from ..formats import text as text_fmt
from ..store import index, kinds, nodes, paths, versions
from .common import Strict
from .nodes import node_info_json

router = APIRouter(prefix="/api/docs", tags=["docs"])


class CreateIn(Strict):
    kind: str = Field(min_length=1, max_length=20)
    name: str = Field(min_length=1, max_length=250)
    parentId: str | None = Field(default=None, max_length=70)
    text: str | None = Field(default=None, max_length=200_000)      # a first line or two (e.g. a note's start)
    format: str | None = Field(default=None, pattern="^(xlsx|csv)$")   # a sheet's file type (default .xlsx)


@router.post("", status_code=201)
def create(body: CreateIn, user: dict = Depends(require_user)):
    data = None
    if body.text is not None:
        k = kinds.get(body.kind) if body.kind in {x.id for x in kinds.all_kinds()} else None
        if k is None or body.kind not in documents.TEXT_KINDS:
            raise HTTPException(422, "Only notes can start with text.")
        data = text_fmt.encode(body.text)
    with db.get_conn() as conn:
        if body.format is not None and body.kind != "sheet":
            raise HTTPException(422, "Only sheets have a format.")
        nid = docops.create(conn, user, body.kind, body.name, body.parentId, data, ext=body.format)
        if body.text:
            links.update(conn, user, nodes.get(conn, nid), body.text)
        return node_info_json(conn, user, nid)


@router.get("/{node_id}")
def open_doc(node_id: str, user: dict = Depends(require_user)):
    with db.get_conn() as conn:
        sharing.require(conn, user, node_id, "viewer")          # 404 before touching the disk
    index.refresh_node(node_id)                                  # a change made outside the app is what you see
    with db.get_conn() as conn:
        return documents.open_doc(conn, user, node_id)


@router.get("/{node_id}/etag")
def etag(node_id: str, user: dict = Depends(require_user)):
    """For the editor's 10-second check: has it changed, and is someone else editing?"""
    with db.get_conn() as conn:
        sharing.require(conn, user, node_id, "viewer")
    row = index.refresh_node(node_id)
    with db.get_conn() as conn:
        if row is None:
            raise HTTPException(404, sharing.NOT_FOUND)
        row = nodes.get(conn, node_id)
        return {"etag": documents.etag_of_row(row), "kind": row["kind"], "name": row["name"],
                "modified": row["mtime"], "updatedBy": row["updated_by"],
                "updatedByName": documents._name_of(conn, row["updated_by"]),
                "editing": documents.editing_by(conn, user, row)}


class SaveIn(Strict):
    etag: str = Field(min_length=1, max_length=200)
    text: str | None = None                                          # a note: the whole text
    keepMine: bool = False
    cells: list[dict] | None = Field(default=None, max_length=50_000)   # a sheet: the changed cells
    tabs: list[dict] | None = Field(default=None, max_length=10)        # … and tab settings
    sheet: dict | None = None                                           # … or the whole sheet (a change of shape)
    links: dict[str, str] | None = Field(default=None, max_length=200)  # a note: [[text]] → the id the picker chose


@router.patch("/{node_id}")
def save(node_id: str, body: SaveIn, user: dict = Depends(require_user)):
    with db.get_conn() as conn:
        node, _role = sharing.require(conn, user, node_id, "viewer")
        if node["kind"] == "sheet":
            answer, conflicts = sheets.save(conn, user, node_id, body.model_dump())
            if conflicts or answer.get("tabsNotSaved"):
                msg = (conflicts[0]["message"] if len(conflicts) == 1 else
                       f"{len(conflicts)} cells were changed by someone else while you were editing them." if conflicts
                       else "Someone changed this sheet meanwhile, so your column widths, frozen panes, totals, colour "
                            "rules or charts weren't saved — the sheet has been updated; please do them again.")
                answer = {"message": msg, "conflicts": conflicts, **answer}
                return JSONResponse(status_code=409, content={"detail": answer})
            return answer
        if body.text is None:
            raise HTTPException(422, "Send the text.")
        return documents.save_note(conn, user, node_id, body.etag, body.text, body.keepMine, body.links)


class OpsIn(Strict):
    ops: list[dict] = Field(min_length=1, max_length=200)


@router.post("/{node_id}/checklist")
def checklist(node_id: str, body: OpsIn, user: dict = Depends(require_user)):
    with db.get_conn() as conn:
        out, conflicts = documents.checklist_ops(conn, user, node_id, body.ops)
    if conflicts:
        out["conflicts"] = conflicts
        return JSONResponse(status_code=409, content={"detail": {"message": conflicts[0]["message"], **out}})
    return out


@router.get("/{node_id}/versions")
def list_versions(node_id: str, user: dict = Depends(require_user)):
    with db.get_conn() as conn:
        node, role = sharing.require(conn, user, node_id, "viewer")
        return {"versions": versions.listing(conn, node["id"]), "canRestore": sharing.at_least(role, "editor")}


@router.get("/{node_id}/versions/{n}")
def version_text(node_id: str, n: int, user: dict = Depends(require_user)):
    """One version's text (to look at or compare before restoring)."""
    with db.get_conn() as conn:
        node, _role = sharing.require(conn, user, node_id, "viewer")
        root = conn.execute("SELECT * FROM roots WHERE id = ?", (node["root_id"],)).fetchone()
        try:
            data = versions.read(conn, root, node["id"], n)
        except FileNotFoundError:
            raise HTTPException(404, "That version isn't there any more.")
    if node["kind"] == "sheet":
        raise HTTPException(415, "Download this version to look at it.")
    try:
        text, _m = text_fmt.decode(data)
    except text_fmt.NotText as e:
        raise HTTPException(415, str(e))
    return {"n": n, "text": text}


@router.get("/{node_id}/versions/{n}/file")
def version_file(node_id: str, n: int, user: dict = Depends(require_user)):
    """One kept version as a download (any kind of file; never inline)."""
    with db.get_conn() as conn:
        node, _role = sharing.require(conn, user, node_id, "viewer")
        root = conn.execute("SELECT * FROM roots WHERE id = ?", (node["root_id"],)).fetchone()
        try:
            path = versions.path_of(conn, root, node["id"], n)
        except FileNotFoundError:
            raise HTTPException(404, "That version isn't there any more.")
    stem, ext = paths.split_ext(node["name"])
    ext = paths.split_ext(os.path.basename(path))[1] or ext          # a .csv converted to .xlsx keeps its .csv versions
    return files.download_response(path, f"{stem} (version {n})" + (f".{ext}" if ext else ""))


@router.post("/{node_id}/versions/{n}/restore")
def restore_version(node_id: str, n: int, user: dict = Depends(require_user)):
    with db.get_conn() as conn:
        return documents.restore_version(conn, user, node_id, n)
