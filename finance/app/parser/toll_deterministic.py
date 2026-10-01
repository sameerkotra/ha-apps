"""Deterministic extraction of an E-470 toll statement (SPEC.md section 18.1 and 18.4).

Unlike the bank-statement and utility paths, which only sanity-check figures against the PDF
text, a toll statement is regular enough to read completely from `pdftotext -layout`: a summary
block, then one "Transactions For Device # ... Plate # ..." heading per car, then one line per
pass. This module reads all of it; the AI extraction (toll_vision.py) reads the same PDF
independently, and toll_pipeline.py compares the two.

A line that starts like a pass (a date and a time) but does not fit the row pattern is never
dropped silently: it goes into `unparsed`, which fails the "no dropped lines" check.

The Toll Status column (e.g. `VTOLL 8758490`) is stepped over and not kept. Grand Totals is the
only summary figure read; Total Tolls, Previous Balance, Adjustments and Payments are ignored. Every pass
amount is positive and above zero, so a pass line whose amount is negative or zero is treated as
unparsed instead of being imported.
"""
import re
from dataclasses import dataclass, field
from datetime import datetime

from .deterministic import NoTextLayer, run_pdftotext, MIN_TEXT_LENGTH

__all__ = [
    "TollPass", "TollCarBlock", "TollExtraction", "NoTextLayer", "normalize_direction",
    "parse_plate", "parse_toll_text", "extract_toll_deterministic", "parse_datetime",
    "clean_amount", "extraction_from_dict",
]


@dataclass
class TollPass:
    occurred_at: str            # ISO 'YYYY-MM-DD HH:MM:SS', local time as printed
    agency: str
    road: str
    plaza: str
    lane: str
    direction: str
    amount: float               # always positive
    raw_line: str = ""

    @property
    def date(self) -> str:
        return self.occurred_at[:10]

    def to_dict(self) -> dict:
        return {"datetime": self.occurred_at, "agency": self.agency, "road": self.road, "plaza": self.plaza,
                "lane": self.lane, "direction": self.direction, "amount": round(self.amount, 2)}


@dataclass
class TollCarBlock:
    device_id: str              # '' when the heading has none
    plate: str
    state: str
    passes: list[TollPass] = field(default_factory=list)

    @property
    def key(self) -> tuple[str, str, str]:
        return (self.device_id, self.plate, self.state)

    @property
    def match_key(self) -> str:
        """How two extractions' cars are paired: the tag id when there is one, else the plate."""
        return self.device_id or self.plate


@dataclass
class TollExtraction:
    grand_total: float | None = None            # printed Grand Totals, absolute value
    period_start: str | None = None
    period_end: str | None = None
    cars: list[TollCarBlock] = field(default_factory=list)
    unparsed: list[str] = field(default_factory=list)
    subtotals: dict[str, float] = field(default_factory=dict)   # device id -> printed per-device total

    def all_passes(self) -> list[tuple[TollCarBlock, TollPass]]:
        return [(car, p) for car in self.cars for p in car.passes]

    def passes_total(self) -> float:
        return round(sum(p.amount for _, p in self.all_passes()), 2)

    def to_dict(self) -> dict:
        return {
            "grand_total": self.grand_total, "period_start": self.period_start, "period_end": self.period_end,
            "cars": [{"device_id": c.device_id, "plate": c.plate, "state": c.state,
                      "transactions": [p.to_dict() for p in c.passes]} for c in self.cars],
            "unparsed": list(self.unparsed), "subtotals": dict(self.subtotals),
        }


def extraction_from_dict(data: dict) -> TollExtraction:
    """Rebuild an extraction from its stored JSON (toll_statements.deterministic_value / llm_value)."""
    ex = TollExtraction(
        grand_total=data.get("grand_total"), period_start=data.get("period_start"), period_end=data.get("period_end"),
        unparsed=list(data.get("unparsed") or []), subtotals=dict(data.get("subtotals") or {}),
    )
    for c in data.get("cars") or []:
        block = TollCarBlock(str(c.get("device_id") or ""), str(c.get("plate") or ""), str(c.get("state") or ""))
        for t in c.get("transactions") or []:
            block.passes.append(TollPass(
                t["datetime"], t.get("agency") or "", t.get("road") or "", t.get("plaza") or "",
                str(t.get("lane") or ""), t.get("direction") or "", float(t["amount"]),
            ))
        ex.cars.append(block)
    return ex


