"""LLM extraction path for utility bills (SPEC.md section 15) —
sibling to parser/vision.py's bank-statement extraction, reusing its
low-level Ollama/rasterization plumbing (rasterize_pdf, warmup_model,
_post_ollama, _extract_json, the model-recently-used warm-up skip) rather
than duplicating it, since none of that is bank-statement-specific. Only
the prompt and the result shape are different here.

Deliberately NOT independently reconstructed by a second, full-field
regex parser the way an early draft of the bank-statement path tried and
abandoned (see deterministic.py's own module docstring for that history)
— this is the sole source of the actual bill fields (period, usage,
cost, the on/off-peak meter breakdown), same as the vision path is the
sole source of bank transactions. utility_deterministic.py's job is only
to sanity-check the numbers this path returns against the bill's own
printed text, not to re-derive them independently.
"""
from dataclasses import dataclass
from typing import Callable

from .vision import MAX_NOTES_CHARS, ask_vision_json, debug_call


@dataclass
class ElectricMeterDetails:
    grid_import_on_peak_kwh: float | None = None
    grid_import_off_peak_kwh: float | None = None
    grid_import_total_kwh: float | None = None
    solar_export_on_peak_kwh: float | None = None
    solar_export_off_peak_kwh: float | None = None
    solar_export_total_kwh: float | None = None
    net_on_peak_kwh: float | None = None
    net_off_peak_kwh: float | None = None
    net_total_kwh: float | None = None
    net_generated_on_peak_kwh: float | None = None
    net_generated_off_peak_kwh: float | None = None
    net_generated_total_kwh: float | None = None
    on_peak_rate: float | None = None
    on_peak_charge: float | None = None
    off_peak_rate: float | None = None
    off_peak_charge: float | None = None


@dataclass
class ParsedUtilityBill:
    utility_type: str            # 'electric' / 'gas' / 'water'
    provider: str
    period_start_date: str       # ISO-8601
    period_end_date: str
    usage_amount: float | None
    usage_unit: str | None
    cost: float
    due_date: str | None
    account_number_last4: str | None
    electric_meter_details: ElectricMeterDetails | None


@dataclass
class UtilityVisionResult:
    bills: list[ParsedUtilityBill]
    raw_response: str  # kept for the admin debug view, same as statements


_METER_FIELDS = [
    "grid_import_on_peak_kwh", "grid_import_off_peak_kwh", "grid_import_total_kwh",
    "solar_export_on_peak_kwh", "solar_export_off_peak_kwh", "solar_export_total_kwh",
    "net_on_peak_kwh", "net_off_peak_kwh", "net_total_kwh",
    "net_generated_on_peak_kwh", "net_generated_off_peak_kwh", "net_generated_total_kwh",
    "on_peak_rate", "on_peak_charge", "off_peak_rate", "off_peak_charge",
]

