"""Orchestrates one E-470 toll statement's processing end to end (SPEC.md section 18.4) —
sibling to utility_pipeline.process_utility_bill, queued on the same single FIFO worker.

Two independent readings of the PDF are compared:

1. deterministic (toll_deterministic.py): `pdftotext -layout`, the summary block, car headings and pass rows;
2. AI (toll_vision.py): the same Ollama vision setup as utilities.

A statement is `complete` only when every check passes; otherwise it is `pending_review`, with each
failed check listed with its numbers. The checks:

* passes agree      — same passes (car, moment, amount) and the same cars in both readings;
* no dropped lines  — no line that started like a pass failed to parse;
* Grand Totals      — the sum of all parsed passes equals the printed Grand Totals (absolute value,
                       compared to the cent). Grand Totals is the only summary figure used: Total Tolls,
                       Previous Balance, Adjustments and Payments are never read. Skipped when nothing
                       is printed;
* Grand Totals read the same — both readings found the same printed figure;
* per-car subtotals — only when the statement prints a per-device total.

Tags (the statement's Device # and plate) are added automatically the first time they are seen, even
while the statement awaits review; a changed plate updates the device. The person creates the cars and
assigns devices to them (routes/tolls.py); until then the passes show under the bare device. Passes already imported from another statement (overlapping periods, a
re-download) are skipped and counted; if every pass is a duplicate the upload fails as "Duplicate".
Stored passes of a statement awaiting review are marked `pending_review` and are left out of every
total, chart and trip until the review is confirmed.
"""
import json
import logging
import re
import sqlite3
from functools import partial
import time
from collections import defaultdict

from ..db import get_db
from ..storage import forget_pdf
from . import documents
from ..toll_trips import rebuild_trips
from ..tolls import dedupe_key, notes_text, resolve_devices
from .toll_deterministic import NoTextLayer, TollExtraction, extract_toll_deterministic
from .toll_vision import TollVisionResult, extract_vision_toll
from .vision import VisionExtractionError

logger = logging.getLogger(__name__)

TOLERANCE = 0.005


# ------------------------------------------------------------------ small DB helpers

check_duplicate_toll = partial(documents.find_duplicate, "toll_statements")
_set_step = partial(documents.set_step, "toll_statements")
_fail = partial(documents.fail, "toll_statements")
delete_pdf_toll = partial(forget_pdf, "toll_statements")


# ------------------------------------------------------------------ comparing the two readings

def _rows(extraction: TollExtraction) -> list[dict]:
    return [{"car": car.key, "mk": car.match_key, "datetime": p.occurred_at, "agency": p.agency, "road": p.road,
             "plaza": p.plaza, "lane": p.lane, "direction": p.direction, "amount": p.amount, "raw_line": p.raw_line}
            for car, p in extraction.all_passes()]


def diff_extractions(det: TollExtraction, ai: TollExtraction) -> dict:
    """{"agree": [row], "differ": [{"idx", "det": row|None, "ai": row|None}]}.

    Passes are matched on (car, moment, amount). Leftovers with the same car and moment but a
    different amount are paired (an amount read differently); the rest exist in one reading only.
    Deterministic for a given pair of extractions, so the review form's row indexes stay valid."""
    det_rows, ai_rows = _rows(det), _rows(ai)
    pool: dict[tuple, list[int]] = defaultdict(list)
    for i, r in enumerate(ai_rows):
        pool[(r["mk"], r["datetime"], r["amount"])].append(i)

    used: set[int] = set()
    agree, det_left = [], []
    for r in det_rows:
        matches = pool.get((r["mk"], r["datetime"], r["amount"]))
        if matches:
            used.add(matches.pop(0))
            agree.append(r)
        else:
            det_left.append(r)

    ai_left: dict[tuple, list[dict]] = defaultdict(list)
    for i, r in enumerate(ai_rows):
        if i not in used:
            ai_left[(r["mk"], r["datetime"])].append(r)

    differ = []
    for r in det_left:
        partners = ai_left.get((r["mk"], r["datetime"]))
        differ.append({"det": r, "ai": partners.pop(0) if partners else None})
    for partners in ai_left.values():
        differ.extend({"det": None, "ai": r} for r in partners)

    differ.sort(key=lambda d: ((d["det"] or d["ai"])["datetime"], (d["det"] or d["ai"])["mk"]))
    for idx, entry in enumerate(differ):
        entry["idx"] = idx
    return {"agree": agree, "differ": differ}


