"""Utilities (SPEC.md section 15): upload, status, restart, delete and
restore, the Dashboard / Upload / Compare tabs, review and the admin debug view.
Kept separate from the finance routes on purpose (utility bills never touch
accounts or transactions); parsing runs on the shared job queue (app/jobs.py).
"""
import asyncio
from datetime import datetime
from pathlib import Path

from fastapi import APIRouter, Depends, File, Form, Request, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from .. import features as _features

from .. import ai_client as _ai_client

from .. import settings as _settings

from ..auth import User, get_current_user, get_acting_user, require_admin
from ..charts import column_chart
from ..db import get_db
from ..version import APP_VERSION
from ..paging import TableView
from ..parser.utility_pipeline import check_duplicate_utility, delete_pdf_utility, _METER_COLUMNS
from ..parser.utility_vision import debug_call_utility
from ..parser.documents import prepare_restart
from ..parser.vision import MAX_NOTES_CHARS, VisionExtractionError
from .accounts import _acting_banner, _acting_qs, htmx_fragment_requested
from .. import ai_usage, jobs, settings, storage

router = APIRouter(dependencies=[Depends(_features.required("utilities"))])
templates = Jinja2Templates(directory=str(Path(__file__).parent.parent / "templates"))
templates.env.globals["app_version"] = APP_VERSION
templates.env.globals["ai_privacy"] = _ai_client.privacy
templates.env.globals["ai_configured"] = _settings.ai_configured
templates.env.globals["ai_usage_view"] = ai_usage.view

PROVIDERS = ["Xcel Energy", "Aurora Water"]    # read with a tuned prompt; any other name is "Other" (section 23)
OTHER = "Other"
MAX_PROVIDER_NAME = 60


def _all_providers(user_id: str) -> list[str]:
    """The known providers, then any other provider this person has bills from (filters)."""
    with get_db() as conn:
        others = [r[0] for r in conn.execute(
            "SELECT DISTINCT provider FROM utility_bills WHERE user_id = ? AND deleted_at IS NULL AND provider IS NOT NULL "
            "ORDER BY provider COLLATE NOCASE", (user_id,)) if r[0] not in PROVIDERS]
    return PROVIDERS + others


def _provider_name(provider: str, other_name: str) -> tuple[str | None, str | None]:
    """(provider to store, error). "Other" takes the typed name; a typed known name maps to it."""
    if provider in PROVIDERS:
        return provider, None
    if provider != OTHER:
        return None, f"Unknown provider {provider!r}"
    name = " ".join((other_name or "").split())[:MAX_PROVIDER_NAME]
    if not name:
        return None, "Type the provider's name (e.g. Denver Water)."
    for known in PROVIDERS:
        if known.casefold() == name.casefold():
            return known, None
    return name, None


@router.post("/utility-upload")
async def upload_utility_bill(
    request: Request,
    provider: str = Form(...),
    provider_other: str = Form(""),
    files: list[UploadFile] = File(...),
    current: User = Depends(get_current_user),
    acting: User = Depends(get_acting_user),
):
    """Multiple PDFs per request — upload several utility bills at once for
    the same provider. Creates one placeholder row per file (migration 0007 —
    everything but `provider` nullable); process_utility_bill fills in the
    rest, and may INSERT a second row for a dual-fuel Xcel statement's gas
    entry."""
    provider, problem = _provider_name(provider, provider_other)
    if problem:
        return JSONResponse({"error": problem}, status_code=400)

    results = []
    conn = get_db()
    try:
        for file in files:
            try:
                content = await storage.read_upload(file, storage.MAX_PDF_BYTES, pdf=True)
            except storage.UploadRejected as e:
                results.append({"filename": file.filename, "error": str(e)})
                continue
            tmp_path, hash_ = await asyncio.to_thread(
                storage.save_file, storage.user_dir(acting.id, "utility-bills"), "utility", ".pdf", content)

            dup = check_duplicate_utility(conn, acting.id, hash_)
            if dup is not None:
                storage.remove_stored_file(tmp_path)
                if dup["status"] == "error":
                    results.append({
                        "filename": file.filename, "duplicate_of": dup["id"],
                        "message": "Already uploaded and failed to process — restart it instead of uploading again.",
                    })
                else:
                    results.append({
                        "filename": file.filename, "duplicate_of": dup["id"],
                        "message": "Already uploaded.",
                    })
                continue

            cur = conn.execute(
                "INSERT INTO utility_bills (user_id, provider, original_filename, pdf_path, file_hash, status) "
                "VALUES (?, ?, ?, ?, ?, 'processing')",
                (acting.id, provider, file.filename, tmp_path, hash_),
            )
            conn.commit()
            utility_bill_id = cur.lastrowid

            jobs.start_job("utility_bill", utility_bill_id, acting.id)
            results.append({"filename": file.filename, "utility_bill_id": utility_bill_id, "status": "processing"})
    finally:
        conn.close()

    return JSONResponse({"results": results})


