"""E-470 toll statement documents (SPEC.md section 18): upload, status polling, restart, delete/restore, review of passes the two readings disagree on, and the admin debug view. Dashboard/Compare/Analyze and cars/tags live in routes/tolls.py."""
import asyncio
import json
from pathlib import Path

from fastapi import APIRouter, Depends, File, Form, Request, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.templating import Jinja2Templates

from .. import features as _features

from ..auth import User, get_current_user, get_acting_user, require_admin
from ..db import get_db
from ..parser.deterministic import run_pdftotext
from ..parser.toll_deterministic import extraction_from_dict, clean_amount, normalize_direction
from ..parser.toll_pipeline import (
    check_duplicate_toll, confirm_review, default_rows, diff_extractions, explain_differences,
)
from ..parser.toll_vision import debug_call_toll
from ..parser.documents import prepare_restart
from ..parser.vision import VisionExtractionError
from ..toll_trips import (
    clock_value, format_minutes, pretty_signature, rebuild_trips,
)
from ..tolls import vehicle_label
from ..version import APP_VERSION
from .tolls import _int_or_none, _redirect
from .accounts import _acting_banner, _acting_qs
from .. import ai_usage, jobs, settings, storage

router = APIRouter(dependencies=[Depends(_features.required("tolls"))])
templates = Jinja2Templates(directory=str(Path(__file__).parent.parent / "templates"))
templates.env.globals["app_version"] = APP_VERSION
templates.env.globals["ai_usage_view"] = ai_usage.view
templates.env.globals["vehicle_label"] = vehicle_label
templates.env.globals["format_minutes"] = format_minutes
templates.env.globals["clock_value"] = clock_value
templates.env.globals["pretty_signature"] = pretty_signature


@router.post("/toll-upload")
async def upload_toll_statement(
    request: Request,
    files: list[UploadFile] = File(...),
    current: User = Depends(get_current_user),
    acting: User = Depends(get_acting_user),
):
    """Several PDFs per request; one placeholder row each, then the shared worker parses them one at a time."""
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
                storage.save_file, storage.user_dir(acting.id, "toll-statements"), "toll", ".pdf", content)

            dup = check_duplicate_toll(conn, acting.id, hash_)
            if dup is not None:
                storage.remove_stored_file(tmp_path)
                message = ("Already uploaded and failed to process — restart it instead of uploading again."
                           if dup["status"] == "error" else "Already uploaded.")
                results.append({"filename": file.filename, "duplicate_of": dup["id"], "message": message})
                continue

            cur = conn.execute(
                "INSERT INTO toll_statements (user_id, original_filename, pdf_path, file_hash, status) "
                "VALUES (?, ?, ?, ?, 'processing')", (acting.id, file.filename, tmp_path, hash_))
            conn.commit()
            jobs.start_job("toll", cur.lastrowid, acting.id)
            results.append({"filename": file.filename, "toll_statement_id": cur.lastrowid, "status": "processing"})
    finally:
        conn.close()
    return JSONResponse({"results": results})


def _status_context(request: Request, row, current: User, acting: User) -> dict:
    return {"row": row, "acting_qs": _acting_qs(current, acting), "is_admin": current.is_admin}


@router.get("/toll-status")
def toll_status(
    request: Request,
    id: int,
    current: User = Depends(get_current_user),
    acting: User = Depends(get_acting_user),
):
    """Polled by htmx while a statement is processing. Anything else (done, needs review, failed,
    deleted meanwhile) answers HX-Refresh: the period, cars and totals only fill in once it finishes."""
    conn = get_db()
    try:
        row = conn.execute(
            "SELECT id, status, error_message, original_filename, current_step, pdf_path FROM toll_statements "
            "WHERE id = ? AND user_id = ? AND deleted_at IS NULL", (id, acting.id)).fetchone()
    finally:
        conn.close()
    if row is None or row["status"] != "processing":
        return HTMLResponse("", headers={"HX-Refresh": "true"})
    return templates.TemplateResponse(request, "_toll_status_fragment.html", _status_context(request, row, current, acting))


