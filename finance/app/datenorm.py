"""Date normalisation to ISO "YYYY-MM-DD". Every stored date must be ISO,
because date filters and ORDER BY compare the strings. Used by the shared
manual/CSV insert path and by db.py's startup clean-up of older rows.
"""
from datetime import datetime

# Deliberately small and unambiguous. Day-first formats (DD/MM/YYYY) are
# NOT included -- for most dates that's indistinguishable from MM/DD/YYYY
# (e.g. "03/04/2026" could mean either), and silently guessing wrong would
# be worse than just rejecting an unrecognized format outright, consistent
# with this app's "never invent state the user didn't ask for" rule
# elsewhere (account-name matching, CSV column validation, ...).
_DATE_FORMATS = ("%Y-%m-%d", "%m/%d/%Y", "%Y/%m/%d")


def normalize_date(raw: str | None) -> str | None:
    """Best-effort parse of a date string into canonical ISO
    "YYYY-MM-DD", trying each accepted format in turn. Returns None for
    a blank string or one that matches none of them -- the caller
    decides what a failed parse means (a required-field error for a
    blank string, an unrecognized-format error otherwise)."""
    raw = (raw or "").strip()
    if not raw:
        return None
    for fmt in _DATE_FORMATS:
        try:
            return datetime.strptime(raw, fmt).date().isoformat()
        except ValueError:
            continue
    return None