_UTILITY_PROMPT = """You are extracting billing data from a US utility bill \
(electric, gas, or water). Only two providers are ever used here: Xcel \
Energy and Aurora Water. Respond with ONLY a JSON object, no other text, \
in this exact shape:
{"bills": [{"utility_type": "electric", "provider": "Xcel Energy", \
"period_start_date": "2026-08-04", "period_end_date": "2026-09-02", \
"usage_amount": 933, "usage_unit": "kWh", "cost": 118.07, \
"due_date": "2026-09-29", "account_number_last4": "6019", \
"electric_meter_details": {"grid_import_on_peak_kwh": 219, \
"grid_import_off_peak_kwh": 714, "grid_import_total_kwh": 933, \
"solar_export_on_peak_kwh": 14, "solar_export_off_peak_kwh": 555, \
"solar_export_total_kwh": 569, "net_on_peak_kwh": 205, \
"net_off_peak_kwh": 159, "net_total_kwh": 364, \
"net_generated_on_peak_kwh": 0, "net_generated_off_peak_kwh": 0, \
"net_generated_total_kwh": 0, "on_peak_rate": 0.21277, \
"on_peak_charge": 43.62, "off_peak_rate": 0.07884, "off_peak_charge": 12.54}}]}

Rules:
- A single PDF is either ONE bill (Aurora Water: one "water" entry) or TWO
  bills on one combined statement (Xcel Energy dual-fuel: one "electric"
  entry AND one separate "gas" entry). Include one object per distinct
  utility type actually billed on this PDF — never merge electric and gas
  into a single entry, and never invent an entry for a utility type that
  isn't actually on the bill.
- For the electric entry's usage_amount/usage_unit, use the bill's own
  SUMMARY-LINE total (e.g. "Electricity Service ... 933 kWh") — this is
  gross grid import, NOT the net-of-solar figure.
- cost is that ONE utility type's own real total only (e.g. Xcel prints a
  separate "Total" for its Electricity Charges section and another for its
  Natural Gas Charges section) — every itemized fee/adjustment/tax already
  folded into that fuel's own printed total belongs in this number; never
  include the other fuel's charges, and never itemize the individual fees
  separately.
- If a bill shows a seasonal rate change mid-cycle (multiple usage
  sub-lines at different rates within one billing period), sum them into
  this single usage_amount/cost — do not treat them as separate bills or
  separate line items.
- If a water bill breaks usage into per-1,000-gallon tiers plus separate
  fixed charges (service, sewer, storm drain, drought surcharge), store
  only the AGGREGATE usage and the bill's own final total cost — not the
  tier-by-tier sub-calculations.
- electric_meter_details is REQUIRED (a real object, not null) whenever
  utility_type is "electric" and the bill shows an on-peak/off-peak meter
  reading breakdown; use null for any individual field genuinely not on
  the bill, and null for the WHOLE electric_meter_details object only if
  the bill has no on/off-peak split at all (a flat-rate electric bill).
  Always null for gas and water entries. Field meanings, exactly as
  printed on an Xcel statement's meter reading table:
    grid_import_on_peak_kwh / _off_peak_kwh / _total_kwh
      = "(On/Off) Peak Delivered by Xcel" — power flowing grid -> customer
    solar_export_on_peak_kwh / _off_peak_kwh / _total_kwh
      = "(On/Off) Pk Delivered by Customer" — power flowing customer -> grid
    net_on_peak_kwh / _off_peak_kwh / _total_kwh
      = "On Pk Net Delivered by Xcel" / "Off Pk Net Delivered by Xcel" /
        "Total Net Delivered by Xcel" — import minus export when import is
        larger, else 0; this is what the on/off-peak dollar RATE below is
        actually applied to
    net_generated_on_peak_kwh / _off_peak_kwh / _total_kwh
      = "On Net Generated by Customer" / "Off Net Generated by Customer" /
        "Net Generated by Customer" — export minus import when export is
        larger, else 0. Solar homes OFTEN have a nonzero figure here (e.g.
        off-peak 755 delivered by customer vs 679 by Xcel = 76 generated):
        copy each row's own USAGE column, never assume 0.
    The _total_kwh fields are the "Total …" rows (the on-peak and off-peak
    figures added up). Take every meter figure from the USAGE column (the
    last number, followed by kWh), not the current or previous reading.
    on_peak_rate / off_peak_rate = the $/kWh rate for RETOU On-Peak /
      Off-Peak from the Electricity Charges table
    on_peak_charge / off_peak_charge = the dollar charge for those same
      two lines
- Dates are ISO-8601 (YYYY-MM-DD), converted from whatever format is
  printed on the bill (e.g. "09/29/26" or "09/29/2026" -> "2026-09-29").
- account_number_last4 is the last 4 digits/characters of the account
  number, or null if none is legible.
- Every number is a plain JSON number — never a string, never with a "$"
  or a comma."""


def debug_call_utility(pdf_path: str, ollama_url: str, model: str, provider: str | None = None,
                       notes: str = "") -> tuple[float, str]:
    """parser.vision.debug_call with this bill's prompt."""
    return debug_call(pdf_path, ollama_url, model, utility_prompt(provider, notes))