@router.post("/toll-restart")
def toll_restart(
    request: Request,
    id: int,
    current: User = Depends(get_current_user),
    acting: User = Depends(get_acting_user),
):
    conn = get_db()
    try:
        row, problem = prepare_restart("toll_statements", conn, id, acting.id)
        if problem:
            return JSONResponse({"error": problem}, status_code=404 if row is None else 400)
        updated = conn.execute(
            "SELECT id, status, error_message, original_filename, current_step, pdf_path FROM toll_statements WHERE id = ?",
            (id,)).fetchone()
    finally:
        conn.close()
    jobs.start_job("toll", id, acting.id)
    return templates.TemplateResponse(request, "_toll_status_fragment.html", _status_context(request, updated, current, acting))


@router.post("/toll-delete")
def toll_delete(
    request: Request,
    id: int = Form(...),
    current: User = Depends(get_current_user),
    acting: User = Depends(get_acting_user),
):
    """Soft delete (30 days, then the purge): the statement and its passes go together and come back
    together from Recently deleted. Trips are rebuilt without them."""
    conn = get_db()
    try:
        row = conn.execute("SELECT id FROM toll_statements WHERE id = ? AND user_id = ? AND deleted_at IS NULL",
                           (id, acting.id)).fetchone()
        if row is not None:
            stamp = conn.execute("SELECT datetime('now') AS now").fetchone()["now"]
            conn.execute("UPDATE toll_statements SET deleted_at = ? WHERE id = ?", (stamp, id))
            conn.execute("UPDATE toll_transactions SET deleted_at = ? WHERE statement_id = ? AND deleted_at IS NULL", (stamp, id))
            conn.commit()
            rebuild_trips(conn, acting.id)
    finally:
        conn.close()
    return _redirect("tolls", current, acting, tab="upload")


@router.post("/toll-restore")
def toll_restore(
    request: Request,
    id: int = Form(...),
    current: User = Depends(get_current_user),
    acting: User = Depends(get_acting_user),
    _admin: User = Depends(require_admin),  # Recently deleted is admin only (SPEC.md section 11)
):
    """Brings back the statement and the passes deleted with it. A pass whose twin was imported from another
    statement in the meantime stays out (it would be counted twice)."""
    conn = get_db()
    try:
        row = conn.execute("SELECT deleted_at FROM toll_statements WHERE id = ? AND user_id = ? AND deleted_at IS NOT NULL",
                           (id, acting.id)).fetchone()
        if row is not None:
            stamp = row["deleted_at"]
            conn.execute("UPDATE toll_statements SET deleted_at = NULL WHERE id = ?", (id,))
            conn.execute(
                "UPDATE toll_transactions SET deleted_at = NULL WHERE statement_id = ? AND deleted_at = ? "
                "AND dedupe_key NOT IN (SELECT dedupe_key FROM toll_transactions WHERE user_id = ? AND deleted_at IS NULL "
                "AND statement_id != ?)", (id, stamp, acting.id, id))
            conn.execute("DELETE FROM toll_transactions WHERE statement_id = ? AND deleted_at = ?", (id, stamp))
            conn.commit()
            rebuild_trips(conn, acting.id)
    finally:
        conn.close()
    return _redirect("recently-deleted", current, acting)


def _car_value(key: tuple) -> str:
    return "|".join(key)


def _load_extractions(row):
    det = extraction_from_dict(json.loads(row["deterministic_value"])) if row["deterministic_value"] else None
    ai = extraction_from_dict(json.loads(row["llm_value"])) if row["llm_value"] else None
    return det, ai


def _review_context(conn, row, current: User, acting: User, error: str | None = None) -> dict:
    det, ai = _load_extractions(row)
    diff = diff_extractions(det, ai) if det and ai else {"agree": [], "differ": []}
    defaults = default_rows(diff)
    car_keys = []
    for ex in (det, ai):
        for car in (ex.cars if ex else []):
            if car.key not in car_keys:
                car_keys.append(car.key)
    checks = json.loads(row["checks_json"]) if row["checks_json"] else []
    pdf_text = None
    if det and storage.stored_path(row["pdf_path"]) and any(not (d["det"] and d["ai"]) for d in diff["differ"]):
        try:
            pdf_text = run_pdftotext(row["pdf_path"])
        except Exception:
            pdf_text = None
    notes = explain_differences(diff, det, pdf_text, _key_label) if det else {}
    printed = row["total_tolls_printed"]
    default_sum = round(sum(r["amount"] for r in defaults), 2)
    return {
        "user": acting, "statement": row, "diff": diff, "checks": checks, "printed": printed, "default_sum": default_sum,
        "difference": round(printed - default_sum, 2) if printed is not None else None,
        "car_options": [(_car_value(k), _key_label(k)) for k in car_keys],
        "car_names": {k: _key_label(k) for k in car_keys}, "notes": notes,
        "error": error, "acting_qs": _acting_qs(current, acting), "acting_as_banner": _acting_banner(current, acting),
        "is_admin": current.is_admin, "active_page": "tolls", "tab": "upload",
    }


