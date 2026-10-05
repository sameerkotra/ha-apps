"""CSV downloads for spreadsheets (shared by the household apps: common/python/csv_export.py, copied
into each app's app/common/ by tools/sync_common.py). Standard library only.

Python's csv module, its default dialect: comma-separated, "\\r\\n" line endings, quotes only
where needed. With `bom=True` the text starts with a UTF-8 byte-order mark so spreadsheet apps read
accents correctly. Each app keeps its own columns, headers, number formats, file names and responses.

The formula-injection guard: a spreadsheet runs a cell that starts with = + - @ (or a tab or
carriage return) as a formula, so text that someone typed (a description, a name, a note) gets a
leading ' when it starts with one of those. Apply it to text cells only — never to numbers, which
must stay numbers in the spreadsheet (an amount like "-12.50" is written as it is).

    csv_export.guard("=SUM(A1)")        # "'=SUM(A1)"; non-text values are returned unchanged
    csv_export.text(None)               # "" (a value made text, then guarded)
    csv_export.to_text(header, rows, bom=True)      # the whole file as str
    csv_export.to_bytes(header, rows, bom=False)    # … encoded as UTF-8
    StreamingResponse(csv_export.stream(header, rows), …)   # one header line, then one line per row
"""
import csv
import io

FORMULA_TRIGGERS = ("=", "+", "-", "@", "\t", "\r")
BOM = "﻿"


def guard(value):
    """Text starting with = + - @ tab or CR gets a leading '; anything else is returned as it is."""
    if isinstance(value, str) and value.startswith(FORMULA_TRIGGERS):
        return "'" + value
    return value


def text(value) -> str:
    """A text cell: "" for None, else str(value), guarded."""
    return guard("" if value is None else str(value))


def _line(row) -> str:
    buf = io.StringIO()
    csv.writer(buf).writerow(row)
    return buf.getvalue()


def stream(header, rows, *, bom: bool = False):
    """The file one line at a time (str): the header (if not None), then each row."""
    first = BOM if bom else ""
    if header is not None:
        yield first + _line(header)
        first = ""
    for row in rows:
        yield first + _line(row)
        first = ""
    if first:
        yield first


def to_text(header, rows, *, bom: bool = False) -> str:
    return "".join(stream(header, rows, bom=bom))


def to_bytes(header, rows, *, bom: bool = False) -> bytes:
    return to_text(header, rows, bom=bom).encode("utf-8")
