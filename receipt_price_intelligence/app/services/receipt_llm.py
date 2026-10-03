"""Prompt, schema, and response handling for LLM receipt extraction.

This module is deliberately free of third-party imports so it can be unit tested
without a database, HTTP client, or model server.

Trust boundary (see LLM_EXTRACTION.md): model output is untrusted input.

    model text -> parse_model_json -> normalize_extraction -> validate_extraction -> user review

Nothing here talks to the network or the database.
"""

from __future__ import annotations

import json
import math
import re
from datetime import date, datetime, timedelta
from typing import Any

from app.services import units

PROMPT_VERSION = "2.1"
SCHEMA_VERSION = "2.1"

# --------------------------------------------------------------------------- #
# Prompt
# --------------------------------------------------------------------------- #

SYSTEM_PROMPT = """You are a receipt transcription engine. You look at photos of retail receipts and output ONE JSON object. You transcribe what is printed. You never guess, correct, or calculate.

GENERAL RULES
- Use only information visible on the receipt. If a value is missing, unreadable, or you are unsure, use null. Never invent, estimate, or compute a value that is not printed.
- Copy item descriptions exactly as printed, including abbreviations, capitalization, and typos. Do not expand abbreviations, translate, or rename. Do not add a "common" or friendly name.
- Numbers are plain JSON numbers: no currency symbols, no thousands separators, period as the decimal point. All amounts are positive, including discounts.
- Times are 24-hour HH:MM.
- Read the receipt from top to bottom and list EVERY purchased line once, in printed order. Do not skip, merge, or repeat lines.
- If several images are supplied they are consecutive photos of the same receipt and may overlap. List each purchased line once.

PURCHASE DATE
- The purchase date is the date of the transaction: the date printed with the time, register, or receipt number, usually near the top or bottom of the receipt.
- Ignore every other date: return-by, exchange, expiry, "valid through", coupon, promotion, birthday, survey, and loyalty-reward dates.
- purchase_date_text is that date exactly as printed (for example "03/14/25" or "14 MAR 2025"), without the time. Copy it character for character.
- purchase_date is the same date as YYYY-MM-DD (two-digit years mean 20YY). If the day and month could be swapped (for example 03/04/25) and nothing on the receipt settles it, still give your best reading, and always fill in purchase_date_text so it can be checked. Use null only when no purchase date is printed.

WHAT IS AN ITEM
- An item is a product or service the customer bought.
- These are NOT items: subtotal, tax, total, balance due, payment method lines (VISA, CASH, CHANGE, card digits, approval codes), loyalty/points messages, "items sold" counts, surveys, barcodes, and store header/footer text.
- If the description is on one line and its quantity/weight math is on the next line, that is still ONE item.

ITEM FIELDS
- item_type: WEIGHT if sold by measured weight or volume; QUANTITY if several units were bought at a per-unit price (for example "3 @ 1.99"); UNIT for a single unit; UNKNOWN if you cannot tell.
- weight_unit and unit_price_unit are one of: LB, KG, OZ, G (weight), GAL, QT, PT, L, ML, FLOZ (volume), CT (count), or "each" for unit_price_unit when the line is priced per whole item rather than per unit of size. Use exactly these codes, not the printed word ("GALLON" -> "GAL", "LITER" -> "L", "OUNCES" -> "OZ").
- quantity is a count of units. weight_value is a measured weight. Never put a weight in quantity. Package sizes that are part of the product name ("MILK 1GAL", "CHIPS 10OZ") stay in the description and are not weights.
- For a plain line that shows only a price: item_type UNIT, quantity 1, unit_price = that price, unit_price_unit "each", line_total = that price.
- line_subtotal is the amount for the line before any discount on that line. discount_amount is the money taken off that line (positive). line_total is the amount charged for the line after its discount.
- tax_amount is only for a tax amount printed for that specific item. Tax flag letters (T, F, A, *) are not amounts; ignore them.
- extraction_confidence is 0.0 to 1.0: 0.9 or more when the line is clear, 0.5 to 0.8 when partly blurry or ambiguous, below 0.5 when you can barely read it. For unreadable text give your best transcription of the visible characters with confidence below 0.5. For unreadable amounts use null.

DISCOUNTS
- A discount, coupon, markdown, or "member price" line printed under an item (often shown as a negative like -1.00) belongs to that item: set discount_amount to the positive amount, keep line_subtotal as the pre-discount price, and set line_total to the price after the discount. Do not create a separate item for it.
- A discount that is not tied to a particular item (an order-level coupon, points redemption, or a discount near the totals) is not an item. Record it in discount_total.

STORE
- store_name is the retail brand or banner printed at the top (for example "WALMART SUPERCENTER").
- store_address is the physical address of the store where the purchase happened, as one string in printed order (street, city, state, postal code). Never use a corporate, headquarters, mailing, or customer-service address, a website, or a phone number. If only such an address is printed, use null.
- store_number is the store or branch number if printed (labels like "ST#", "Store", "#"), otherwise null.
- receipt_number is the receipt, invoice, ticket, order, or transaction number. It is not a card number, approval code, register/terminal/lane number, cashier id, or phone number.

TAXES AND TOTALS
- taxes has one entry per tax line printed in the totals area. tax_name is as printed. tax_rate is a percentage (6.0 means 6%) and only if a rate is printed; otherwise null. Never work out a rate from the amounts. taxable_amount only if printed.
- subtotal is the printed amount before tax. tax_total is the printed total tax. fee_total is printed bag fees, deposits, or surcharges. grand_total is the final amount charged (TOTAL or BALANCE DUE), not cash tendered and not change. If a total is not printed, use null.

WORKED EXAMPLES
Line: "2.45 LB @ 1.29/LB   3.16" under "BANANAS"
  -> receipt_description "BANANAS", item_type WEIGHT, quantity null, weight_value 2.45, weight_unit "LB", unit_price 1.29, unit_price_unit "LB", line_total 3.16
Line: "GAS UNLEADED 10.2 GAL @ 3.49/GAL   35.60"
  -> receipt_description "GAS UNLEADED", item_type WEIGHT, quantity null, weight_value 10.2, weight_unit "GAL", unit_price 3.49, unit_price_unit "GAL", line_total 35.60
Line: "GV 2% MILK 1GAL   3.48"
  -> receipt_description "GV 2% MILK 1GAL", item_type UNIT, quantity 1, weight_value null, unit_price 3.48, unit_price_unit "each", line_total 3.48
Lines: "SPARKLING WATER" then "3 @ 1.99   5.97"
  -> receipt_description "SPARKLING WATER", item_type QUANTITY, quantity 3, unit_price 1.99, unit_price_unit "each", line_total 5.97
Lines: "CEREAL   4.99" then "COUPON   -1.00"
  -> ONE item: receipt_description "CEREAL", line_subtotal 4.99, discount_amount 1.00, line_total 3.99

Return ONLY the JSON object. No markdown, no commentary."""