def _key_label(key: tuple) -> str:
    """'PLATE-ST · device 1234567' for a (device, plate, state) key read from a statement."""
    device, plate, state = key
    return " · ".join(p for p in (f"{plate}-{state}" if state else plate, f"device {device}" if device else "") if p) or "unknown device"


@router.get("/toll-review")
def toll_review(
    request: Request,
    id: int,
    current: User = Depends(get_current_user),
    acting: User = Depends(get_acting_user),
):
    with get_db() as conn:
        row = conn.execute("SELECT * FROM toll_statements WHERE id = ? AND user_id = ? AND deleted_at IS NULL "
                           "AND status = 'pending_review'", (id, acting.id)).fetchone()
        if row is None:
            return templates.TemplateResponse(request, "not_found.html", {"user": acting}, status_code=404)
        context = _review_context(conn, row, current, acting)
    return templates.TemplateResponse(request, "toll_review.html", context)


def _form_datetime(text: str) -> str | None:
    text = (text or "").strip().replace("T", " ")
    parts = text.split(" ")
    if len(parts) != 2:
        return None
    clock = parts[1]
    if clock.count(":") == 1:
        clock += ":00"
    from datetime import datetime
    try:
        return datetime.strptime(f"{parts[0]} {clock}", "%Y-%m-%d %H:%M:%S").strftime("%Y-%m-%d %H:%M:%S")
    except ValueError:
        return None


@router.post("/toll-resolve")
async def toll_resolve(
    request: Request,
    current: User = Depends(get_current_user),
    acting: User = Depends(get_acting_user),
):
    """Confirm a review: the agreed passes, plus one choice per difference (the deterministic reading,
    the AI's, an edited version, or leave it out), plus any passes typed in. The printed Grand Totals can
    be corrected too. A remaining mismatch is recorded as acknowledged."""
    form = await request.form()
    statement_id = _int_or_none(form.get("id"))
    conn = get_db()
    try:
        row = conn.execute("SELECT * FROM toll_statements WHERE id = ? AND user_id = ? AND deleted_at IS NULL "
                           "AND status = 'pending_review'", (statement_id, acting.id)).fetchone() if statement_id else None
        if row is None:
            return templates.TemplateResponse(request, "not_found.html", {"user": acting}, status_code=404)

        det, ai = _load_extractions(row)
        diff = diff_extractions(det, ai) if det and ai else {"agree": [], "differ": []}
        rows = list(diff["agree"])
        errors: list[str] = []

        for entry in diff["differ"]:
            i = entry["idx"]
            choice = form.get(f"choice_{i}") or ("det" if entry["det"] else "ai")
            if choice == "skip":
                continue
            if choice in ("det", "ai"):
                picked = entry[choice]
                if picked is None:                       # only the other reading has this pass
                    picked = entry["det"] or entry["ai"]
                rows.append(picked)
            elif choice == "edit":
                base = entry["det"] or entry["ai"]
                when = _form_datetime(form.get(f"edit_datetime_{i}", ""))
                amount = clean_amount(form.get(f"edit_amount_{i}", ""))
                if when is None or amount is None or amount <= 0:
                    errors.append(f"Row {i + 1}: enter a valid date and time and a positive amount.")
                    continue
                rows.append({**base, "datetime": when, "plaza": (form.get(f"edit_plaza_{i}") or base["plaza"]).strip(),
                             "lane": (form.get(f"edit_lane_{i}") or base["lane"]).strip(),
                             "direction": normalize_direction(form.get(f"edit_direction_{i}") or base["direction"]),
                             "amount": round(amount, 2)})

        for j in range(3):                                # passes typed in
            amount_text = (form.get(f"add_amount_{j}") or "").strip()
            if not amount_text:
                continue
            when = _form_datetime(form.get(f"add_datetime_{j}", ""))
            amount = clean_amount(amount_text)
            car_text = form.get(f"add_car_{j}") or ""
            car = tuple((car_text.split("|") + ["", "", ""])[:3])
            if when is None or amount is None or amount <= 0 or not car_text:
                errors.append(f"Added pass {j + 1}: choose a car and enter a valid date and time and a positive amount.")
                continue
            rows.append({"car": car, "mk": car[0] or car[1], "datetime": when, "agency": (form.get(f"add_agency_{j}") or "CO").strip().upper(),
                         "road": (form.get(f"add_road_{j}") or "E470").strip().upper(), "plaza": (form.get(f"add_plaza_{j}") or "").strip(),
                         "lane": (form.get(f"add_lane_{j}") or "").strip(), "direction": normalize_direction(form.get(f"add_direction_{j}") or ""),
                         "amount": round(amount, 2), "raw_line": ""})

        printed_text = (form.get("printed_total") or "").strip()
        printed = row["total_tolls_printed"]
        if printed_text:
            value = clean_amount(printed_text)
            if value is None:
                errors.append("The printed Grand Totals must be a number.")
            else:
                printed = round(abs(value), 2)

        if errors:
            return templates.TemplateResponse(request, "toll_review.html", _review_context(conn, row, current, acting, " ".join(errors)),
                                              status_code=400)
        confirm_review(conn, statement_id, acting.id, rows, printed)
    finally:
        conn.close()
    return _redirect("tolls", current, acting, tab="upload")