@router.get("/utility-status")
def utility_status(
    request: Request,
    id: int,
    current: User = Depends(get_current_user),
    acting: User = Depends(get_acting_user),
):
    """Polled by htmx (hx-trigger="every 3s") while a bill is processing.

    While it is still processing, returns the refreshed status badge (HTML,
    not JSON). The moment it is anything else — done, needs review, failed,
    or deleted meanwhile — the response is an HX-Refresh instead: the
    provider / type / period / usage / cost cells only fill in once parsing
    finishes, and swapping just the badge left them blank until the person
    reloaded by hand. (Dual-fuel PDFs also add a sibling row.)"""
    conn = get_db()
    try:
        row = conn.execute(
            "SELECT id, status, error_message, original_filename, current_step FROM utility_bills "
            "WHERE id = ? AND user_id = ? AND deleted_at IS NULL",
            (id, acting.id),
        ).fetchone()
    finally:
        conn.close()

    if row is None or row["status"] != "processing":
        return HTMLResponse("", headers={"HX-Refresh": "true"})
    return templates.TemplateResponse(
        request, "_utility_status_fragment.html",
        {"row": row, "acting_qs": _acting_qs(current, acting)},
    )


@router.post("/utility-restart")
def utility_restart(
    request: Request,
    id: int,
    current: User = Depends(get_current_user),
    acting: User = Depends(get_acting_user),
):
    """Re-run a failed bill from its already-uploaded PDF (see /statement-restart)."""
    conn = get_db()
    try:
        row, problem = prepare_restart("utility_bills", conn, id, acting.id)
        if problem:
            return JSONResponse({"error": problem}, status_code=404 if row is None else 400)
        updated = conn.execute(
            "SELECT id, status, error_message, current_step FROM utility_bills WHERE id = ?", (id,)
        ).fetchone()
    finally:
        conn.close()

    jobs.start_job("utility_bill", id, acting.id)
    return templates.TemplateResponse(
        request, "_utility_status_fragment.html",
        {"row": updated, "acting_qs": _acting_qs(current, acting)},
    )


@router.post("/utility-delete")
def utility_delete(
    request: Request,
    id: int = Form(...),
    current: User = Depends(get_current_user),
    acting: User = Depends(get_acting_user),
):
    """Soft delete (section 11), confirmed with a plain JS confirm(): a bill
    is never linked to anything else, so there's no cascade to warn about.
    The PDF and meter details are kept so Restore brings back the same bill."""
    conn = get_db()
    try:
        conn.execute(
            "UPDATE utility_bills SET deleted_at = datetime('now') "
            "WHERE id = ? AND user_id = ? AND deleted_at IS NULL",
            (id, acting.id),
        )
        conn.commit()
    finally:
        conn.close()

    qs = _acting_qs(current, acting)
    target = "utilities?tab=upload" + (f"&{qs}" if qs else "")  # delete lives in the upload history
    return RedirectResponse(url=target, status_code=303)


@router.post("/utility-restore")
def utility_restore(
    request: Request,
    id: int = Form(...),
    current: User = Depends(get_current_user),
    acting: User = Depends(get_acting_user),
    _admin: User = Depends(require_admin),  # Recently deleted is admin only (SPEC.md section 11)
):
    """Undo a soft delete (linked from Recently deleted)."""
    conn = get_db()
    try:
        conn.execute(
            "UPDATE utility_bills SET deleted_at = NULL "
            "WHERE id = ? AND user_id = ? AND deleted_at IS NOT NULL",
            (id, acting.id),
        )
        conn.commit()
    finally:
        conn.close()

    qs = _acting_qs(current, acting)
    target = "recently-deleted" + (f"?{qs}" if qs else "")
    return RedirectResponse(url=target, status_code=303)


