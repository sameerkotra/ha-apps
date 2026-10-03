"""One canonical spelling for every unit, shared everywhere a unit is read, stored or compared.

Before this module existed, the extraction prompt only recognised mass units (LB/KG/OZ/G) and the
normalizer that ran on the model's answer matched that same short list. A liquid item ("1 GAL",
"2 LITER") fell through both untouched, so the same real unit could be stored as "GAL" on one
receipt and "GALLON" on the next, and the analysis code's own separate alias table didn't
recognise "GALLON" either — so those two receipts could end up treated as different, incomparable
units. This module is the single place that maps every spelling to one code, so extraction,
analysis and the review screen's dropdowns always agree.
"""

from __future__ import annotations

import re

# Every canonical code, in the order they should be offered as choices (mass, then volume, then
# count). "EACH" is the special case for a line priced per whole item rather than per unit of size.
CANONICAL_UNITS = ("LB", "KG", "OZ", "G", "GAL", "QT", "PT", "L", "ML", "FLOZ", "CT", "EACH")

UNIT_ALIASES: dict[str, str] = {
    # mass
    "LB": "LB", "LBS": "LB", "POUND": "LB", "POUNDS": "LB", "#": "LB",
    "KG": "KG", "KGS": "KG", "KILO": "KG", "KILOS": "KG", "KILOGRAM": "KG", "KILOGRAMS": "KG",
    "OZ": "OZ", "OUNCE": "OZ", "OUNCES": "OZ",
    "G": "G", "GM": "G", "GMS": "G", "GRAM": "G", "GRAMS": "G",
    # volume
    "GAL": "GAL", "GALLON": "GAL", "GALLONS": "GAL", "GA": "GAL",
    "QT": "QT", "QUART": "QT", "QUARTS": "QT",
    "PT": "PT", "PINT": "PT", "PINTS": "PT",
    "L": "L", "LT": "L", "LTR": "L", "LITER": "L", "LITERS": "L", "LITRE": "L", "LITRES": "L",
    "ML": "ML", "MILLILITER": "ML", "MILLILITERS": "ML", "MILLILITRE": "ML", "MILLILITRES": "ML",
    "FLOZ": "FLOZ", "FLUIDOUNCE": "FLOZ", "FLUIDOUNCES": "FLOZ",
    # count / each
    "EA": "EACH", "EACH": "EACH", "UNIT": "EACH", "UNITS": "EACH",
    "PC": "EACH", "PCS": "EACH", "PIECE": "EACH", "PIECES": "EACH",
    "CT": "CT", "COUNT": "CT", "PK": "CT", "PACK": "CT",
}

_DISPLAY = {"EACH": "each", "FLOZ": "fl oz", "CT": "ct"}


def unit_code(value: str | None) -> str | None:
    """A unit as one of ``CANONICAL_UNITS``, or the cleaned-up original text if it is not
    recognised (so nothing is silently discarded), or None for an empty value.

    Matching ignores case, periods and spaces, so "fl oz", "Fl.Oz." and "FLOZ" are the same unit.
    """
    if not value:
        return None
    key = re.sub(r"[.\s]+", "", str(value).upper())
    if not key:
        return None
    return UNIT_ALIASES.get(key, key)


def display_unit(value: str | None) -> str | None:
    """A unit code as it should read to a person: lower-case "each", "fl oz", "ct"; everything
    else (LB, KG, GAL, ...) stays as its short upper-case code."""
    code = unit_code(value)
    return _DISPLAY.get(code, code) if code else None