def _debug_context(conn, row, admin: User, **extra) -> dict:
    det, ai = _load_extractions(row)
    context = {
        "user": admin, "statement": row, "checks": json.loads(row["checks_json"]) if row["checks_json"] else [],
        "unparsed": json.loads(row["unparsed_json"]) if row["unparsed_json"] else [],
        "timings": json.loads(row["timings_json"]) if row["timings_json"] else {},
        "diff": diff_extractions(det, ai) if det and ai else None,
        "det": det, "ai": ai, "ai_rejected": (json.loads(row["llm_value"]).get("rejected") or []) if row["llm_value"] else [],
        "text_excerpt": None, "active_page": "tolls", "tab": "upload", "acting_qs": "", "acting_as_banner": None,
    }
    if storage.stored_path(row["pdf_path"]):
        try:
            context["text_excerpt"] = run_pdftotext(row["pdf_path"])[:20000]
        except Exception as exc:  # shown on the page, not a 500
            context["text_excerpt"] = f"(could not extract text: {exc})"
    context.update(extra)
    return context


@router.get("/toll-debug")
def toll_debug(request: Request, id: int, admin: User = Depends(require_admin)):
    with get_db() as conn:
        row = conn.execute("SELECT * FROM toll_statements WHERE id = ?", (id,)).fetchone()
        if row is None:
            return templates.TemplateResponse(request, "not_found.html", {"user": admin}, status_code=404)
        context = _debug_context(conn, row, admin)
    return templates.TemplateResponse(request, "toll_debug.html", context)


@router.post("/toll-debug-resend")
def toll_debug_resend(request: Request, id: int, admin: User = Depends(require_admin)):
    """Re-send the PDF to Ollama with no timeout and no retry, showing the full raw response."""
    with get_db() as conn:
        row = conn.execute("SELECT * FROM toll_statements WHERE id = ?", (id,)).fetchone()
        if row is None:
            return templates.TemplateResponse(request, "not_found.html", {"user": admin}, status_code=404)
        if not storage.stored_path(row["pdf_path"]):
            return templates.TemplateResponse(request, "toll_debug.html", _debug_context(
                conn, row, admin, resend_error="Source PDF no longer exists (already resolved and deleted) — can't resend."))
    try:
        (elapsed, raw), resend_usage = jobs.exclusive_recorded(debug_call_toll, row["pdf_path"],
                                                               settings.ai_url(), settings.ai_model())
    except VisionExtractionError as e:
        with get_db() as conn:
            return templates.TemplateResponse(request, "toll_debug.html", _debug_context(conn, row, admin, resend_error=str(e)))
    with get_db() as conn:
        conn.execute("UPDATE toll_statements SET llm_raw_response = ? WHERE id = ?", (raw, id))
        conn.commit()
        row = conn.execute("SELECT * FROM toll_statements WHERE id = ?", (id,)).fetchone()
        return templates.TemplateResponse(request, "toll_debug.html", _debug_context(conn, row, admin, resend_elapsed=elapsed,
                                                                                    resend_usage=resend_usage))