def _build_utility_bill_conditions(user_id: str, provider: str, utility_type: str,
                                    start_date: str, end_date: str) -> tuple[list[str], list]:
    """The Dashboard tab's filter conditions. Dates filter on the billing
    period's start (not the upload date): "bills from March" means March's
    billing period."""
    conditions = ["user_id = ?", "deleted_at IS NULL"]
    params: list = [user_id]

    if provider.strip():
        conditions.append("provider = ?")
        params.append(provider.strip())
    if utility_type in ("electric", "gas", "water"):
        conditions.append("utility_type = ?")
        params.append(utility_type)
    if start_date.strip():
        conditions.append("period_start_date >= ?")
        params.append(start_date.strip())
    if end_date.strip():
        conditions.append("period_start_date <= ?")
        params.append(end_date.strip())

    return conditions, params


def _lower(text):
    return text.lower() if text else None


# Upload-tab history sort keys. "period" falls back to the upload time for a
# bill with no period yet (still processing), the same order this list always
# had; None values sort last in either direction.
_BILL_SORT_KEYS = {
    "file": lambda b: _lower(b["original_filename"]),
    "provider": lambda b: _lower(b["provider"]),
    "type": lambda b: b["utility_type"],
    "uploaded": lambda b: b["uploaded_at"],
    "period": lambda b: b["period_start_date"] or b["uploaded_at"],
    "usage": lambda b: b["usage_amount"],
    "cost": lambda b: b["cost"],
    "status": lambda b: b["status"],
}
BILL_SORT_COLUMNS = [
    ("file", "File"), ("uploaded", "Uploaded"), ("provider", "Provider"), ("type", "Type"), ("period", "Period"),
    ("usage", "Usage"), ("cost", "Cost"), ("status", "Status"),
]


@router.get("/utilities")
def utilities_page(
    request: Request,
    tab: str = "dashboard",
    provider: str = "",
    utility_type: str = "",
    start_date: str = "",
    end_date: str = "",
    current: User = Depends(get_current_user),
    acting: User = Depends(get_acting_user),
):
    """One route, two tabs picked by `tab` (unknown values mean "dashboard").
    The Dashboard's filters swap only #utility-bills-table-wrap through htmx.
    Bills are LEFT JOINed to electric_meter_details (NULL for gas/water) and
    grouped by type in Python for the per-type tables."""
    tab = tab if tab in ("upload", "dashboard") else "dashboard"

    conditions, params = _build_utility_bill_conditions(
        acting.id, provider, utility_type, start_date, end_date
    )
    meter_cols = ", ".join(f"m.{c}" for c in _METER_COLUMNS)
    conn = get_db()
    try:
        bills = conn.execute(
            f"SELECT b.*, {meter_cols} FROM utility_bills b "
            "LEFT JOIN electric_meter_details m ON m.utility_bill_id = b.id "
            f"WHERE {' AND '.join(conditions)} "
            "ORDER BY COALESCE(b.period_start_date, b.uploaded_at) DESC, b.id DESC",
            params,
        ).fetchall()
    finally:
        conn.close()

    # Per-type tables, plus a bucket for bills whose type isn't known yet
    # (processing, or failed before extraction) so nothing disappears.
    grouped = {"untyped": [], "electric": [], "gas": [], "water": []}
    for b in bills:
        key = b["utility_type"] if b["utility_type"] in ("electric", "gas", "water") else "untyped"
        grouped[key].append(b)

    processing = any(b["status"] == "processing" for b in bills)

    # Upload tab: the whole history as one server-sorted, paged table.
    bill_view = None
    if tab == "upload":
        bill_view = TableView(
            request, "utilities", "ub_", bills, _BILL_SORT_KEYS, "uploaded",
            extra_qs=_acting_qs(current, acting),
        )
        bills = bill_view.rows

    context = {
        "bill_view": bill_view, "bill_sort_columns": BILL_SORT_COLUMNS, "processing": processing,
        "user": acting, "bills": bills, "grouped": grouped, "providers": _all_providers(acting.id),
        "upload_providers": PROVIDERS, "tab": tab,
        "filter_provider": provider, "filter_utility_type": utility_type,
        "filter_start_date": start_date, "filter_end_date": end_date,
        "acting_as_banner": _acting_banner(current, acting),
        "acting_qs": _acting_qs(current, acting),
        "is_admin": current.is_admin,  # current, NOT acting -- same reasoning as uploads_list
        "active_page": "utilities",
    }

    # Same full-page-vs-fragment split as transactions.py's list route --
    # an htmx-driven filter change gets back just the table wrapper, a
    # direct page load/tab switch gets the whole page. Only the
    # Dashboard tab's filter form ever fires an htmx request against
    # this route (the Upload tab has no filters), so the fragment
    # returned here is always the by-type one, never the plain one.
    if htmx_fragment_requested(request):
        return templates.TemplateResponse(request, "_utility_bills_by_type.html", context)
    return templates.TemplateResponse(request, "utilities_list.html", context)


