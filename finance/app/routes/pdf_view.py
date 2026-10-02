"""In-app PDF viewer (see app/pdfview.py for why it renders page images).

    GET /pdf-view?kind=statement|utility|toll&id=N[&q=text][&amount=12.34][&embed=1]
    GET /pdf-page?kind=...&id=N&page=P            (one page as a PNG)

Opened in the same window (or inside a modal on the Uploads page), never a new
tab — a new tab is handed to the phone's own browser by the Home Assistant app,
which has no Home Assistant session and answers 401.

Scoping: the acting user's own rows; an admin (the debug views' audience) may
open any row. Only files inside PENDING_DIR are ever read (app/storage.py). The PDF is gone once a
statement has completed, in which case the page says so instead of erroring.
"""
import sqlite3
from pathlib import Path

from fastapi import APIRouter, Depends, Request
from fastapi.responses import Response
from fastapi.templating import Jinja2Templates

from ..auth import User, get_acting_user, get_current_user
from .. import storage
from ..db import get_db
from ..pdfview import find_hits, load_pages, render_page
from ..version import APP_VERSION
from .accounts import _acting_banner, _acting_qs
from .upload import statement_flows

router = APIRouter()
templates = Jinja2Templates(directory=str(Path(__file__).parent.parent / "templates"))
templates.env.globals["app_version"] = APP_VERSION


def _money(value: float) -> str:
    return f"{value:,.2f}"


def _resolve(conn: sqlite3.Connection, kind: str, id_: int, current: User, acting: User) -> dict | None:
    """What the viewer needs about one document, or None when it isn't the caller's."""
    if kind == "statement":
        sql = ("SELECT s.id, s.user_id, s.pdf_path, s.original_filename, s.status, s.needs_confirmation, s.confirmed_at, "
               "a.name AS account_name FROM statements s JOIN accounts a ON a.id = s.account_id "
               "WHERE s.id = ? AND s.deleted_at IS NULL")
    elif kind == "utility":
        sql = ("SELECT id, user_id, pdf_path, original_filename, provider, cost FROM utility_bills "
               "WHERE id = ? AND deleted_at IS NULL")
    elif kind == "toll":
        sql = ("SELECT id, user_id, pdf_path, original_filename, total_tolls_printed FROM toll_statements "
               "WHERE id = ? AND deleted_at IS NULL")
    else:
        return None
    params: list = [id_]
    if not current.is_admin:
        sql += " AND " + ("s." if kind == "statement" else "") + "user_id = ?"
        params.append(acting.id)
    row = conn.execute(sql, params).fetchone()
    if row is None:
        return None
    doc = dict(row)
    doc["kind"] = kind
    if kind == "statement":
        flow = statement_flows(conn, row["user_id"], id_).get(id_, {"money_in": 0, "money_out": 0})
        doc["title"] = f"{row['account_name']} — {row['original_filename'] or 'statement'}"
        doc["targets"] = [
            {"kind": "in", "label": "Money in", "amount": flow["money_in"]},
            {"kind": "out", "label": "Money out", "amount": flow["money_out"]},
        ]
        doc["can_confirm"] = (row["status"] == "pending_review" and bool(row["needs_confirmation"])
                              and not row["confirmed_at"] and row["user_id"] == acting.id)
        doc["back"] = "uploads"
    elif kind == "toll":
        doc["title"] = f"Tolls — {row['original_filename'] or 'toll statement'}"
        # The printed Grand Totals is what the passes are checked against; it is highlighted so it
        # can be found (it prints negative; the viewer matches the absolute value).
        doc["targets"] = ([{"kind": "total", "label": "Grand Totals", "amount": row["total_tolls_printed"]}]
                          if row["total_tolls_printed"] else [])
        doc["can_confirm"] = False
        doc["back"] = "tolls?tab=upload"
    else:
        doc["title"] = f"{row['provider']} — {row['original_filename'] or 'bill'}"
        doc["targets"] = [{"kind": "total", "label": "Bill total", "amount": row["cost"]}] if row["cost"] is not None else []
        doc["can_confirm"] = False
        doc["back"] = "utilities?tab=upload"
    return doc


def _path_or_none(doc: dict) -> str | None:
    return storage.stored_path(doc["pdf_path"])


@router.get("/pdf-view")
def pdf_view(
    request: Request,
    kind: str,
    id: int,
    q: str = "",
    embed: int = 0,
    current: User = Depends(get_current_user),
    acting: User = Depends(get_acting_user),
):
    conn = get_db()
    try:
        doc = _resolve(conn, kind, id, current, acting)
    finally:
        conn.close()

    qs = _acting_qs(current, acting)
    context = {
        "user": acting, "embed": bool(embed), "doc": doc, "q": q.strip(), "pages": [], "summary": [],
        "acting_qs": qs, "acting_as_banner": _acting_banner(current, acting),
        "active_page": {"utility": "utilities", "toll": "tolls"}.get(kind, "uploads"), "tab": "upload",
        "error": None,
    }
    if doc is None:
        return templates.TemplateResponse(request, "not_found.html", {"user": acting}, status_code=404)

    path = _path_or_none(doc)
    if path is None:
        context["error"] = "The PDF is no longer available — it is deleted once a statement completes."
        return templates.TemplateResponse(request, "pdf_view.html", context)

    try:
        pages = load_pages(path)
    except Exception as exc:  # surfaced on the page, not a 500
        context["error"] = f"Could not read this PDF: {exc}"
        return templates.TemplateResponse(request, "pdf_view.html", context)

    # Figures to highlight: the app's own totals, any ?amount=, and the search text.
    targets = [t for t in doc["targets"] if t.get("amount")]
    for raw in request.query_params.getlist("amount"):
        try:
            targets.append({"kind": "find", "label": "Amount", "amount": abs(float(raw))})
        except ValueError:
            pass
    if context["q"]:
        targets.append({"kind": "find", "label": context["q"], "text": context["q"]})
    per_page = find_hits(pages, targets)

    context["pages"] = [
        {"number": i + 1, "width": p["width"], "height": p["height"], "hits": per_page[i]}
        for i, p in enumerate(pages)
    ]
    # One line per figure: where it was found (page numbers) or that it wasn't.
    for t in doc["targets"]:
        found = sorted({pg["number"] for pg in context["pages"] for h in pg["hits"] if h["kind"] == t["kind"]}) if t.get("amount") else []
        context["summary"].append({
            "kind": t["kind"], "label": t["label"], "amount": _money(t["amount"]) if t["amount"] is not None else "—",
            "pages": found, "searched": bool(t.get("amount")),
        })
    if context["q"]:
        pages_found = sorted({pg["number"] for pg in context["pages"] for h in pg["hits"] if h["kind"] == "find" and h["label"] == context["q"]})
        context["q_pages"] = pages_found
    return templates.TemplateResponse(request, "pdf_view.html", context)


@router.get("/pdf-page")
def pdf_page(
    kind: str,
    id: int,
    page: int,
    current: User = Depends(get_current_user),
    acting: User = Depends(get_acting_user),
):
    conn = get_db()
    try:
        doc = _resolve(conn, kind, id, current, acting)
    finally:
        conn.close()
    path = _path_or_none(doc) if doc else None
    if path is None:
        return Response("PDF not found", status_code=404)
    if not 1 <= page <= len(load_pages(path)):
        return Response("No such page", status_code=404)
    return Response(render_page(path, page), media_type="image/png")