def _printed_needle(iso_datetime: str) -> re.Pattern | None:
    """A regex for the line of the PDF text that prints this moment ('9/9/2026 7:21:07 AM ...')."""
    m = re.match(r"^(\d{4})-(\d{2})-(\d{2}) (\d{2}):(\d{2}):(\d{2})$", iso_datetime or "")
    if not m:
        return None
    y, mo, d, h, mi, se = m.groups()
    hour12 = int(h) % 12 or 12
    return re.compile(rf"^{int(mo)}/{int(d)}/{y}\s+0?{hour12}:{mi}(?::{se})?\s*{'AM' if int(h) < 12 else 'PM'}\b", re.IGNORECASE)


def explain_differences(diff: dict, det: TollExtraction, pdf_text: str | None, label) -> dict[int, str]:
    """{difference idx: a plain sentence saying WHY a pass is missing from one reading}, for the review page.

    `label(car key)` names a car. A pass that only one reading has is either (a) the same pass, at the same
    moment and amount, that the other reading put under a different device, (b) a line the text reader saw
    in the PDF but could not read, or (c) not in the PDF's text at all."""
    notes: dict[int, str] = {}
    differ = diff["differ"]
    lines = [re.sub(r"\s+", " ", raw).strip() for raw in (pdf_text or "").splitlines()]
    for e in differ:
        det_row, ai_row = e["det"], e["ai"]
        if det_row and ai_row:
            continue
        mine, side = (det_row, "text") if det_row else (ai_row, "AI")
        other_side = "AI" if det_row else "text"
        twin = next((o for o in differ if o is not e and (o["ai"] if det_row else o["det"]) and not (o["det"] if det_row else o["ai"])
                     and (o["ai"] if det_row else o["det"])["datetime"] == mine["datetime"]
                     and (o["ai"] if det_row else o["det"])["amount"] == mine["amount"]), None)
        if twin:
            theirs = twin["ai"] if det_row else twin["det"]
            notes[e["idx"]] = (f"The {other_side} reading has this same pass under {label(theirs['car'])}, the {side} reading under "
                               f"{label(mine['car'])}: they disagree about which device it belongs to. Keep the one that is right.")
            continue
        if det_row:
            notes[e["idx"]] = "The AI reading did not return this pass."
            continue
        needle = _printed_needle(ai_row["datetime"])
        found = next((ln for ln in lines if needle and needle.match(ln)), None)
        if found is None:
            notes[e["idx"]] = ("This date and time do not appear in the PDF's text at all" if pdf_text
                               else "The PDF's text is no longer available to check this against") + (
                               ", so the AI may have misread or invented it." if pdf_text else ".")
            continue
        unread = next((u for u in det.unparsed if u.startswith(found[:40]) or found.startswith(u[:40])), None)
        if unread:
            notes[e["idx"]] = f"The PDF text has this line but the text reader could not read it: {unread}"
        else:
            notes[e["idx"]] = f"The PDF text has this line ({found}) but the text reader did not keep it."
    return notes


def default_rows(diff: dict) -> list[dict]:
    """What is stored before anyone reviews: the agreed passes, plus for every difference the
    deterministic reading when there is one, else the AI's."""
    return list(diff["agree"]) + [d["det"] or d["ai"] for d in diff["differ"]]


def _car_line(key: tuple) -> str:
    device, plate, state = key
    return " · ".join(p for p in (f"{plate}-{state}" if state else plate, f"device {device}" if device else "") if p) or "unknown device"