@router.get("/utility-debug")
def utility_debug(
    request: Request,
    id: int,
    admin: User = Depends(require_admin),
):
    """Admin-only troubleshooting view, mirroring /statement-debug
    (SPEC.md section 13) — same "not scoped to the acting user"
    reasoning: an admin debugging needs to see any bill, including ones
    belonging to a user they aren't currently viewing as."""
    conn = get_db()
    try:
        row = conn.execute("SELECT * FROM utility_bills WHERE id = ?", (id,)).fetchone()
        meter = conn.execute(
            "SELECT * FROM electric_meter_details WHERE utility_bill_id = ?", (id,)
        ).fetchone() if row else None
    finally:
        conn.close()

    if row is None:
        return templates.TemplateResponse(request, "not_found.html", {"user": admin}, status_code=404)

    return templates.TemplateResponse(
        request, "utility_debug.html",
        {"user": admin, "bill": row, "meter": meter, "active_page": "utilities", "tab": "upload"},
    )


@router.post("/utility-debug-resend")
def utility_debug_resend(
    request: Request,
    id: int,
    admin: User = Depends(require_admin),
):
    """Admin debug action, deliberately synchronous — mirrors
    /statement-debug-resend exactly, using the utility-bill prompt."""
    def _load(conn):
        r = conn.execute("SELECT * FROM utility_bills WHERE id = ?", (id,)).fetchone()
        m = conn.execute(
            "SELECT * FROM electric_meter_details WHERE utility_bill_id = ?", (id,)
        ).fetchone() if r else None
        return r, m

    conn = get_db()
    try:
        row, meter = _load(conn)
    finally:
        conn.close()

    if row is None:
        return templates.TemplateResponse(request, "not_found.html", {"user": admin}, status_code=404)

    if not storage.stored_path(row["pdf_path"]):
        return templates.TemplateResponse(
            request, "utility_debug.html",
            {
                "user": admin, "bill": row, "meter": meter, "active_page": "utilities", "tab": "upload",
                "resend_error": "Source PDF no longer exists (already resolved and deleted) — can't resend.",
            },
        )

    try:
        (elapsed, raw), resend_usage = jobs.exclusive_recorded(debug_call_utility, row["pdf_path"],
                                                               settings.ai_url(), settings.ai_model(),
                                                               row["provider"], row["extraction_notes"] or "")
    except VisionExtractionError as e:
        return templates.TemplateResponse(
            request, "utility_debug.html",
            {"user": admin, "bill": row, "meter": meter, "active_page": "utilities", "tab": "upload", "resend_error": str(e)},
        )

    conn = get_db()
    try:
        conn.execute("UPDATE utility_bills SET llm_raw_response = ? WHERE id = ?", (raw, id))
        conn.commit()
        row, meter = _load(conn)
    finally:
        conn.close()

    return templates.TemplateResponse(
        request, "utility_debug.html",
        {"user": admin, "bill": row, "meter": meter, "active_page": "utilities", "tab": "upload", "resend_elapsed": elapsed,
         "resend_usage": resend_usage},
    )


