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


def parse_number(raw) -> float | None:
    """'12.5', '-$1,234.56', '$ 1 234.56', '(12.50)' (negative) -> the number; None when the
    value is blank, isn't a number, or isn't finite (nan, inf, 1e999)."""
    if raw is None:
        return None
    if isinstance(raw, (int, float)) and not isinstance(raw, bool):
        value = float(raw)
        return value if math.isfinite(value) else None
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