def run_checks(det: TollExtraction, ai: TollExtraction, rejected: list[str], diff: dict, rows: list[dict]) -> list[dict]:
    """The verification table (18.4). Each check: key, name, ok, skipped, detail."""
    checks: list[dict] = []

    def add(key, name, ok, detail, skipped=False):
        checks.append({"key": key, "name": name, "ok": bool(ok), "skipped": skipped, "detail": detail})

    # 1. Passes agree
    # A car is its device; the plate is only a label, so a plate read differently does not make the readings disagree.
    det_cars, ai_cars = {c.match_key: c.key for c in det.cars}, {c.match_key: c.key for c in ai.cars}
    problems = []
    only_det = sum(1 for d in diff["differ"] if d["ai"] is None)
    only_ai = sum(1 for d in diff["differ"] if d["det"] is None)
    amount_diff = sum(1 for d in diff["differ"] if d["det"] and d["ai"])
    if only_det:
        problems.append(f"{only_det} pass{'es' if only_det != 1 else ''} found only by the deterministic reading")
    if only_ai:
        problems.append(f"{only_ai} pass{'es' if only_ai != 1 else ''} found only by the AI reading")
    if amount_diff:
        problems.append(f"{amount_diff} with a different amount in the two readings")
    if rejected:
        problems.append(f"{len(rejected)} AI row{'s' if len(rejected) != 1 else ''} that could not be read")
    if set(det_cars) != set(ai_cars):
        problems.append("the cars differ (deterministic: " + (", ".join(sorted(_car_line(k) for k in det_cars.values())) or "none")
                        + "; AI: " + (", ".join(sorted(_car_line(k) for k in ai_cars.values())) or "none") + ")")
    add("passes_agree", "Passes agree", not problems,
        f"Both readings found the same {len(diff['agree'])} passes." if not problems else "The two readings disagree: " + "; ".join(problems) + ".")

    # 2. No dropped lines
    if det.unparsed:
        sample = "; ".join(det.unparsed[:3]) + (" …" if len(det.unparsed) > 3 else "")
        add("no_dropped_lines", "No dropped lines", False,
            f"{len(det.unparsed)} line{'s' if len(det.unparsed) != 1 else ''} started like a toll pass but could not be read: {sample}.")
    else:
        add("no_dropped_lines", "No dropped lines", True, "Every line that looks like a toll pass was read.")

    # 3. Grand Totals
    printed = det.grand_total if det.grand_total is not None else ai.grand_total
    passes_sum = round(sum(r["amount"] for r in rows), 2)
    if printed is None:
        add("grand_total", "Grand Totals", True, "No printed total to check against.", skipped=True)
    else:
        difference = round(printed - passes_sum, 2)
        if abs(difference) < TOLERANCE:
            add("grand_total", "Grand Totals", True, f"Passes add up to {passes_sum:.2f}, matching the statement's Grand Totals.")
        elif difference > 0:
            add("grand_total", "Grand Totals", False,
                f"Passes add up to {passes_sum:.2f} but the statement says {printed:.2f} ({difference:.2f} not accounted for).")
        else:
            add("grand_total", "Grand Totals", False,
                f"Passes add up to {passes_sum:.2f} but the statement says {printed:.2f} ({-difference:.2f} more than the statement says).")

    # 4. Both readings found the same Grand Totals
    if det.grand_total is None and ai.grand_total is None:
        add("total_read_same", "Grand Totals read the same", True, "Neither reading found a Grand Totals figure.", skipped=True)
    elif det.grand_total is None or ai.grand_total is None:
        found = "the AI" if det.grand_total is None else "the deterministic reading"
        add("total_read_same", "Grand Totals read the same", False, f"Only {found} found a Grand Totals figure.")
    elif abs(det.grand_total - ai.grand_total) < TOLERANCE:
        add("total_read_same", "Grand Totals read the same", True, f"Both readings found {det.grand_total:.2f}.")
    else:
        add("total_read_same", "Grand Totals read the same", False,
            f"The deterministic reading found {det.grand_total:.2f} but the AI found {ai.grand_total:.2f}.")

    # 5. Per-device subtotals, only when printed
    if not det.subtotals:
        add("car_subtotals", "Per-car subtotals", True, "No per-device totals are printed.", skipped=True)
    else:
        bad = []
        for device, printed_sub in det.subtotals.items():
            got = round(sum(r["amount"] for r in rows if r["car"][0] == device), 2)
            if abs(got - printed_sub) >= TOLERANCE:
                bad.append(f"device {device}: statement says {printed_sub:.2f}, passes add up to {got:.2f}")
        add("car_subtotals", "Per-car subtotals", not bad,
            "; ".join(bad) + "." if bad else "Each printed per-device total matches its passes.")
    return checks


# ------------------------------------------------------------------ saving

