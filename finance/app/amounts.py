"""Reading a typed or imported number (an amount, a cost, a usage or meter figure).

Plain float() takes "nan", "inf" and "1e999" -- NaN then fails the database's
NOT NULL/CHECK constraints (a 500 that aborts a whole CSV import) and an
infinity poisons every total it is added to -- and refuses the forms a bank
export or a person writes: "$1,234.56", "1 234.56", "(12.50)" for a negative.
parse_number() accepts those and only finite values.
"""
import math
import re

_STRIP = re.compile(r"[\s$,]")
_SPACES = re.compile(r"[\s$]")
# Commas only as thousands separators: "1,234" and "1,234,567.89". "12,50" (a decimal comma) or
# "1,23" would otherwise lose the comma and read as 1250 / 123 — a hundred times too much.
_THOUSANDS = re.compile(r"[-+]?\(?[-+]?\d{1,3}(,\d{3})+(\.\d*)?\)?")


def parse_number(raw) -> float | None:
    """'12.5', '-$1,234.56', '$ 1 234.56', '(12.50)' (negative) -> the number; None when the
    value is blank, isn't a number, isn't finite (nan, inf, 1e999), or has a comma that isn't a
    thousands separator ('12,50' is refused rather than read as 1250)."""
    if raw is None:
        return None
    if isinstance(raw, (int, float)) and not isinstance(raw, bool):
        value = float(raw)
        return value if math.isfinite(value) else None
    if "," in str(raw) and not _THOUSANDS.fullmatch(_SPACES.sub("", str(raw))):
        return None
    s = _STRIP.sub("", str(raw))
    negative = len(s) > 2 and s.startswith("(") and s.endswith(")")
    if negative:
        s = s[1:-1]
        if s.startswith(("-", "+")):
            return None
    if not s or not re.fullmatch(r"[-+]?(\d+\.?\d*|\.\d+)", s):
        return None
    try:
        value = float(s)
    except ValueError:
        return None
    if not math.isfinite(value):
        return None
    return -value if negative else value


def to_cents(value: float) -> int:
    """A money amount as whole cents. Compare amounts this way: 0.07 - 0.06 is 0.010000000000000009 in
    floating point, so "within 0.01" said yes or no depending on the figures."""
    return int(round(round(float(value), 2) * 100))