@router.get("/utility-review")
def utility_review(
    request: Request,
    id: int,
    current: User = Depends(get_current_user),
    acting: User = Depends(get_acting_user),
):
    """Confirm or correct a flagged bill against its PDF (the vision pass is
    the only source of a bill's values, so there's nothing to choose between)."""
    conn = get_db()
    try:
        row = conn.execute(
            "SELECT * FROM utility_bills WHERE id = ? AND user_id = ? AND deleted_at IS NULL "
            "AND status = 'pending_review'",
            (id, acting.id),
        ).fetchone()
        meter = conn.execute(
            "SELECT * FROM electric_meter_details WHERE utility_bill_id = ?", (id,)
        ).fetchone() if row else None
    finally:
        conn.close()

    if row is None:
        return templates.TemplateResponse(request, "not_found.html", {"user": acting}, status_code=404)

    return templates.TemplateResponse(
        request, "utility_review.html",
        {
            "user": acting, "bill": row, "meter": meter, "meter_columns": _METER_COLUMNS,
            "known_provider": row["provider"] in PROVIDERS, "can_reextract": bool(storage.stored_path(row["pdf_path"])),
            "max_notes": MAX_NOTES_CHARS,
            "acting_as_banner": _acting_banner(current, acting),
            "acting_qs": _acting_qs(current, acting),
            "active_page": "utilities", "tab": "upload",
        },
    )


@router.post("/utility-reextract")
def utility_reextract(
    request: Request,
    id: int = Form(...),
    notes: str = Form(""),
    current: User = Depends(get_current_user),
    acting: User = Depends(get_acting_user),
):
    """Read a not-yet-confirmed bill's PDF again, optionally with notes on what was wrong
    (section 23). The whole upload is re-run from its first row, so the other bills from the
    same PDF (Xcel's gas with its electric) are read again too; the notes are kept on that row
    and sent to the model with the PDF (empty notes clear them)."""
    with get_db() as conn:
        row = conn.execute(
            "SELECT id, file_hash, pdf_path, status FROM utility_bills WHERE id = ? AND user_id = ? AND deleted_at IS NULL",
            (id, acting.id)).fetchone()
        if row is None:
            return templates.TemplateResponse(request, "not_found.html", {"user": acting}, status_code=404)
        if row["status"] != "pending_review":
            return JSONResponse({"error": "Only a bill that isn't confirmed yet can be re-extracted"}, status_code=400)
        if not storage.stored_path(row["pdf_path"]):
            return JSONResponse({"error": "Source PDF no longer exists — re-upload instead"}, status_code=400)
        first = conn.execute(
            "SELECT MIN(id) FROM utility_bills WHERE user_id = ? AND file_hash = ? AND deleted_at IS NULL",
            (acting.id, row["file_hash"])).fetchone()[0] or id
        conn.execute(
            "UPDATE utility_bills SET status = 'processing', error_message = NULL, current_step = NULL, "
            "extraction_notes = ? WHERE id = ?", ((notes or "").strip()[:MAX_NOTES_CHARS] or None, first))
        conn.commit()
    jobs.start_job("utility_bill", first, acting.id)
    qs = _acting_qs(current, acting)
    return RedirectResponse(url="utilities?tab=upload" + (f"&{qs}" if qs else ""), status_code=303)