def save_rows(conn: sqlite3.Connection, statement_id: int, user_id: str, rows: list[dict], review_status: str) -> dict:
    """Replace this statement's stored passes with `rows` (dicts as built by _rows). Passes already
    stored by ANOTHER live statement are skipped. Finds or adds the devices (a changed plate updates the
    device; which car a device belongs to is never touched). The caller commits.
    Returns {"inserted", "skipped", "new_devices": [labels], "plate_changes": [(device, old, new)]}."""
    conn.execute("DELETE FROM toll_transactions WHERE statement_id = ?", (statement_id,))
    existing = {r["dedupe_key"] for r in conn.execute(
        "SELECT dedupe_key FROM toll_transactions WHERE user_id = ? AND deleted_at IS NULL AND statement_id != ?",
        (user_id, statement_id))}

    keyed = []
    for r in rows:
        device, plate, state = r["car"]
        keyed.append((r, dedupe_key(device, plate, state, r["datetime"], r["road"], r["plaza"], r["lane"], r["direction"], r["amount"])))
    fresh = [(r, key) for r, key in keyed if key not in existing]

    latest: dict[tuple, str] = {}
    for r, _ in fresh:
        latest[r["car"]] = max(latest.get(r["car"], ""), r["datetime"][:10])
    device_ids, new_devices, plate_changes = resolve_devices(conn, user_id, latest)

    for r, key in fresh:
        conn.execute(
            "INSERT INTO toll_transactions (user_id, statement_id, device_ref, occurred_at, date, agency, road, plaza, lane, "
            "direction, amount, raw_line, dedupe_key, review_status) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (user_id, statement_id, device_ids[r["car"]], r["datetime"], r["datetime"][:10], r["agency"], r["road"], r["plaza"],
             r["lane"], r["direction"], round(r["amount"], 2), r.get("raw_line") or None, key, review_status),
        )
    return {"inserted": len(fresh), "skipped": len(keyed) - len(fresh), "new_devices": new_devices, "plate_changes": plate_changes}


def _ensure_devices_for(conn: sqlite3.Connection, user_id: str, extraction: TollExtraction) -> tuple[list[str], list[tuple]]:
    """Tags are found or added (and their plate brought up to date) even when every pass of them is a
    duplicate. Returns (labels of devices added, plate changes)."""
    seen = {car.key: max((p.date for p in car.passes), default=None) for car in extraction.cars}
    _, added, changes = resolve_devices(conn, user_id, seen)
    return added, changes


def _period(det: TollExtraction, ai: TollExtraction, rows: list[dict]) -> tuple[str | None, str | None]:
    if det.period_start and det.period_end:
        return det.period_start, det.period_end
    if ai.period_start and ai.period_end:
        return ai.period_start, ai.period_end
    dates = sorted(r["datetime"][:10] for r in rows)
    return (dates[0], dates[-1]) if dates else (None, None)


# ------------------------------------------------------------------ the background job