_USER_PROMPT_TEMPLATE = """Extract this receipt{plural_note}. Today's date is {today}; a purchase is never dated after today.

Return JSON with exactly this shape (use null for anything not visible):
{{
  "store_name": string|null,
  "store_address": string|null,
  "store_number": string|null,
  "purchase_date_text": string|null,
  "purchase_date": "YYYY-MM-DD"|null,
  "purchase_time": "HH:MM"|null,
  "receipt_number": string|null,
  "currency": "USD"|null,
  "items": [
    {{
      "receipt_description": string,
      "item_type": "UNIT"|"WEIGHT"|"QUANTITY"|"UNKNOWN",
      "quantity": number|null,
      "weight_value": number|null,
      "weight_unit": "LB"|"KG"|"OZ"|"G"|"GAL"|"QT"|"PT"|"L"|"ML"|"FLOZ"|null,
      "unit_price": number|null,
      "unit_price_unit": "each"|"LB"|"KG"|"OZ"|"G"|"GAL"|"QT"|"PT"|"L"|"ML"|"FLOZ"|"CT"|null,
      "line_subtotal": number|null,
      "discount_amount": number|null,
      "line_total": number|null,
      "tax_amount": number|null,
      "extraction_confidence": number
    }}
  ],
  "taxes": [
    {{"tax_name": string|null, "tax_rate": number|null, "taxable_amount": number|null, "tax_amount": number|null}}
  ],
  "subtotal": number|null,
  "discount_total": number|null,
  "tax_total": number|null,
  "fee_total": number|null,
  "grand_total": number|null,
  "store_confidence": number,
  "total_confidence": number
}}"""


MAX_CORRECTION_LENGTH = 1000

_CORRECTION_TEMPLATE = """

CORRECTIONS FROM THE PERSON WHO REVIEWED YOUR PREVIOUS READING OF THIS RECEIPT
Read the receipt again and apply these notes. They tell you where to look or how to interpret what is printed. Everything above still applies: transcribe only what is actually printed, never invent values, and reply with the same JSON format as before.
<notes>
{notes}
</notes>"""


