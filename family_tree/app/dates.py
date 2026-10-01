"""Genealogy dates (§5): exact, partial (year / month+year), year-less (day +
month, "birthday known, year not"), and qualified (about / before / after /
between). Stored as a canonical GEDCOM-style `date_text` plus the parsed parts
of the (first) date, an approximate flag and a `sort_key`.

Accepted combinations: year; month+year; day+month+year; day+month.
Rejected: a day without a month, a month on its own.
"""
import calendar
from datetime import date

MONTHS = ["JAN", "FEB", "MAR", "APR", "MAY", "JUN", "JUL", "AUG", "SEP", "OCT", "NOV", "DEC"]
MONTH_NAMES = ["January", "February", "March", "April", "May", "June", "July", "August",
               "September", "October", "November", "December"]
_MONTH_LOOKUP = {m: i + 1 for i, m in enumerate(MONTHS)}
_MONTH_LOOKUP.update({n.upper(): i + 1 for i, n in enumerate(MONTH_NAMES)})
_MONTH_LOOKUP.update({"SEPT": 9})

QUALS = ("exact", "about", "before", "after", "between")
_QUAL_TAG = {"about": "ABT", "before": "BEF", "after": "AFT"}
_TAG_QUAL = {"ABT": "about", "EST": "about", "CAL": "about", "ABOUT": "about", "CIRCA": "about", "C.": "about",
             "BEF": "before", "BEFORE": "before", "AFT": "after", "AFTER": "after"}

MIN_YEAR, MAX_YEAR = 1, 2200


class DateError(ValueError):
    pass


def _as_int(v, what):
    if v is None or v == "":
        return None
    try:
        i = int(v)
    except (TypeError, ValueError):
        raise DateError(f"{what} must be a number.")
    return i


def _check_parts(d, m, y) -> tuple:
    d, m, y = _as_int(d, "Day"), _as_int(m, "Month"), _as_int(y, "Year")
    if d is None and m is None and y is None:
        raise DateError("Enter at least a year, or a day and month.")
    if m is not None and not 1 <= m <= 12:
        raise DateError("Month must be 1–12.")
    if y is not None and not MIN_YEAR <= y <= MAX_YEAR:
        raise DateError(f"Year must be between {MIN_YEAR} and {MAX_YEAR}.")
    if d is not None:
        if m is None:
            raise DateError("A day needs a month too.")
        # 29 Feb is fine without a year; with a year it must be a leap year.
        limit = calendar.monthrange(y if y is not None else 2000, m)[1]
        if not 1 <= d <= limit:
            raise DateError(f"{MONTH_NAMES[m - 1]} {'' if y is None else y} doesn't have a day {d}.".replace("  ", " "))
    elif m is not None and y is None:
        raise DateError("A month on its own isn't a date — add a day or a year.")
    return d, m, y


def _gedcom_one(d, m, y) -> str:
    parts = []
    if d is not None:
        parts.append(str(d))
    if m is not None:
        parts.append(MONTHS[m - 1])
    if y is not None:
        parts.append(str(y))
    return " ".join(parts)


def _sort_key(d, m, y):
    if y is None:
        return None
    return f"{y:04d}-{(m or 0):02d}-{(d or 0):02d}"


def from_parts(obj: dict | None) -> dict | None:
    """{qual, d, m, y, d2, m2, y2} → stored columns, or None for "no date".
    Raises DateError with a message suitable for a 422."""
    if not obj:
        return None
    if all(obj.get(k) in (None, "") for k in ("d", "m", "y")):
        return None
    qual = obj.get("qual") or "exact"
    if qual not in QUALS:
        raise DateError("Unknown date qualifier.")
    d, m, y = _check_parts(obj.get("d"), obj.get("m"), obj.get("y"))
    if qual == "between":
        d2, m2, y2 = _check_parts(obj.get("d2"), obj.get("m2"), obj.get("y2"))
        if y is None or y2 is None:
            raise DateError("Both dates of a 'between' range need a year.")
        if (y2, m2 or 0, d2 or 0) < (y, m or 0, d or 0):
            raise DateError("The second date of a range must come after the first.")
        text = f"BET {_gedcom_one(d, m, y)} AND {_gedcom_one(d2, m2, y2)}"
    else:
        if qual != "exact" and y is None:
            raise DateError("About / before / after need a year.")
        one = _gedcom_one(d, m, y)
        text = one if qual == "exact" else f"{_QUAL_TAG[qual]} {one}"
    return {
        "date_text": text, "date_y": y, "date_m": m, "date_d": d,
        "date_approx": 0 if qual == "exact" else 1, "sort_key": _sort_key(d, m, y),
    }


def _parse_one(tokens: list) -> tuple:
    d = m = y = None
    for t in tokens:
        u = t.upper().rstrip(",.")
        if u in _MONTH_LOOKUP:
            if m is not None:
                raise DateError(f"Couldn't read the date near '{t}'.")
            m = _MONTH_LOOKUP[u]
        elif u.isdigit():
            n = int(u)
            if len(u) <= 2 and m is None and d is None and y is None:
                d = n
            elif len(u) <= 2 and d is None and m is not None and y is None and n <= 31 and len(tokens) == 2:
                d = n                                    # "MAR 12"
            else:
                if y is not None:
                    raise DateError(f"Couldn't read the date near '{t}'.")
                y = n
        else:
            raise DateError(f"Couldn't read the date near '{t}'.")
    if d is not None and m is None and y is None:     # a lone small number is a year ("45" → 45)
        d, y = None, d
    return d, m, y