# ------------------------------------------------------------------ small parsers

_DIRECTIONS = {"n": "North", "nb": "North", "north": "North", "s": "South", "sb": "South", "south": "South",
               "e": "East", "eb": "East", "east": "East", "w": "West", "wb": "West", "west": "West"}


def normalize_direction(text: str) -> str:
    return _DIRECTIONS.get((text or "").strip().lower(), (text or "").strip().title())


def parse_plate(raw: str) -> tuple[str, str]:
    """'carplate-co' -> ('CARPLATE', 'CO'). Split on the LAST hyphen: the plate itself may contain hyphens."""
    raw = (raw or "").strip().upper()
    if "-" in raw:
        plate, state = raw.rsplit("-", 1)
        if len(state) == 2 and state.isalpha() and plate:
            return plate, state
    return raw, ""


def parse_datetime(date_text: str, time_text: str, ampm: str) -> str | None:
    """'8/10/2026', '6:30:53', 'AM' -> '2026-08-10 06:30:53' (seconds optional)."""
    fmt = "%m/%d/%Y %I:%M:%S %p" if time_text.count(":") == 2 else "%m/%d/%Y %I:%M %p"
    try:
        return datetime.strptime(f"{date_text} {time_text} {ampm.upper()}", fmt).strftime("%Y-%m-%d %H:%M:%S")
    except ValueError:
        return None


def clean_amount(text) -> float | None:
    """'$1.25', '-$170.45', '(3.00)', 1.5 -> the signed number, or None if it is not a money amount."""
    if isinstance(text, (int, float)):
        return float(text)
    s = str(text or "").strip()
    neg = s.startswith("(") and s.endswith(")") or "-" in s
    digits = re.sub(r"[^\d.]", "", s)
    if not digits or digits.count(".") > 1 or digits == ".":
        return None
    try:
        value = float(digits)
    except ValueError:
        return None
    return -value if neg else value


# ------------------------------------------------------------------ text patterns

_STARTS_LIKE_PASS = re.compile(r"^\d{1,2}/\d{1,2}/\d{4}\s+\d{1,2}:\d{2}")
_ROW = re.compile(
    r"^(?P<date>\d{1,2}/\d{1,2}/\d{4})\s+(?P<time>\d{1,2}:\d{2}(?::\d{2})?)\s*(?P<ampm>[AP]M)\s+"
    r"(?P<agency>[A-Z]{2})\s+(?P<road>\S+)\s+(?P<plaza>.+?)\s+Lane\s+(?P<lane>\S+)\s+"
    r"(?P<direction>North|South|East|West|NB|SB|EB|WB)\b(?P<status>.*?)\s*"
    r"(?P<amount>[-(]?\s*\$\s*-?\s*[\d,]*\.\d{2}\)?)\s*$",
    re.IGNORECASE,
)
_HEADING = re.compile(r"Transactions\s+For\s+Device\s*#?\s*(?P<device>\d*)\s*Plate\s*#?\s*(?P<plate>\S+)", re.IGNORECASE)
# "Grand Totals: $166.25" -- the one summary figure the passes are checked against. It may sit at the end of
# a line that has other summary text on it, and its amount may be on the next line. "Total Tolls" is not it.
_GRAND_TOTAL = re.compile(r"\bGrand\s+Totals?\b\s*:?\s*(?P<amount>[-(]?\s*\$?\s*-?\s*[\d,]*\.\d{2}\)?)?\s*$", re.IGNORECASE)
_SUBTOTAL = re.compile(
    r"^\s*(?:sub-?\s*)?total(?:\s+tolls)?\s+(?:for\s+)?device\s*#?\s*(?P<device>\d+)\s*:?\s*(?P<amount>[-(]?\s*\$?\s*-?\s*[\d,]*\.\d{2}\)?)\s*$",
    re.IGNORECASE,
)
_PERIOD = re.compile(
    r"(?:statement|billing)?\s*period\s*:?\s*(?P<a>\d{1,2}/\d{1,2}/\d{4})\s*(?:-|to|through|thru)\s*(?P<b>\d{1,2}/\d{1,2}/\d{4})",
    re.IGNORECASE,
)