def clean_correction(text: str | None) -> str | None:
    """Tidy a person's correction note: drop control characters, cap the length, None if empty."""
    if not text:
        return None
    cleaned = "".join(ch for ch in str(text) if ch in "\n\t" or ch >= " ")
    cleaned = re.sub(r"[ \t]+", " ", cleaned)
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned).strip()
    return cleaned[:MAX_CORRECTION_LENGTH].strip() or None


def build_user_prompt(image_count: int = 1, correction: str | None = None, today: date | None = None) -> str:
    """User-turn text. Mentions multiple photos when more than one image is sent.

    A correction note from the reviewer is appended to the standard prompt (never replaces it).
    """
    if image_count > 1:
        note = f" (the {image_count} images are consecutive photos of ONE receipt, top to bottom)"
    else:
        note = ""
    prompt = _USER_PROMPT_TEMPLATE.format(plural_note=note, today=(today or date.today()).isoformat())
    notes = clean_correction(correction)
    if notes:
        prompt += _CORRECTION_TEMPLATE.format(notes=notes)
    return prompt


# --------------------------------------------------------------------------- #
# JSON schema (for servers that support structured output)
# --------------------------------------------------------------------------- #

def _nullable(json_type: str) -> dict[str, Any]:
    return {"type": [json_type, "null"]}


_ITEM_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "receipt_description": {"type": "string"},
        "item_type": {"type": "string", "enum": ["UNIT", "WEIGHT", "QUANTITY", "UNKNOWN"]},
        "quantity": _nullable("number"),
        "weight_value": _nullable("number"),
        "weight_unit": _nullable("string"),
        "unit_price": _nullable("number"),
        "unit_price_unit": _nullable("string"),
        "line_subtotal": _nullable("number"),
        "discount_amount": _nullable("number"),
        "line_total": _nullable("number"),
        "tax_amount": _nullable("number"),
        "extraction_confidence": {"type": "number"},
    },
    "required": [
        "receipt_description", "item_type", "quantity", "weight_value", "weight_unit",
        "unit_price", "unit_price_unit", "line_subtotal", "discount_amount", "line_total",
        "tax_amount", "extraction_confidence",
    ],
    "additionalProperties": False,
}

_TAX_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "tax_name": _nullable("string"),
        "tax_rate": _nullable("number"),
        "taxable_amount": _nullable("number"),
        "tax_amount": _nullable("number"),
    },
    "required": ["tax_name", "tax_rate", "taxable_amount", "tax_amount"],
    "additionalProperties": False,
}

RECEIPT_JSON_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "store_name": _nullable("string"),
        "store_address": _nullable("string"),
        "store_number": _nullable("string"),
        "purchase_date_text": _nullable("string"),
        "purchase_date": _nullable("string"),
        "purchase_time": _nullable("string"),
        "receipt_number": _nullable("string"),
        "currency": _nullable("string"),
        "items": {"type": "array", "items": _ITEM_SCHEMA},
        "taxes": {"type": "array", "items": _TAX_SCHEMA},
        "subtotal": _nullable("number"),
        "discount_total": _nullable("number"),
        "tax_total": _nullable("number"),
        "fee_total": _nullable("number"),
        "grand_total": _nullable("number"),
        "store_confidence": {"type": "number"},
        "total_confidence": {"type": "number"},
    },
    "required": [
        "store_name", "store_address", "store_number", "purchase_date_text", "purchase_date", "purchase_time",
        "receipt_number", "currency", "items", "taxes", "subtotal", "discount_total",
        "tax_total", "fee_total", "grand_total", "store_confidence", "total_confidence",
    ],
    "additionalProperties": False,
}


# --------------------------------------------------------------------------- #
# Reading model responses
# --------------------------------------------------------------------------- #

class ReceiptParseError(ValueError):
    """The model responded, but not with usable receipt JSON."""


def extract_message_text(api_format: str, body: dict[str, Any]) -> tuple[str, bool]:
    """Pull the assistant text out of an API response body.

    Returns (text, truncated). ``truncated`` is True when the server reports the
    output hit the token limit, which almost always means the JSON is cut off.
    """
    truncated = False

    if api_format == "ollama":
        message = body.get("message") or {}
        content = message.get("content")
        truncated = body.get("done_reason") == "length"
    else:
        choices = body.get("choices") or []
        if not choices:
            raise ReceiptParseError("Model response contained no choices")
        choice = choices[0] or {}
        message = choice.get("message") or {}
        content = message.get("content")
        truncated = choice.get("finish_reason") == "length"

    if isinstance(content, list):  # OpenAI content parts
        content = "".join(
            part.get("text", "") for part in content if isinstance(part, dict)
        )

    if not isinstance(content, str) or not content.strip():
        hint = " (the model may have used its whole token budget thinking)" if truncated else ""
        raise ReceiptParseError(f"Model returned an empty response{hint}")

    return content, truncated