def parse_text(text: str | None) -> dict | None:
    """GEDCOM-style or friendly text: "1950", "Mar 1950", "12 March 1950",
    "12 MAR", "ABT 1950", "about 1950", "BEF 1920", "BET 1900 AND 1910",
    "1950-03-12". Day/month order in slash dates is decided by the browser."""
    if text is None or not str(text).strip():
        return None
    s = str(text).strip()
    if len(s) == 10 and s[4] == "-" and s[7] == "-" and s.replace("-", "").isdigit():
        return from_parts({"qual": "exact", "y": s[:4], "m": s[5:7], "d": s[8:10]})
    tokens = s.replace("/", " ").split()
    head = tokens[0].upper()
    if head in ("BET", "BETWEEN", "FROM"):
        rest = tokens[1:]
        joiners = [i for i, t in enumerate(rest) if t.upper() in ("AND", "TO", "-")]
        if not joiners:
            raise DateError("A 'between' date needs two dates: 'between 1900 and 1910'.")
        j = joiners[0]
        d, m, y = _parse_one(rest[:j])
        d2, m2, y2 = _parse_one(rest[j + 1:])
        return from_parts({"qual": "between", "d": d, "m": m, "y": y, "d2": d2, "m2": m2, "y2": y2})
    qual = "exact"
    if head in _TAG_QUAL:
        qual = _TAG_QUAL[head]
        tokens = tokens[1:]
    if not tokens:
        raise DateError("Enter a date after the qualifier.")
    d, m, y = _parse_one(tokens)
    return from_parts({"qual": qual, "d": d, "m": m, "y": y})


def to_parts(row) -> dict | None:
    """Stored columns → the {qual, d, m, y, d2, m2, y2} object the editor uses."""
    text = row["date_text"] if row else None
    if not text:
        return None
    out = {"qual": "exact", "d": row["date_d"], "m": row["date_m"], "y": row["date_y"]}
    head = text.split()[0]
    if head == "BET":
        out["qual"] = "between"
        second = text.split(" AND ", 1)[1].split()
        d2, m2, y2 = _parse_one(second)
        out.update(d2=d2, m2=m2, y2=y2)
    elif head in ("ABT", "BEF", "AFT"):
        out["qual"] = _TAG_QUAL[head]
    return out


def _display_one(d, m, y) -> str:
    if d is not None and m is not None:
        return f"{d} {MONTH_NAMES[m - 1]}" + (f" {y}" if y is not None else "")
    if m is not None:
        return f"{MONTH_NAMES[m - 1]} {y}"
    return str(y)


def display(row) -> str | None:
    """'12 March 1950', 'about 1950', 'before 1920', 'between 1900 and 1910', '12 March'."""
    p = to_parts(row)
    if not p:
        return None
    one = _display_one(p["d"], p["m"], p["y"])
    q = p["qual"]
    if q == "between":
        return f"between {one} and {_display_one(p['d2'], p['m2'], p['y2'])}"
    return one if q == "exact" else f"{q} {one}"


def age_on(birth, on: date) -> int | None:
    """Whole years from a birth row (date_y/m/d) to `on`; None without a birth year."""
    if not birth or birth["date_y"] is None:
        return None
    y, m, d = birth["date_y"], birth["date_m"], birth["date_d"]
    age = on.year - y
    if m is None:
        return max(age, 0)                     # year only: best estimate
    if (on.month, on.day) < (m, d or 1):
        age -= 1
    return max(age, 0)


def age_between(birth, death) -> int | None:
    if not death or death["date_y"] is None:
        return None
    on = date(death["date_y"], death["date_m"] or 12, death["date_d"] or 28)
    return age_on(birth, on)


def is_living(deceased_flag, birth, death, today: date) -> bool:
    """Living unless marked deceased, a death is recorded, or born more than
    110 years ago. A birth without a year never makes someone 'too old'."""
    if deceased_flag or death is not None:        # any death event means they died
        return False
    return not born_over_110(birth, today)


def born_over_110(birth, today: date) -> bool:
    """Born more than 110 years ago, to the day when the month is known."""
    if not birth or birth["date_y"] is None:
        return False
    y, m, d = birth["date_y"], birth["date_m"], birth["date_d"]
    if m is None:
        return today.year - y > 110
    return (today.year - 110, today.month, today.day) > (y, m, d or 1)


def years_label(birth, death, living: bool) -> str:
    by = birth["date_y"] if birth else None
    dy = death["date_y"] if death else None
    if living:
        return f"b. {by}" if by else ""
    if by or dy:
        return f"{by or '?'}–{dy or '?'}"
    return ""


def field_display(kind: str, value: str) -> str:
    """A custom field's stored value as shown: date fields in words, anything else as typed."""
    if kind == "date":
        try:
            return display(parse_text(value))
        except DateError:
            return value
    return value