@router.post("/utility-resolve")
async def utility_resolve(
    request: Request,
    id: int = Form(...),
    period_start_date: str = Form(...),
    period_end_date: str = Form(...),
    usage_amount: str = Form(""),
    usage_unit: str = Form(""),
    cost: str = Form(...),
    due_date: str = Form(""),
    account_number_last4: str = Form(""),
    current: User = Depends(get_current_user),
    acting: User = Depends(get_acting_user),
):
    """The value shown on /utility-review — possibly edited by the user
    if they caught something the amount check didn't pinpoint, possibly
    unchanged if they just verified it against the source PDF and it was
    right — becomes authoritative. Clears review_status to 'clean',
    status to 'complete', and (mirroring how a statement's PDF is only
    deleted once nothing on it still needs review) deletes the source
    PDF now that resolution is what was keeping it around.

    The 16 electric_meter_details fields aren't declared as individual
    Form(...) parameters (unlike the fields above) — they only exist for
    an electric bill, and reading the raw form data directly keeps this
    signature from ballooning to 24 parameters most calls don't use. Any
    field left blank on the form is stored as NULL, same as an
    unrecognized one during original extraction."""
    try:
        cost_value = round(float(cost), 2)
    except ValueError:
        return JSONResponse({"error": "Cost must be a number"}, status_code=400)
    usage_value = None
    if usage_amount.strip():
        try:
            usage_value = float(usage_amount)
        except ValueError:
            return JSONResponse({"error": "Usage must be a number"}, status_code=400)
    if not period_start_date.strip() or not period_end_date.strip():
        return JSONResponse({"error": "Period start/end dates are required"}, status_code=400)

    form = await request.form()
    meter_values: dict[str, float | None] = {}
    for col in _METER_COLUMNS:
        raw = (form.get(f"meter_{col}") or "").strip()
        if not raw:
            meter_values[col] = None
            continue
        try:
            meter_values[col] = float(raw)
        except ValueError:
            return JSONResponse({"error": f"{col.replace('_', ' ')} must be a number"}, status_code=400)

    conn = get_db()
    try:
        row = conn.execute(
            "SELECT id, pdf_path, utility_type FROM utility_bills WHERE id = ? AND user_id = ? "
            "AND deleted_at IS NULL AND status = 'pending_review'",
            (id, acting.id),
        ).fetchone()
        if row is None:
            return templates.TemplateResponse(request, "not_found.html", {"user": acting}, status_code=404)

        conn.execute(
            "UPDATE utility_bills SET period_start_date = ?, period_end_date = ?, usage_amount = ?, "
            "usage_unit = ?, cost = ?, due_date = ?, account_number_last4 = ?, "
            "status = 'complete', review_status = 'clean', error_message = NULL WHERE id = ?",
            (
                period_start_date.strip(), period_end_date.strip(), usage_value,
                (usage_unit.strip() or None), cost_value, (due_date.strip() or None),
                (account_number_last4.strip() or None), id,
            ),
        )

        if row["utility_type"] == "electric":
            existing_meter = conn.execute(
                "SELECT id FROM electric_meter_details WHERE utility_bill_id = ?", (id,)
            ).fetchone()
            set_clause = ", ".join(f"{col} = ?" for col in _METER_COLUMNS)
            values = [meter_values[col] for col in _METER_COLUMNS]
            if existing_meter is not None:
                conn.execute(
                    f"UPDATE electric_meter_details SET {set_clause} WHERE utility_bill_id = ?",
                    [*values, id],
                )
            else:
                columns = ", ".join(_METER_COLUMNS)
                placeholders = ", ".join("?" for _ in _METER_COLUMNS)
                conn.execute(
                    f"INSERT INTO electric_meter_details (utility_bill_id, {columns}) "
                    f"VALUES (?, {placeholders})",
                    [id, *values],
                )

        conn.commit()
        # A dual-fuel statement's bills share one PDF: delete it only once no
        # bill using it still needs review.
        siblings = conn.execute(
            "SELECT id, status FROM utility_bills WHERE pdf_path = ? AND deleted_at IS NULL", (row["pdf_path"],),
        ).fetchall() if row["pdf_path"] else []
        if all(s["status"] == "complete" for s in siblings):
            delete_pdf_utility(conn, [s["id"] for s in siblings] or [id], row["pdf_path"])
    finally:
        conn.close()

    qs = _acting_qs(current, acting)
    return RedirectResponse(url="utilities?tab=upload" + (f"&{qs}" if qs else ""), status_code=303)


def _month_title(period_start: str) -> str:
    try:
        return datetime.strptime(period_start[:7], "%Y-%m").strftime("%B %Y")
    except ValueError:
        return period_start


