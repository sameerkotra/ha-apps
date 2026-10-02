"""Vision reading of a toll road statement (SPEC.md section 18.4): the
prompt and the result shape; the request loop is parser/vision.ask_vision_json.
toll_pipeline.py compares it with the deterministic reading. A row that can't be
read (no valid date and time, or an amount that isn't positive) is reported in
`rejected` and counts as a disagreement, never silently dropped.
"""
import json
import re
from dataclasses import dataclass, field
from typing import Callable

from .toll_deterministic import (
    TollCarBlock, TollExtraction, TollPass, clean_amount, normalize_direction, parse_datetime, parse_plate,
)
from .vision import ask_vision_json, debug_call


@dataclass
class TollVisionResult:
    extraction: TollExtraction
    raw_response: str
    rejected: list[str] = field(default_factory=list)


_TOLL_PROMPT = """You are extracting toll data from a toll road statement. Respond with ONLY \
a JSON object, no other text, in this exact shape:
{"grand_total": 170.45, "period_start": "2026-08-01", "period_end": "2026-08-31", \
"cars": [{"device_id": "1234567", "plate": "CARPLATE", "state": "CO", "transactions": [\
{"date": "8/10/2026", "time": "6:30:53 AM", "agency": "XX", "road": "TOLLWAY 1", "plaza": "MAIN ST", \
"lane": "1", "direction": "South", "amount": 1.25}, \
{"date": "8/10/2026", "time": "4:17:25 PM", "agency": "XX", "road": "TOLLWAY 1", "plaza": "PLAZA A", \
"lane": "3", "direction": "North", "amount": 2.60}]}]}

Rules:
- One object in "cars" per "Transactions For Device # ... Plate # ..." heading. device_id is the \
digits after "Device #" (an empty string if there are none). plate is the plate number and state \
is the two letters after the LAST hyphen of the printed plate (for "carplate-co": plate \
"CARPLATE", state "CO").
- Include EVERY toll line under each heading, on every page. Never invent a line and never skip one.
- A statement can run over several pages. A later page may repeat only the column titles \
("Transaction Date/Time ... Amount") WITHOUT repeating the "Transactions For Device #" heading: those \
lines still belong to the LAST heading above them. Put them in that same car's "transactions", never \
in a new car with an empty device_id.
- Each line reads: date, time (AM/PM), location (a two-letter agency code, the road's name or number, \
then the toll point's name), "Lane N" with a direction, a Toll Status column, and an amount.
  date = the date exactly as printed (month/day/year, e.g. "8/10/2026"); time = the time exactly as \
printed INCLUDING its AM or PM (e.g. "6:30:53 AM", "4:17:25 PM"). Copy them; do NOT convert to 24-hour \
time and do NOT drop the AM/PM.
  agency = the two-letter code at the start of the location; road = the road's name or number as printed; \
plaza = the toll point's name between the road and "Lane".
  lane = the lane NUMBER only; direction = North, South, East or West as printed after the lane.
  amount = the line's amount as a plain positive JSON number (no "$", no comma).
- IGNORE the Toll Status column entirely (text such as "VTOLL 8758490"): do not return it.
- grand_total = the figure printed after "Grand Totals" in the statement's summary, as a positive \
number (drop any minus sign and the "$"). Use null if there is no "Grand Totals" line. Do NOT use \
"Total Tolls" for it, and do NOT return Total Tolls, Previous Balance, Adjustments or Payments.
- period_start / period_end = the statement period as ISO dates, or null if none is printed.
- Every number is a plain JSON number, never a string."""


_DATETIME_RE = re.compile(r"^(\d{4}-\d{2}-\d{2})[ T](\d{2}:\d{2})(?::(\d{2}))?$")


def _normalize_datetime(value) -> str | None:
    m = _DATETIME_RE.match(str(value or "").strip())
    if not m:
        return None
    return f"{m.group(1)} {m.group(2)}:{m.group(3) or '00'}"


_US_DATE_RE = re.compile(r"^(\d{1,2})/(\d{1,2})/(\d{4})$")
_ISO_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_CLOCK_RE = re.compile(r"^(\d{1,2}:\d{2}(?::\d{2})?)\s*(?:([AaPp])\.?\s*[Mm]\.?)?$")
_US_WHOLE_RE = re.compile(r"^(\d{1,2}/\d{1,2}/\d{4})[ T]+(.+)$")