_THINK_BLOCK = re.compile(r"<think>.*?</think>", re.DOTALL | re.IGNORECASE)
_FENCE = re.compile(r"^```[a-zA-Z0-9_-]*\s*|\s*```$")
_TRAILING_COMMA = re.compile(r",\s*([}\]])")


def parse_model_json(text: str) -> dict[str, Any]:
    """Parse a JSON object out of model text.

    Tolerates reasoning blocks, markdown fences, prose around the JSON, and
    trailing commas. Raises ReceiptParseError if nothing usable is found.
    """
    if not isinstance(text, str):
        raise ReceiptParseError("Model response was not text")

    cleaned = _THINK_BLOCK.sub("", text)
    # An unterminated <think> block (output cut off while reasoning)
    if "<think>" in cleaned.lower():
        cleaned = cleaned[: cleaned.lower().index("<think>")]
    cleaned = cleaned.strip()
    cleaned = _FENCE.sub("", cleaned).strip()

    decoder = json.JSONDecoder()
    candidates = [cleaned, _TRAILING_COMMA.sub(r"\1", cleaned)]

    for candidate in candidates:
        try:
            return _as_receipt_dict(json.loads(candidate))
        except (json.JSONDecodeError, ReceiptParseError):
            pass

        # Look for a JSON object embedded in surrounding prose
        for match in re.finditer(r"\{", candidate):
            try:
                obj, _ = decoder.raw_decode(candidate[match.start():])
            except json.JSONDecodeError:
                continue
            try:
                return _as_receipt_dict(obj)
            except ReceiptParseError:
                continue

    raise ReceiptParseError("Model response did not contain valid receipt JSON")


def _as_receipt_dict(obj: Any) -> dict[str, Any]:
    if isinstance(obj, list):
        obj = {"items": obj}
    if not isinstance(obj, dict):
        raise ReceiptParseError("Model JSON was not an object")
    # Reject objects that share no keys with the receipt schema. This catches an API
    # envelope (e.g. the whole HTTP body passed in by mistake), an empty object, and
    # stray nested objects found while scanning prose.
    if not (set(obj) & set(RECEIPT_JSON_SCHEMA["properties"])):
        raise ReceiptParseError("Model JSON does not look like receipt data")
    return obj


# --------------------------------------------------------------------------- #
# Normalization
# --------------------------------------------------------------------------- #

_ITEM_TYPES = {"UNIT", "WEIGHT", "QUANTITY", "UNKNOWN"}
UNREADABLE_DESCRIPTION = "(unreadable line)"


def _clean_str(value: Any, max_len: int = 500) -> str | None:
    if value is None or isinstance(value, bool):
        return None
    text = re.sub(r"\s+", " ", str(value)).strip()
    if not text or text.lower() in {"null", "none", "n/a", "unknown"}:
        return None
    return text[:max_len]


def to_number(value: Any) -> float | None:
    """Best-effort conversion of model output to a finite float, else None."""
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        number = float(value)
    elif isinstance(value, str):
        text = value.strip()
        if not text:
            return None
        negative = text.startswith("(") and text.endswith(")") or text.endswith("-") or text.startswith("-")
        text = re.sub(r"[^0-9.,]", "", text)
        if not text:
            return None
        # "1,234.56" -> 1234.56 ; "3,16" (decimal comma) -> 3.16
        if "," in text and "." in text:
            text = text.replace(",", "")
        elif "," in text:
            head, _, tail = text.rpartition(",")
            text = f"{head.replace(',', '')}.{tail}" if len(tail) in (1, 2) else text.replace(",", "")
        try:
            number = float(text)
        except ValueError:
            return None
        if negative:
            number = -number
    else:
        return None

    if math.isnan(number) or math.isinf(number):
        return None
    return round(number, 4)


def _money(value: Any) -> float | None:
    return to_number(value)


def _confidence(value: Any) -> float | None:
    number = to_number(value)
    if number is None:
        return None
    if 10.0 < number <= 100.0:  # model answered in percent (85 -> 0.85)
        number = number / 100.0
    return round(min(1.0, max(0.0, number)), 2)


def _normalize_weight_unit(value: Any) -> str | None:
    """A unit of weight or volume as its canonical code (see app.services.units), or the cleaned
    original text if it is not a recognised unit."""
    text = _clean_str(value, 20)
    if text is None:
        return None
    return units.unit_code(text)