KNOWN_PROVIDERS = ("Xcel Energy", "Aurora Water")

_GENERIC_UTILITY_PROMPT = """You are extracting billing data from a utility bill from {provider}.
Respond with ONLY a JSON object, no other text, in this exact shape:
{{"bills": [{{"utility_type": "electric", "provider": "{provider}", "period_start_date": "2026-08-04",
"period_end_date": "2026-09-02", "usage_amount": 933, "usage_unit": "kWh", "cost": 118.07,
"due_date": "2026-09-29", "account_number_last4": "6019", "electric_meter_details": null}}]}}

Rules:
- utility_type is exactly one of "electric", "gas" or "water". Include one object per utility
  type actually billed on this PDF (a combined statement can bill electric AND gas: two objects).
  Leave out anything that is not electric, gas or water (trash, internet, …).
- cost is that one utility type's own total for this period, with its fees and taxes, as printed;
  never the account balance, a past-due amount or another utility's charges.
- usage_amount / usage_unit: the period's total usage for that utility as printed (kWh, therms,
  CCF, gallons, …), or null when the bill doesn't show it.
- Dates are ISO-8601 (YYYY-MM-DD), converted from whatever format is printed.
- account_number_last4 is the last 4 characters of the account number, or null.
- electric_meter_details is always null.
- Every number is a plain JSON number — never a string, never with a "$" or a comma."""


def utility_prompt(provider: str | None = None, notes: str = "") -> str:
    """The Xcel / Aurora prompt for those two; a general one naming any other provider. Notes on an
    earlier reading (Re-extract) are added the same way as for statements."""
    if provider in KNOWN_PROVIDERS or not provider:
        prompt = _UTILITY_PROMPT
    else:
        prompt = _GENERIC_UTILITY_PROMPT.format(provider=provider.replace('"', "'"))
    notes = (notes or "").strip()[:MAX_NOTES_CHARS]
    if notes:
        prompt += ("\n\nA person checked an earlier extraction of this same bill against the PDF and wrote the notes "
                   "below. Follow them; they override the rules above where they conflict:\n" + notes)
    return prompt


def extract_vision_utility(
    pdf_path: str, ollama_url: str, model: str,
    on_step: Callable[[str], None] | None = None,
    provider: str | None = None, notes: str = "",
) -> UtilityVisionResult:
    """Utility bills (one PDF can hold several, e.g. electric + gas). A
    malformed bill entry is skipped, not fatal."""
    data, raw = ask_vision_json(pdf_path, ollama_url, model, utility_prompt(provider, notes), on_step)
    bills: list[ParsedUtilityBill] = []
    for row in data.get("bills", []):
        try:
            utility_type = str(row["utility_type"])
            if utility_type not in ("electric", "gas", "water"):
                continue  # malformed type -- skip this one entry, not the whole response

            meter = None
            meter_raw = row.get("electric_meter_details")
            if utility_type == "electric" and isinstance(meter_raw, dict):
                meter_kwargs = {}
                for field_name in _METER_FIELDS:
                    v = meter_raw.get(field_name)
                    meter_kwargs[field_name] = float(v) if v is not None else None
                meter = ElectricMeterDetails(**meter_kwargs)

            bills.append(ParsedUtilityBill(
                utility_type=utility_type,
                provider=str(row["provider"]),
                period_start_date=str(row["period_start_date"]),
                period_end_date=str(row["period_end_date"]),
                usage_amount=(float(row["usage_amount"]) if row.get("usage_amount") is not None else None),
                usage_unit=(str(row["usage_unit"]) if row.get("usage_unit") is not None else None),
                cost=float(row["cost"]),
                due_date=(str(row["due_date"]) if row.get("due_date") else None),
                account_number_last4=(str(row["account_number_last4"]) if row.get("account_number_last4") else None),
                electric_meter_details=meter,
            ))
        except (KeyError, TypeError, ValueError):
            continue  # one malformed bill entry doesn't invalidate the whole response

    return UtilityVisionResult(bills=bills, raw_response=raw)