def process_toll_statement(statement_id: int, user_id: str, ollama_url: str, ollama_model: str) -> None:
    """The background job. Never raises: every failure path leaves the statement in a visible,
    restartable `error` state (the same design as statements and utility bills)."""
    conn = None
    try:
        conn = get_db()
        row = conn.execute("SELECT pdf_path FROM toll_statements WHERE id = ?", (statement_id,)).fetchone()
        if row is None:
            return
        pdf_path = row["pdf_path"]

        timings: dict[str, float] = {}
        _set_step(conn, statement_id, "Extracting text (pdftotext)")
        started = time.monotonic()
        try:
            det, raw_text = extract_toll_deterministic(pdf_path)
        except NoTextLayer as e:
            _fail(conn, statement_id, f"No text layer (scanned PDF, no OCR support): {e}")
            return
        except Exception as e:
            _fail(conn, statement_id, f"Deterministic extraction failed: {e}")
            return
        timings["deterministic_seconds"] = round(time.monotonic() - started, 2)

        # Cheap format check ahead of the slow vision call.
        if not re.search(r"e-?470|toll", raw_text, re.IGNORECASE):
            _fail(conn, statement_id,
                  "This PDF never mentions E-470 or tolls — it doesn't look like a toll statement. "
                  "Check you uploaded the right file.")
            return

        started = time.monotonic()
        try:
            vision: TollVisionResult = extract_vision_toll(
                pdf_path, ollama_url, ollama_model, on_step=lambda msg: _set_step(conn, statement_id, msg))
            conn.execute("UPDATE toll_statements SET llm_raw_response = ? WHERE id = ?", (vision.raw_response, statement_id))
            conn.commit()
        except VisionExtractionError as e:
            conn.execute("UPDATE toll_statements SET llm_raw_response = ? WHERE id = ?", (e.raw_response, statement_id))
            conn.commit()
            _fail(conn, statement_id, f"LLM extraction failed: {e}")
            return
        timings["ai_seconds"] = round(time.monotonic() - started, 2)
        ai = vision.extraction

        gone = conn.execute("SELECT deleted_at FROM toll_statements WHERE id = ?", (statement_id,)).fetchone()
        if gone is None or gone["deleted_at"] is not None:
            return          # deleted while it was being read: nothing to save

        _set_step(conn, statement_id, "Comparing the two readings")
        diff = diff_extractions(det, ai)
        rows = default_rows(diff)
        if not rows and det.grand_total is None and ai.grand_total is None:
            _fail(conn, statement_id, "No toll passes were found on this PDF.")
            return

        # Duplicates: every pass already imported from another statement -> refuse (keeps the PDF).
        existing = {r["dedupe_key"] for r in conn.execute(
            "SELECT dedupe_key FROM toll_transactions WHERE user_id = ? AND deleted_at IS NULL AND statement_id != ?",
            (user_id, statement_id))}
        if rows and all(dedupe_key(*r["car"], r["datetime"], r["road"], r["plaza"], r["lane"], r["direction"], r["amount"])
                        in existing for r in rows):
            _fail(conn, statement_id, f"Duplicate -- all {len(rows)} passes on this PDF are already imported.")
            return

        _set_step(conn, statement_id, "Verifying totals")
        checks = run_checks(det, ai, vision.rejected, diff, rows)
        clean = all(c["ok"] for c in checks)
        failed_text = " ".join(c["detail"] for c in checks if not c["ok"]) or None
        printed = det.grand_total if det.grand_total is not None else ai.grand_total
        period_start, period_end = _period(det, ai, rows)

        _set_step(conn, statement_id, "Saving passes")
        ai_dict = ai.to_dict()
        ai_dict["rejected"] = vision.rejected
        conn.execute("BEGIN")
        try:
            saved = save_rows(conn, statement_id, user_id, rows, "clean" if clean else "pending_review")
            extra_devices, extra_changes = _ensure_devices_for(conn, user_id, det)
            new_devices = saved["new_devices"] + [c for c in extra_devices if c not in saved["new_devices"]]
            plate_changes = saved["plate_changes"] + [c for c in extra_changes if c not in saved["plate_changes"]]
            conn.execute(
                "UPDATE toll_statements SET status = ?, review_status = ?, error_message = ?, period_start_date = ?, "
                "period_end_date = ?, raw_text_length = ?, deterministic_value = ?, llm_value = ?, total_tolls_printed = ?, "
                "checks_json = ?, unparsed_json = ?, skipped_duplicates = ?, notes = ?, review_note = NULL, timings_json = ?, "
                "processed_at = datetime('now') WHERE id = ?",
                ("complete" if clean else "pending_review", "clean" if clean else "pending_review", failed_text,
                 period_start, period_end, len(raw_text), json.dumps(det.to_dict()), json.dumps(ai_dict), printed,
                 json.dumps(checks), json.dumps(det.unparsed), saved["skipped"], notes_text(new_devices, plate_changes),
                 json.dumps(timings), statement_id),
            )
            conn.commit()
        except Exception:
            conn.rollback()
            raise

        rebuild_trips(conn, user_id)
        if clean:
            delete_pdf_toll(conn, [statement_id], pdf_path)

    except Exception as e:
        logger.exception("process_toll_statement(%s) failed unexpectedly", statement_id)
        if conn is not None:
            try:
                _fail(conn, statement_id, f"Unexpected error: {e}")
            except Exception:
                pass
    finally:
        if conn is not None:
            conn.close()


# ------------------------------------------------------------------ review

def confirm_review(conn: sqlite3.Connection, statement_id: int, user_id: str, rows: list[dict],
                   printed_total: float | None) -> dict:
    """Store the rows the person settled on as clean, mark the statement complete and delete its PDF.
    A remaining Grand Totals mismatch is recorded as acknowledged (review_note), like the bank-statement
    balance check. Returns {"inserted", "skipped", "note"}."""
    conn.execute("BEGIN")
    try:
        saved = save_rows(conn, statement_id, user_id, rows, "clean")
        passes_sum = round(sum(r["amount"] for r in rows), 2)
        note = None
        if printed_total is not None and abs(printed_total - passes_sum) >= TOLERANCE:
            note = (f"Confirmed by hand: Grand Totals {printed_total:.2f} vs passes {passes_sum:.2f} "
                    f"({abs(printed_total - passes_sum):.2f} difference acknowledged).")
        conn.execute(
            "UPDATE toll_statements SET status = 'complete', review_status = 'clean', error_message = NULL, "
            "total_tolls_printed = ?, skipped_duplicates = ?, review_note = ?, notes = COALESCE(?, notes), "
            "processed_at = datetime('now') WHERE id = ?",
            (printed_total, saved["skipped"], note, notes_text(saved["new_devices"], saved["plate_changes"]), statement_id),
        )
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    rebuild_trips(conn, user_id)
    pdf = conn.execute("SELECT pdf_path FROM toll_statements WHERE id = ?", (statement_id,)).fetchone()
    if pdf is not None and pdf["pdf_path"]:
        delete_pdf_toll(conn, [statement_id], pdf["pdf_path"])
    return {"inserted": saved["inserted"], "skipped": saved["skipped"], "note": note}