def _normalize_price_unit(value: Any) -> str | None:
    """A per-unit price's unit as its canonical code, "each" for a whole-item price, or the
    cleaned original text if it is not recognised."""
    text = _clean_str(value, 20)
    if text is None:
        return None
    code = units.unit_code(text.lstrip("/").strip())
    return "each" if code == "EACH" else code


_MONTHS = {"JAN": 1, "FEB": 2, "MAR": 3, "APR": 4, "MAY": 5, "JUN": 6,
           "JUL": 7, "AUG": 8, "SEP": 9, "OCT": 10, "NOV": 11, "DEC": 12}
_MONTH_FIRST_CURRENCIES = {"USD", "CAD"}
DATE_ORDERS = ("auto", "MDY", "DMY")


def _make_date(year: int, month: int, day: int) -> date | None:
    try:
        return date(year, month, day)
    except ValueError:
        return None


def _year(text: str) -> int:
    year = int(text)
    return year + 2000 if len(text) <= 2 else year


def _strip_date_noise(text: str) -> str:
    """Upper-case a printed date and drop times, weekday names, labels and ordinal suffixes."""
    t = text.upper().strip()
    t = re.sub(r"(?<=\d)T(?=\d{1,2}:)", " ", t)  # ISO 2025-03-14T10:32
    t = re.sub(r"\b\d{1,2}:\d{2}(?::\d{2})?(?:\.\d+)?\s*(?:[AP]\.?M\.?)?(?:\s*(?:Z|UTC|[+-]\d{2}:?\d{2}))?", " ", t)
    t = re.sub(r"\b(?:MON|TUE|WED|THU|FRI|SAT|SUN)[A-Z]*\b\.?", " ", t)
    t = re.sub(r"\b(?:DATE|DT|ON)\b:?", " ", t)
    t = re.sub(r"(\d)(?:ST|ND|RD|TH)\b", r"\1", t)
    t = t.replace(",", " ")
    return re.sub(r"\s+", " ", t).strip(" .:-")


def parse_date_candidates(text: Any) -> list[tuple[date, str]]:
    """Every way a printed date can be read, as (date, order) pairs.

    Unambiguous dates (year first, a month name, or a day above 12) give one reading. A date
    like 03/04/25 gives two: month first ("MDY") then day first ("DMY").
    """
    cleaned = _clean_str(text, 60)
    if cleaned is None:
        return []
    t = _strip_date_noise(cleaned)
    if not t:
        return []

    m = re.fullmatch(r"(\d{4})[-/. ](\d{1,2})[-/. ](\d{1,2})", t)
    if m:
        y, a, b = int(m[1]), int(m[2]), int(m[3])
        d = _make_date(y, a, b) or _make_date(y, b, a)
        return [(d, "YMD")] if d else []

    m = re.fullmatch(r"(\d{4})(\d{2})(\d{2})", t)
    if m:
        d = _make_date(int(m[1]), int(m[2]), int(m[3]))
        return [(d, "YMD")] if d else []

    m = re.fullmatch(r"(\d{1,2})[-/. ](\d{1,2})[-/. ](\d{4}|\d{2})", t)
    if m:
        a, b, year = int(m[1]), int(m[2]), _year(m[3])
        mdy, dmy = _make_date(year, a, b), _make_date(year, b, a)
        if mdy and dmy and mdy == dmy:
            return [(mdy, "MDY")]
        return [(d, order) for d, order in ((mdy, "MDY"), (dmy, "DMY")) if d]

    m = re.fullmatch(r"(\d{1,2})[-. ]?([A-Z]{3,9})[-. ]*(\d{4}|\d{2})", t)  # 14 MAR 2025, 14-MAR-25
    if m and m[2][:3] in _MONTHS:
        d = _make_date(_year(m[3]), _MONTHS[m[2][:3]], int(m[1]))
        return [(d, "NAME")] if d else []

    m = re.fullmatch(r"([A-Z]{3,9})[-. ]*(\d{1,2})[-. ]+(\d{4}|\d{2})", t)  # MAR 14 2025
    if m and m[1][:3] in _MONTHS:
        d = _make_date(_year(m[3]), _MONTHS[m[1][:3]], int(m[2]))
        return [(d, "NAME")] if d else []

    return []


def normalize_date(value: Any) -> str | None:
    """Return YYYY-MM-DD or None. Accepts ISO dates (with or without a time), month names and
    slash dates; a slash date that could be read two ways is read month first."""
    candidates = parse_date_candidates(value)
    return candidates[0][0].isoformat() if candidates else None


