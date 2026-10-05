"""Filter values for the Search page (SPEC §10.3, §10.5): dates in Home Assistant's time zone, sizes with units,
and the type groups. Each parser returns plain values for SQL (UTC ISO strings, byte counts, kinds and
extensions) or raises FilterError with a message for the chip.

Dates — `modified:` / `created:` (and the Modified / Created pickers):
  today · yesterday · 7d (today and the 6 days before) · 30d · Nd · thisweek · thismonth · lastmonth · thisyear ·
  lastyear · 2026 / 2026-03 / 2026-03-14 (that year, month or day) · >D (after D) · >=D (from D) · <D (before D)
  · <=D (up to and including D) · A..B (from A up to and including B; either side may be left out).
  "Today" is today in Home Assistant's time zone; stored times are UTC, so the bounds are converted.
Sizes — `size:` (and the Size picker): >5mb · <100kb · 1mb..10mb · 5mb (5 MB or more, under 6 MB) · units b, kb,
  mb, gb (1024-based), decimals allowed; folders have no size and never match a size filter.
"""
import re
from datetime import date, datetime, time, timedelta, timezone

from .. import config


class FilterError(ValueError):
    pass


# ---------- types ----------
TYPE_GROUPS = {
    "note": {"kinds": ("note",)},
    "markdown": {"kinds": ("markdown",)},
    "checklist": {"kinds": ("checklist",)},
    "sheet": {"kinds": ("sheet",)},
    "folder": {"kinds": ("folder",)},
    "image": {"exts": ("png", "jpg", "jpeg", "gif", "webp", "heic", "heif", "bmp", "tif", "tiff", "svg", "avif")},
    "pdf": {"exts": ("pdf",)},
    "spreadsheet": {"exts": ("xlsx", "xls", "xlsm", "ods", "csv", "tsv", "numbers")},
    "document": {"exts": ("doc", "docx", "odt", "rtf", "pages", "ppt", "pptx", "odp", "key", "epub")},
    "archive": {"exts": ("zip", "7z", "rar", "tar", "gz", "tgz", "bz2", "xz", "zst")},
    "video": {"exts": ("mp4", "mov", "mkv", "avi", "webm", "m4v", "wmv", "mpg", "mpeg", "3gp")},
    "audio": {"exts": ("mp3", "m4a", "wav", "flac", "ogg", "aac", "opus", "wma", "aiff")},
}
TYPE_LABELS = {"note": "Note", "markdown": "Markdown", "checklist": "Checklist", "sheet": "Sheet", "folder": "Folder",
               "image": "Image", "pdf": "PDF", "spreadsheet": "Spreadsheet", "document": "Document",
               "archive": "Archive", "video": "Video", "audio": "Audio", "other": "Other"}
OTHER_EXTS = tuple(sorted({e for g in TYPE_GROUPS.values() for e in g.get("exts", ())}))
TYPE_ALIASES = {"notes": "note", "md": "markdown", "list": "checklist", "checklists": "checklist", "sheets": "sheet",
                "folders": "folder", "dir": "folder", "images": "image", "photo": "image", "photos": "image",
                "picture": "image", "pdfs": "pdf", "spreadsheets": "spreadsheet", "doc": "document",
                "documents": "document", "zip": "archive", "archives": "archive", "videos": "video", "music": "audio"}


def type_name(value: str) -> str:
    v = (value or "").strip().lower()
    v = TYPE_ALIASES.get(v, v)
    if v not in TYPE_LABELS:
        raise FilterError(f"Unknown type “{value}” — try note, checklist, sheet, folder, image, pdf, spreadsheet, "
                          "document, archive, video, audio or other.")
    return v


def type_label_of(kind: str, ext: str | None) -> str:
    if kind in ("note", "markdown", "checklist", "sheet", "folder"):
        return TYPE_LABELS[kind]
    for g, spec in TYPE_GROUPS.items():
        if ext and ext in spec.get("exts", ()):
            return TYPE_LABELS[g]
    return TYPE_LABELS["other"]