def _usage_cost_charts(utility_type: str, rows: list) -> dict | None:
    """Cost and usage column charts for one utility type (gas / water on the Compare tab).

    Two charts rather than one with two axes: a bill's dollars and its therms or gallons
    are different measures. Usage only plots bills in the newest bill's unit, so a
    provider switching units never draws two scales into one chart."""
    if not rows:
        return None

    def tip(b, measure: str) -> str:
        lines = [_month_title(b["period_start_date"]), measure]
        if b["period_end_date"]:
            lines.append(f"{b['period_start_date']} to {b['period_end_date']}")
        if b["status"] == "pending_review":
            lines.append("(not yet confirmed)")
        return "\n".join(lines)

    cost_points = [
        {"label": b["period_start_date"][:7], "value": b["cost"], "tip": tip(b, f"Cost ${b['cost']:,.2f}")}
        for b in rows if b["cost"] is not None
    ]
    unit = next((b["usage_unit"] for b in reversed(rows) if b["usage_amount"] is not None), None)
    usage_points = [
        {"label": b["period_start_date"][:7], "value": b["usage_amount"],
         "tip": tip(b, f"Usage {b['usage_amount']:,g} {b['usage_unit'] or ''}".rstrip())}
        for b in rows if b["usage_amount"] is not None and b["usage_unit"] == unit
    ]
    name = utility_type.capitalize()
    return {
        "unit": unit,
        "cost": column_chart(cost_points, prefix="$", css_class="chart-cost", label=f"{name} cost per billing month"),
        "usage": column_chart(usage_points, css_class="chart-usage",
                              label=f"{name} usage per billing month" + (f" in {unit}" if unit else "")),
    }



@router.get("/utility-comparison")
def utility_comparison(
    request: Request,
    month_a: str = "",
    month_b: str = "",
    current: User = Depends(get_current_user),
    acting: User = Depends(get_acting_user),
):
    """Compare tab: per-type trends across billing periods (types are never
    compared with each other) and, with month_a/month_b ("YYYY-MM"), two
    months side by side — both built from the same query, so they can't
    disagree. Bills awaiting review are included (their status is shown);
    processing and failed bills are not. A month without a bill of a type
    shows "No bill" rather than a zero."""
    # Every meter column, so the Dashboard's triplet()/rate_pair() macros work here.
    meter_cols = ", ".join(f"m.{c}" for c in _METER_COLUMNS)
    conn = get_db()
    try:
        bills = conn.execute(
            f"SELECT b.*, {meter_cols} "
            "FROM utility_bills b LEFT JOIN electric_meter_details m ON m.utility_bill_id = b.id "
            "WHERE b.user_id = ? AND b.deleted_at IS NULL AND b.status IN ('complete', 'pending_review') "
            "AND b.utility_type IS NOT NULL AND b.period_start_date IS NOT NULL "
            "ORDER BY b.utility_type, b.period_start_date",
            (acting.id,),
        ).fetchall()
    finally:
        conn.close()

    by_type: dict[str, list] = {"electric": [], "gas": [], "water": []}
    for b in bills:
        by_type.setdefault(b["utility_type"], []).append(b)

    # Bar width as a percentage of that utility type's own highest cost —
    # each type gets its own scale (a water bill's ~$140 and an
    # electric bill's ~$120 would otherwise both look tiny next to a much
    # larger one if all three types shared one scale, when they aren't
    # meaningfully comparable to each other in the first place).
    max_cost = {t: max([r["cost"] or 0 for r in rows], default=0) for t, rows in by_type.items()}

    charts = {t: _usage_cost_charts(t, by_type.get(t, [])) for t in ("gas", "water")}

    compare = None
    ma, mb = month_a.strip(), month_b.strip()
    if ma and mb:
        compare = {}
        for t, rows in by_type.items():
            # A billing period's own start date matched by month prefix
            # ("2026-01-04" matches "2026-01") -- a billing cycle rarely
            # starts exactly on the 1st, so this is a "which month did
            # this bill's period START in" match, not an exact-date one.
            # If more than one bill of the same type somehow starts in
            # the same month, the most recently-processed one wins (last
            # one in period_start_date order, which is how `rows` is
            # already sorted) -- picking one deterministically beats
            # silently combining two bills' numbers together.
            bill_a = next((r for r in reversed(rows) if (r["period_start_date"] or "").startswith(ma)), None)
            bill_b = next((r for r in reversed(rows) if (r["period_start_date"] or "").startswith(mb)), None)
            if bill_a is not None or bill_b is not None:
                compare[t] = {"a": bill_a, "b": bill_b}

    return templates.TemplateResponse(
        request, "utility_comparison.html",
        {
            "user": acting, "by_type": by_type, "max_cost": max_cost, "charts": charts,
            "month_a": month_a, "month_b": month_b, "compare": compare,
            "acting_as_banner": _acting_banner(current, acting),
            "acting_qs": _acting_qs(current, acting),
            "is_admin": current.is_admin,  # current, NOT acting -- same reasoning as the Dashboard tab
            "active_page": "utilities",
        },
    )