def resolve_purchase_date(
    printed: Any,
    model_value: Any = None,
    order: str = "auto",
    currency: str | None = None,
    today: date | None = None,
) -> dict[str, Any]:
    """Work out the purchase date from the date as printed, using the model's reading as a fallback.

    The printed text is what gets parsed (the model's own conversion is only used if the text
    cannot be). For a date that could be month-first or day-first:
    * ``order`` "MDY" or "DMY" (a setting) decides;
    * with "auto", the currency decides (USD/CAD month first, other currencies day first);
    * with no currency, the model's reading decides if it matches one of the two, else month first.
    A reading in the future loses to one that is not.

    Returns {"date": ISO|None, "ambiguous": bool, "alternatives": [ISO...]}. ``ambiguous`` is only
    true when the choice was a guess (order "auto").
    """
    today = today or date.today()
    candidates = parse_date_candidates(printed)
    from_model = False
    if not candidates:
        candidates = parse_date_candidates(model_value)
        from_model = True
    if not candidates:
        return {"date": None, "ambiguous": False, "alternatives": []}
    if len(candidates) == 1:
        return {"date": candidates[0][0].isoformat(), "ambiguous": False, "alternatives": []}

    by_order = {label: d for d, label in candidates}
    mdy, dmy = by_order["MDY"], by_order["DMY"]
    explicit = order in ("MDY", "DMY")
    if explicit:
        chosen_label = order
    else:
        code = (currency or "").upper()
        if code in _MONTH_FIRST_CURRENCIES:
            chosen_label = "MDY"
        elif code:
            chosen_label = "DMY"
        else:
            hint = parse_date_candidates(model_value) if not from_model else []
            hint_date = hint[0][0] if len(hint) == 1 else None
            chosen_label = "DMY" if hint_date == dmy else "MDY"

    chosen, other = (mdy, dmy) if chosen_label == "MDY" else (dmy, mdy)
    limit = today + timedelta(days=1)
    if chosen > limit >= other:
        chosen, other = other, chosen
    return {
        "date": chosen.isoformat(),
        "ambiguous": not explicit,
        "alternatives": [other.isoformat()] if not explicit else [],
    }


def normalize_time(value: Any) -> str | None:
    """Return 24-hour HH:MM or None."""
    text = _clean_str(value, 20)
    if text is None:
        return None
    for fmt in ("%H:%M", "%H:%M:%S", "%I:%M %p", "%I:%M%p", "%I:%M:%S %p"):
        try:
            return datetime.strptime(text.upper(), fmt).strftime("%H:%M")
        except ValueError:
            continue
    return None


def _normalize_currency(value: Any) -> str | None:
    text = _clean_str(value, 10)
    if text and re.fullmatch(r"[A-Za-z]{3}", text):
        return text.upper()
    return None


def _normalize_item(raw: Any) -> dict[str, Any] | None:
    if not isinstance(raw, dict):
        return None

    description = _clean_str(raw.get("receipt_description"))
    quantity = to_number(raw.get("quantity"))
    weight_value = to_number(raw.get("weight_value"))
    weight_unit = _normalize_weight_unit(raw.get("weight_unit"))
    unit_price = _money(raw.get("unit_price"))
    unit_price_unit = _normalize_price_unit(raw.get("unit_price_unit"))
    line_subtotal = _money(raw.get("line_subtotal"))
    discount = _money(raw.get("discount_amount"))
    line_total = _money(raw.get("line_total"))
    tax_amount = _money(raw.get("tax_amount"))
    confidence = _confidence(raw.get("extraction_confidence"))

    if discount is not None:
        discount = abs(discount)  # convention: discounts are positive

    has_amounts = any(v is not None for v in (line_total, line_subtotal, unit_price))
    if description is None and not has_amounts:
        return None  # nothing usable on this line
    if description is None:
        description = UNREADABLE_DESCRIPTION
        confidence = min(confidence if confidence is not None else 0.3, 0.3)

    item_type = (_clean_str(raw.get("item_type"), 20) or "").upper()
    if item_type not in _ITEM_TYPES:
        item_type = ""

    # A weight copied into the quantity field is the most common small-model mistake.
    if weight_value is not None and quantity is not None and abs(quantity - weight_value) < 1e-9:
        quantity = None

    if not item_type or item_type == "UNKNOWN":
        if weight_value is not None:
            item_type = "WEIGHT"
        elif quantity is not None and quantity > 1:
            item_type = "QUANTITY"
        elif line_total is not None or unit_price is not None:
            item_type = "UNIT"
        else:
            item_type = "UNKNOWN"

    return {
        "receipt_description": description,
        "item_type": item_type,
        "quantity": quantity,
        "weight_value": weight_value,
        "weight_unit": weight_unit,
        "unit_price": unit_price,
        "unit_price_unit": unit_price_unit,
        "line_subtotal": line_subtotal,
        "discount_amount": discount,
        "line_total": line_total,
        "tax_amount": tax_amount,
        "extraction_confidence": confidence,
    }