def _iso_date(us: str) -> str | None:
    try:
        return datetime.strptime(us, "%m/%d/%Y").strftime("%Y-%m-%d")
    except ValueError:
        return None


def _match_row(line: str) -> tuple[TollPass | None, str]:
    """(pass, reason). reason is '' on success, else why the line did not become a pass."""
    m = _ROW.match(line)
    if not m:
        return None, ""
    occurred = parse_datetime(m["date"], m["time"], m["ampm"])
    amount = clean_amount(m["amount"])
    if occurred is None:
        return None, "the date or time is not valid"
    if amount is None or amount <= 0:
        return None, "the amount is not a positive number"
    # The line as printed, minus the Toll Status column (ignored, never kept).
    kept = re.sub(r"\s+", " ", line[:m.start("status")] + " " + line[m.end("status"):]).strip()
    return TollPass(
        occurred_at=occurred, agency=m["agency"].upper(), road=m["road"].upper(), plaza=m["plaza"].strip(),
        lane=m["lane"], direction=normalize_direction(m["direction"]), amount=round(amount, 2), raw_line=kept,
    ), ""


def parse_toll_text(text: str) -> TollExtraction:
    """Read the whole `pdftotext -layout` text of a toll statement."""
    lines = [re.sub(r"\s+", " ", raw).strip() for raw in text.splitlines()]
    ex = TollExtraction()
    blocks: dict[tuple, TollCarBlock] = {}
    current: TollCarBlock | None = None

    def is_boundary(line: str) -> bool:
        return not line or bool(_STARTS_LIKE_PASS.match(line)) or bool(_HEADING.search(line))

    i = 0
    while i < len(lines):
        line = lines[i]
        i += 1
        if not line:
            continue

        heading = _HEADING.search(line)
        if heading:
            plate, state = parse_plate(heading["plate"])
            key = (heading["device"], plate, state)
            current = blocks.get(key)
            if current is None:
                current = blocks[key] = TollCarBlock(*key)
                ex.cars.append(current)
            continue

        if _STARTS_LIKE_PASS.match(line):
            parsed, reason = _match_row(line)
            consumed = 0
            if parsed is None and not reason:
                # A long plaza name can wrap onto the next line(s): join and try again.
                joined = line
                for extra in range(1, 3):
                    if i - 1 + extra >= len(lines) or is_boundary(lines[i - 1 + extra]):
                        break
                    joined = f"{joined} {lines[i - 1 + extra]}"
                    parsed, reason = _match_row(joined)
                    if parsed is not None or reason:
                        consumed = extra
                        break
            if parsed is None:
                ex.unparsed.append(line + (f" [{reason}]" if reason else ""))
            elif current is None:
                ex.unparsed.append(line + " [no car heading above this line]")
            else:
                current.passes.append(parsed)
            i += consumed
            continue

        m = _GRAND_TOTAL.search(line)
        if m and ex.grand_total is None:
            token = m["amount"]
            if not token and i < len(lines) and lines[i]:
                token = lines[i] if re.fullmatch(r"[-(]?\s*\$?\s*-?\s*[\d,]*\.\d{2}\)?", lines[i]) else None
            value = clean_amount(token) if token else None
            if value is not None:
                ex.grand_total = round(abs(value), 2)
            continue

        m = _SUBTOTAL.match(line)
        if m:
            value = clean_amount(m["amount"])
            if value is not None:
                ex.subtotals[m["device"]] = round(abs(value), 2)
            continue

        m = _PERIOD.search(line)
        if m and ex.period_start is None:
            ex.period_start, ex.period_end = _iso_date(m["a"]), _iso_date(m["b"])

    return ex


def extract_toll_deterministic(pdf_path: str) -> tuple[TollExtraction, str]:
    """(extraction, raw text). Raises NoTextLayer for a scanned PDF, like the other pipelines."""
    text = run_pdftotext(pdf_path)
    if len(text.strip()) < MIN_TEXT_LENGTH:
        raise NoTextLayer(f"Only {len(text.strip())} chars extracted — no text layer")
    return parse_toll_text(text), text
