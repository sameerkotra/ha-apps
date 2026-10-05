"""Organise (SPEC §17.1, §17.3, §17.12, §17.13): tags and colours, links between documents and backlinks, PDF of a
note or checklist, pins on the home page and Quick note."""
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import Response
from pydantic import Field

from .. import db, docpdf, documents, files, links, pins, sharing
from .. import tags as tagmod
from ..auth import require_user
from ..formats import text as text_fmt
from ..store import index
from .common import Strict
from .nodes import node_info_json

router = APIRouter(prefix="/api", tags=["organise"])


# ---------------------------------------------------------------- tags and colours
@router.get("/tags")
def all_tags(user: dict = Depends(require_user)):
    with db.get_conn() as conn:
        return {"tags": tagmod.listing(conn, user), "colours": list(tagmod.COLOURS)}


@router.get("/nodes/{node_id}/tags")
def node_tags(node_id: str, user: dict = Depends(require_user)):
    with db.get_conn() as conn:
        node, role = sharing.require(conn, user, node_id, "viewer")
        return {"tags": tagmod.of(conn, node_id), "color": node["color"], "canEdit": sharing.at_least(role, "editor")}


class TagsIn(Strict):
    tags: list[str] | None = Field(default=None, max_length=tagmod.MAX_TAGS * 2)
    add: list[str] | None = Field(default=None, max_length=tagmod.MAX_TAGS)
    remove: list[str] | None = Field(default=None, max_length=tagmod.MAX_TAGS * 2)
    color: str | None = Field(default="keep", max_length=10)


@router.put("/nodes/{node_id}/tags")
def put_tags(node_id: str, body: TagsIn, user: dict = Depends(require_user)):
    """{tags: [...], color?: "red" | null} — replaces the tags."""
    if body.tags is None:
        raise HTTPException(422, "Send the tags.")
    with db.get_conn() as conn:
        return tagmod.change(conn, user, node_id, tags=body.tags, color=body.color)


@router.post("/nodes/{node_id}/tags")
def post_tags(node_id: str, body: TagsIn, user: dict = Depends(require_user)):
    """{add?: [...], remove?: [...], color?} — the selection bar's Tag action."""
    with db.get_conn() as conn:
        return tagmod.change(conn, user, node_id, add=body.add, remove=body.remove, color=body.color)


# ---------------------------------------------------------------- links
@router.get("/docs/{node_id}/links")
def doc_links(node_id: str, user: dict = Depends(require_user)):
    """The note's [[links]] as this person may see them, and Linked from (only what they can open)."""
    with db.get_conn() as conn:
        node, _role = sharing.require(conn, user, node_id, "viewer")
        text = None
        if node["kind"] in links.TEXT_KINDS:
            try:
                _r, _p, data, _e, _s, _h = documents._read(conn, node)
                text = text_fmt.decode(data)[0]
            except Exception:
                text = None
        return links.describe(conn, user, node, text)


# ---------------------------------------------------------------- PDF
@router.get("/docs/{node_id}/pdf")
def pdf(node_id: str, user: dict = Depends(require_user)):
    with db.get_conn() as conn:
        sharing.require(conn, user, node_id, "viewer")
    index.refresh_node(node_id)
    with db.get_conn() as conn:
        name, data, bad = docpdf.make(conn, user, node_id)
    headers = files.download_headers(name)
    headers["X-Docs-Unsupported"] = str(bad)
    return Response(data, media_type="application/pdf", headers=headers)


@router.post("/docs/{node_id}/pdf", status_code=201)
def save_pdf(node_id: str, user: dict = Depends(require_user)):
    """Save PDF here: the PDF as a file beside the document."""
    nid, bad = docpdf.save_here(user, node_id)
    with db.get_conn() as conn:
        out = node_info_json(conn, user, nid)
    out["unsupported"] = bad
    return out


# ---------------------------------------------------------------- pins
@router.get("/me/pins")
def my_pins(user: dict = Depends(require_user)):
    with db.get_conn() as conn:
        return {"pins": pins.cards(conn, user), "max": pins.MAX_PINS}


class PinsOrderIn(Strict):
    ids: list[str] = Field(max_length=50)


@router.put("/me/pins")
def reorder_pins(body: PinsOrderIn, user: dict = Depends(require_user)):
    with db.get_conn() as conn:
        pins.reorder(conn, user, body.ids)
        return {"pins": pins.cards(conn, user), "max": pins.MAX_PINS}


class PinIn(Strict):
    value: bool = True
    cell: dict | None = None


@router.post("/nodes/{node_id}/pin")
def pin(node_id: str, body: PinIn, user: dict = Depends(require_user)):
    with db.get_conn() as conn:
        return pins.set_pin(conn, user, node_id, body.value, body.cell)


# ---------------------------------------------------------------- Quick note
@router.post("/quick-note", status_code=201)
def quick_note(user: dict = Depends(require_user)):
    with db.get_conn() as conn:
        nid = pins.quick_note(conn, user)
        out = node_info_json(conn, user, nid)
    out["quick"] = True
    return out


@router.post("/quick-note/{node_id}/finish")
def finish_quick_note(node_id: str, user: dict = Depends(require_user)):
    with db.get_conn() as conn:
        return pins.finish(conn, user, node_id)