def _combine(date_text: str, time_text: str) -> str | None:
    """A date (as printed, M/D/YYYY, or ISO) and a time (12-hour with AM/PM as printed, or 24-hour)
    -> 'YYYY-MM-DD HH:MM:SS'. The 12-hour conversion is the deterministic reader's own
    (parse_datetime), so the two readings can never convert differently."""
    date_text, time_text = date_text.strip(), time_text.strip()
    clock = _CLOCK_RE.match(time_text)
    if not clock:
        return None
    hhmm, ampm = clock.group(1), clock.group(2)
    if ampm:
        us = date_text
        if _ISO_DATE_RE.match(date_text):
            y, mo, d = date_text.split("-")
            us = f"{int(mo)}/{int(d)}/{y}"
        return parse_datetime(us, hhmm, ampm.upper() + "M")
    if _US_DATE_RE.match(date_text):
        mo, d, y = _US_DATE_RE.match(date_text).groups()
        date_text = f"{y}-{int(mo):02d}-{int(d):02d}"
    return _normalize_datetime(f"{date_text} {hhmm}")


def _occurred_at(t: dict) -> str | None:
    """The moment of one returned pass. The model is asked for `date` and `time` exactly as printed (the
    AM/PM is kept, so nothing is converted by the model: it once turned 4:17 PM into 04:17). A single
    `datetime` string is still accepted, in ISO form or as printed."""
    date_text, time_text = str(t.get("date") or "").strip(), str(t.get("time") or "").strip()
    if date_text and time_text:
        return _combine(date_text, time_text)
    whole = str(t.get("datetime") or "").strip()
    iso = _normalize_datetime(whole)
    if iso:
        return iso
    m = _US_WHOLE_RE.match(whole)
    return _combine(m.group(1), m.group(2)) if m else None


def debug_call_toll(pdf_path: str, ollama_url: str, model: str) -> tuple[float, str]:
    """parser.vision.debug_call with the toll-statement prompt."""
    return debug_call(pdf_path, ollama_url, model, _TOLL_PROMPT)


def parse_toll_response(data: dict) -> tuple[TollExtraction, list[str]]:
    """The model's JSON -> (extraction, rejected rows). Pure, so it is testable without Ollama."""
    ex = TollExtraction()
    rejected: list[str] = []

    total = data.get("grand_total")
    if total is not None:
        value = clean_amount(total)
        ex.grand_total = round(abs(value), 2) if value is not None else None
    ex.period_start = str(data["period_start"]) if data.get("period_start") else None
    ex.period_end = str(data["period_end"]) if data.get("period_end") else None

    blocks: dict[tuple, TollCarBlock] = {}
    last_key: tuple | None = None
    for car in data.get("cars") or []:
        if not isinstance(car, dict):
            continue
        device = re.sub(r"\D", "", str(car.get("device_id") or ""))
        plate, state = str(car.get("plate") or "").strip().upper(), str(car.get("state") or "").strip().upper()
        if plate and not state:
            plate, state = parse_plate(plate)
        key = (device, plate, state)
        if key == ("", "", "") and last_key is not None:
            # No device, no plate: the model started a "car" for lines under a page's repeated column titles.
            # Like the text reader, those lines continue the last heading above them.
            key = last_key
        last_key = key
        block = blocks.get(key)
        if block is None:
            block = blocks[key] = TollCarBlock(*key)
            ex.cars.append(block)
        for t in car.get("transactions") or []:
            try:
                occurred = _occurred_at(t)
                amount = clean_amount(t.get("amount"))
                if occurred is None or amount is None or amount <= 0:
                    raise ValueError("unreadable date/time or amount")
                block.passes.append(TollPass(
                    occurred_at=occurred, agency=str(t.get("agency") or "").strip().upper(),
                    road=str(t.get("road") or "").strip().upper(), plaza=str(t.get("plaza") or "").strip(),
                    lane=str(t.get("lane") or "").strip(), direction=normalize_direction(str(t.get("direction") or "")),
                    amount=round(amount, 2),
                ))
            except (AttributeError, ValueError, TypeError):
                rejected.append(json.dumps(t, default=str)[:200])
    return ex, rejected


def extract_vision_toll(
    pdf_path: str, ollama_url: str, model: str,
    on_step: Callable[[str], None] | None = None,
) -> TollVisionResult:
    """Raises VisionExtractionError on any failure, always carrying the raw response Ollama returned."""
    data, raw = ask_vision_json(pdf_path, ollama_url, model, _TOLL_PROMPT, on_step)
    extraction, rejected = parse_toll_response(data)
    return TollVisionResult(extraction=extraction, raw_response=raw, rejected=rejected)