def _normalize_tax(raw: Any) -> dict[str, Any] | None:
    if not isinstance(raw, dict):
        return None
    tax = {
        "tax_name": _clean_str(raw.get("tax_name"), 100),
        "tax_rate": to_number(raw.get("tax_rate")),
        "taxable_amount": _money(raw.get("taxable_amount")),
        "tax_amount": _money(raw.get("tax_amount")),
    }
    if all(v is None for v in tax.values()):
        return None
    return tax


def tax_type_for(tax_name: str | None) -> str:
    """Coarse tax category for storage. The receipt text decides; default is sales tax."""
    name = (tax_name or "").upper()
    for label in ("VAT", "GST", "HST", "PST", "QST"):
        if re.search(rf"\b{label}\b", name):
            return label
    return "SALES"


def normalize_extraction(raw: dict[str, Any], date_order: str = "auto", today: date | None = None) -> dict[str, Any]:
    """Coerce untrusted model output into the canonical extraction shape.

    ``date_order`` ("auto", "MDY" or "DMY") settles dates that could be read month-first or day-first.
    """
    currency = _normalize_currency(raw.get("currency"))
    resolved = resolve_purchase_date(raw.get("purchase_date_text"), raw.get("purchase_date"), date_order, currency, today)
    items = [i for i in (_normalize_item(x) for x in (raw.get("items") or [])) if i]
    taxes = [t for t in (_normalize_tax(x) for x in (raw.get("taxes") or [])) if t]

    return {
        "store_name": _clean_str(raw.get("store_name"), 255),
        "store_address": _clean_str(raw.get("store_address"), 500),
        "store_number": _clean_str(raw.get("store_number"), 50),
        "purchase_date": resolved["date"],
        "purchase_date_text": _clean_str(raw.get("purchase_date_text"), 40),
        "purchase_date_ambiguous": resolved["ambiguous"],
        "purchase_date_alternatives": resolved["alternatives"],
        "purchase_time": normalize_time(raw.get("purchase_time")),
        "receipt_number": _clean_str(raw.get("receipt_number"), 100),
        "currency": currency,
        "items": items,
        "taxes": taxes,
        "subtotal": _money(raw.get("subtotal")),
        "discount_total": _abs_or_none(_money(raw.get("discount_total"))),
        "tax_total": _money(raw.get("tax_total")),
        "fee_total": _money(raw.get("fee_total")),
        "grand_total": _money(raw.get("grand_total")),
        "store_confidence": _confidence(raw.get("store_confidence")),
        "total_confidence": _confidence(raw.get("total_confidence")),
    }


def _abs_or_none(value: float | None) -> float | None:
    return abs(value) if value is not None else None


# --------------------------------------------------------------------------- #
# Arithmetic validation
# --------------------------------------------------------------------------- #

MONEY_TOLERANCE = 0.02


def _differs(a: float, b: float, tolerance: float = MONEY_TOLERANCE) -> bool:
    return round(abs(a - b), 2) > tolerance


def item_math_issue(item: dict[str, Any]) -> str | None:
    """Return a human-readable problem with one item's arithmetic, or None if it checks out.

    Mirrors ``itemMathIssue`` in frontend/review.html so the server and the review
    screen flag the same lines.
    """
    unit_price = item.get("unit_price")
    subtotal = item.get("line_subtotal")
    discount = item.get("discount_amount") or 0.0
    total = item.get("line_total")

    # 1. Discount arithmetic: subtotal - discount = total
    if subtotal is not None and total is not None and item.get("discount_amount") is not None:
        if _differs(subtotal - discount, total):
            return f"{subtotal:.2f} - {discount:.2f} discount = {subtotal - discount:.2f}, but line total is {total:.2f}"

    # 2. Rate arithmetic: weight (or quantity) x unit price = pre-discount amount
    if unit_price is not None:
        multiplier = item.get("weight_value")
        label = None
        if multiplier is not None:
            label = f"{multiplier:g} {item.get('weight_unit') or ''}".strip()
        elif item.get("quantity") is not None:
            multiplier = item["quantity"]
            label = f"{multiplier:g}"
        if multiplier is not None:
            expected = multiplier * unit_price
            if subtotal is not None:
                actual = subtotal
            elif total is not None:
                actual = total + discount
            else:
                actual = None
            if actual is not None and _differs(expected, actual, MONEY_TOLERANCE + 0.005 * abs(expected)):
                return f"{label} x {unit_price:.2f} = {expected:.2f}, but line shows {actual:.2f}"

    return None