def clean_ext(value: str) -> str:
    v = (value or "").strip().lower().lstrip(".")
    if not v or len(v) > 16 or not re.fullmatch(r"[a-z0-9_+-]+", v):
        raise FilterError(f"“{value}” isn't a file extension.")
    return v


# ---------- sizes ----------
_UNITS = {"": 1, "b": 1, "k": 1024, "kb": 1024, "m": 1024 ** 2, "mb": 1024 ** 2, "g": 1024 ** 3, "gb": 1024 ** 3,
          "t": 1024 ** 4, "tb": 1024 ** 4}
_SIZE = re.compile(r"^\s*(\d+(?:[.,]\d+)?)\s*([kmgt]?b?)\s*$", re.I)
SIZE_PRESETS = {"tiny": (None, 100 * 1024), "small": (100 * 1024, 1024 ** 2), "medium": (1024 ** 2, 10 * 1024 ** 2),
                "large": (10 * 1024 ** 2, 100 * 1024 ** 2), "huge": (100 * 1024 ** 2, None)}


def parse_amount(text: str) -> tuple[int, int]:
    """"5mb" → (5 MiB in bytes, the unit's size)."""
    m = _SIZE.match(text or "")
    if not m:
        raise FilterError(f"“{text}” isn't a size — try 500kb, 5mb or 1.5gb.")
    unit = _UNITS[m.group(2).lower()]
    return int(float(m.group(1).replace(",", ".")) * unit), unit


def parse_size(value: str) -> tuple[int | None, int | None]:
    """A size filter → (at least, under) in bytes; either may be None."""
    v = (value or "").strip().lower().replace(" ", "")
    if v in SIZE_PRESETS:
        return SIZE_PRESETS[v]
    if ".." in v:
        a, _, b = v.partition("..")
        lo = parse_amount(a)[0] if a else None
        hi = parse_amount(b)[0] if b else None
        if lo is not None and hi is not None and lo > hi:
            raise FilterError("The smaller size comes first (1mb..10mb).")
        return lo, (hi + 1 if hi is not None else None)
    for op in (">=", "<=", ">", "<"):
        if v.startswith(op):
            n = parse_amount(v[len(op):])[0]
            return {">=": (n, None), ">": (n + 1, None), "<=": (None, n + 1), "<": (None, n)}[op]
    n, unit = parse_amount(v)
    return n, n + unit


# ---------- dates ----------
_DAY = re.compile(r"^(\d{4})(?:-(\d{1,2})(?:-(\d{1,2}))?)?$")
_REL = re.compile(r"^(\d{1,4})\s*(d|days?|w|weeks?|m|months?|y|years?)$")


def _tz():
    return config.ZONE.tz or timezone.utc


def _local_midnight(d: date) -> datetime:
    return datetime.combine(d, time(0, 0), tzinfo=_tz())


def _iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat(timespec="seconds")


def _add_months(d: date, n: int) -> date:
    y, m = divmod(d.month - 1 + n, 12)
    return date(d.year + y, m + 1, 1)


def _period(text: str) -> tuple[date, date]:
    """"2026" / "2026-03" / "2026-03-14" → [first day, day after the last)."""
    m = _DAY.match(text.strip())
    if not m:
        raise FilterError(f"“{text}” isn't a date — use 2026-09-01 (or 2026-09, or 2026).")
    y, mo, d = int(m.group(1)), m.group(2), m.group(3)
    try:
        if d:
            start = date(y, int(mo), int(d))
            return start, start + timedelta(days=1)
        if mo:
            start = date(y, int(mo), 1)
            return start, _add_months(start, 1)
        return date(y, 1, 1), date(y + 1, 1, 1)
    except ValueError:
        raise FilterError(f"“{text}” isn't a date.") from None