def check_totals(data: dict[str, Any]) -> list[dict[str, Any]]:
    """Whole-receipt arithmetic checks. Each result: {code, message, difference}."""
    problems: list[dict[str, Any]] = []
    items = data.get("items") or []
    subtotal = data.get("subtotal")
    discount_total = data.get("discount_total") or 0.0
    tax_total = data.get("tax_total")
    fee_total = data.get("fee_total") or 0.0
    grand_total = data.get("grand_total")
    taxes = data.get("taxes") or []

    totals = [i["line_total"] for i in items if i.get("line_total") is not None]
    items_sum = round(sum(totals), 2) if totals else None

    # Item lines vs printed subtotal. Discounts printed near the totals may or may not
    # already be reflected in the item lines, so accept either reading.
    if items_sum is not None and subtotal is not None:
        if _differs(items_sum, subtotal) and _differs(items_sum - discount_total, subtotal):
            problems.append({
                "code": "items_vs_subtotal",
                "message": f"Item lines add up to {items_sum:.2f}, but the receipt subtotal is {subtotal:.2f}",
                "difference": round(items_sum - subtotal, 2),
            })

    # Subtotal + tax + fees vs grand total (falling back to item sum when no subtotal).
    base = subtotal if subtotal is not None else items_sum
    if base is not None and grand_total is not None:
        expected = base + (tax_total or 0.0) + fee_total
        if _differs(expected, grand_total) and _differs(expected - discount_total, grand_total):
            problems.append({
                "code": "total_mismatch",
                "message": f"Subtotal + tax + fees = {expected:.2f}, but the receipt total is {grand_total:.2f}",
                "difference": round(expected - grand_total, 2),
            })

    tax_amounts = [t["tax_amount"] for t in taxes if t.get("tax_amount") is not None]
    if tax_amounts and tax_total is not None and _differs(sum(tax_amounts), tax_total):
        problems.append({
            "code": "tax_lines_vs_total",
            "message": f"Tax lines add up to {sum(tax_amounts):.2f}, but total tax is {tax_total:.2f}",
            "difference": round(sum(tax_amounts) - tax_total, 2),
        })

    return problems


def validate_extraction(data: dict[str, Any], today: date | None = None) -> list[dict[str, Any]]:
    """Return warnings about a normalized extraction. Never raises; warnings do not block review."""
    warnings: list[dict[str, Any]] = []
    today = today or date.today()

    if not data.get("items"):
        warnings.append({"level": "error", "code": "no_items", "message": "No items were found on the receipt", "item_index": None})

    for index, item in enumerate(data.get("items") or []):
        label = f'Item {index + 1} "{item["receipt_description"]}"'
        if item["receipt_description"] == UNREADABLE_DESCRIPTION:
            warnings.append({"level": "warning", "code": "unreadable_item", "message": f"Item {index + 1} could not be read", "item_index": index})
        if item.get("line_total") is None:
            warnings.append({"level": "warning", "code": "missing_line_total", "message": f"{label} has no line total", "item_index": index})
        issue = item_math_issue(item)
        if issue:
            warnings.append({"level": "warning", "code": "line_math", "message": f"{label}: {issue}", "item_index": index})

    for problem in check_totals(data):
        warnings.append({"level": "warning", "code": problem["code"], "message": problem["message"], "item_index": None})

    purchase_date = data.get("purchase_date")
    if not purchase_date:
        warnings.append({"level": "warning", "code": "date_missing", "message": "No purchase date was found. Enter it before saving", "item_index": None})
    else:
        parsed = date.fromisoformat(purchase_date)
        if parsed > today + timedelta(days=1):
            warnings.append({"level": "warning", "code": "date_future", "message": f"Purchase date {purchase_date} is in the future", "item_index": None})
        elif parsed.year < 2000 or (today - parsed).days > 730:
            warnings.append({"level": "warning", "code": "date_old", "message": f"Purchase date {purchase_date} looks wrong", "item_index": None})
        if data.get("purchase_date_ambiguous") and not data.get("currency"):
            alt = ", ".join(data.get("purchase_date_alternatives") or [])
            printed = data.get("purchase_date_text") or purchase_date
            warnings.append({"level": "warning", "code": "date_ambiguous",
                             "message": f'The date "{printed}" could be read two ways. Showing {purchase_date}' + (f" (it could also be {alt})" if alt else ""),
                             "item_index": None})

    return warnings