def date_range(value: str, now: datetime | None = None) -> tuple[str | None, str | None]:
    """A date filter → (from, until) as stored UTC ISO strings: from ≤ time < until; either may be None."""
    v = (value or "").strip().lower().replace(" ", "")
    if not v:
        raise FilterError("Choose a date.")
    now = (now or config.utcnow()).astimezone(_tz())
    today = now.date()
    if v == "today":
        return _iso(_local_midnight(today)), None
    if v == "yesterday":
        return _iso(_local_midnight(today - timedelta(days=1))), _iso(_local_midnight(today))
    if v in ("thisweek", "week"):
        return _iso(_local_midnight(today - timedelta(days=today.weekday()))), None
    if v in ("lastweek",):
        start = today - timedelta(days=today.weekday() + 7)
        return _iso(_local_midnight(start)), _iso(_local_midnight(start + timedelta(days=7)))
    if v in ("thismonth", "month"):
        return _iso(_local_midnight(today.replace(day=1))), None
    if v == "lastmonth":
        first = today.replace(day=1)
        return _iso(_local_midnight(_add_months(first, -1))), _iso(_local_midnight(first))
    if v in ("thisyear", "year"):
        return _iso(_local_midnight(date(today.year, 1, 1))), None
    if v == "lastyear":
        return _iso(_local_midnight(date(today.year - 1, 1, 1))), _iso(_local_midnight(date(today.year, 1, 1)))
    m = _REL.match(v)
    if m:
        n, unit = int(m.group(1)), m.group(2)[0]
        if n < 1:
            raise FilterError("Use 1 or more (7d = today and the 6 days before).")
        if unit == "d":
            start = today - timedelta(days=n - 1)
        elif unit == "w":
            start = today - timedelta(days=7 * n - 1)
        elif unit == "m":                                   # 1m = the last 30 days
            start = today - timedelta(days=30 * n - 1)
        else:
            start = today - timedelta(days=365 * n - 1)
        return _iso(_local_midnight(start)), None
    if ".." in v:
        a, _, b = v.partition("..")
        lo = _iso(_local_midnight(_period(a)[0])) if a else None
        hi = _iso(_local_midnight(_period(b)[1])) if b else None
        if lo and hi and lo >= hi:
            raise FilterError("The earlier date comes first (2026-01-01..2026-03-31).")
        return lo, hi
    for op in (">=", "<=", ">", "<"):
        if v.startswith(op):
            start, end = _period(v[len(op):])
            return {">=": (_iso(_local_midnight(start)), None), ">": (_iso(_local_midnight(end)), None),
                    "<=": (None, _iso(_local_midnight(end))), "<": (None, _iso(_local_midnight(start)))}[op]
    start, end = _period(v)
    return _iso(_local_midnight(start)), _iso(_local_midnight(end))


def date_label(value: str) -> str:
    v = (value or "").strip().lower()
    names = {"today": "today", "yesterday": "yesterday", "7d": "in the last 7 days", "30d": "in the last 30 days",
             "thisweek": "this week", "lastweek": "last week", "thismonth": "this month", "lastmonth": "last month",
             "thisyear": "this year", "lastyear": "last year"}
    if v in names:
        return names[v]
    if v.startswith(">="):
        return "from " + v[2:]
    if v.startswith("<="):
        return "up to " + v[2:]
    if v.startswith(">"):
        return "after " + v[1:]
    if v.startswith("<"):
        return "before " + v[1:]
    if ".." in v:
        a, _, b = v.partition("..")
        return f"{a or '…'} to {b or '…'}"
    m = _REL.match(v)
    if m:
        return f"in the last {v}"
    return "in " + v


def size_label(value: str) -> str:
    v = (value or "").strip().lower()
    names = {"tiny": "under 100 KB", "small": "100 KB – 1 MB", "medium": "1–10 MB", "large": "10–100 MB",
             "huge": "over 100 MB"}
    return names.get(v, v)


def today_bounds(now: datetime | None = None) -> tuple[str, str]:
    """(start of today, start of this week) as UTC ISO strings — the Modified groups."""
    now = (now or config.utcnow()).astimezone(_tz())
    today = now.date()
    return _iso(_local_midnight(today)), _iso(_local_midnight(today - timedelta(days=today.weekday())))
