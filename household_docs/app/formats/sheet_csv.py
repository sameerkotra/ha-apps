""".csv sheets (SPEC §8.3): one tab of values and `=` formulas (Excel and LibreOffice read those as formulas).

Reading: UTF-8 (with or without a BOM) or UTF-16 with a BOM; the separator — comma, semicolon or tab — is
detected from the first lines (the one that splits them most evenly). Numbers become numbers only when
writing them back gives the same text (`007`, `1.50` and `1,5` stay as they were: the cell keeps the file's
text in `t` until it's changed); `yyyy-mm-dd` dates become dates. Writing: UTF-8 (a BOM if the file had one or
was UTF-16), the separator it had, "\\r\\n" line endings if the file used them, dates as yyyy-mm-dd, formulas as
"=…". Formats, widths, colours, charts and further tabs aren't kept in a .csv — the editor says so and offers
Convert to .xlsx.
"""
import csv
import io
import re

from . import sheet_model as M

SEPARATORS = (",", ";", "\t")
_NUM = re.compile(r"^-?(0|[1-9][0-9]*)(\.[0-9]*[1-9])?$")


class CsvError(ValueError):
    pass


def decode(data: bytes) -> tuple[str, dict]:
    if data.startswith(b"\xef\xbb\xbf"):
        return data[3:].decode("utf-8", "replace"), {"bom": True, "encoding": "utf-8"}
    if data.startswith((b"\xff\xfe", b"\xfe\xff")):
        return data.decode("utf-16"), {"bom": True, "encoding": "utf-16"}
    try:
        return data.decode("utf-8"), {"bom": False, "encoding": "utf-8"}
    except UnicodeDecodeError:
        return data.decode("cp1252", "replace"), {"bom": False, "encoding": "cp1252"}


def detect_separator(text: str) -> str:
    lines = [ln for ln in text.splitlines()[:20] if ln.strip()]
    if not lines:
        return ","
    best, score = ",", -1.0
    for sep in SEPARATORS:
        try:
            rows = list(csv.reader(io.StringIO("\n".join(lines)), delimiter=sep))
        except csv.Error:
            continue
        counts = [len(r) for r in rows]
        if max(counts) <= 1:
            continue
        common = max(set(counts), key=counts.count)
        s = counts.count(common) / len(counts) * 10 + min(common, 50) / 50
        if s > score:
            best, score = sep, s
    return best


def _cell_of(text: str) -> dict | None:
    if text == "":
        return None
    if text.startswith("=") and len(text) > 1:
        return {"v": text}
    if _NUM.match(text):
        n = float(text) if "." in text else int(text)
        if M.num_text(n) == text:              # only when writing it back gives the same text
            return {"v": n}
        return {"v": text}
    if re.match(r"^\d{4}-\d{2}-\d{2}$", text):
        s = M.iso_to_serial(text)
        if s is not None and M.serial_to_iso(s) == text:
            return {"v": s, "f": "date"}
    m = re.match(r"^(?:(\d{4}-\d{2}-\d{2}) )?(\d{2}):(\d{2})(?::(\d{2}))?$", text)
    if m:                                       # 17:30, 2026-10-08 17:30 (as the app writes them)
        day = M.iso_to_serial(m.group(1)) if m.group(1) else 0
        h, mi, se = int(m.group(2)), int(m.group(3)), int(m.group(4) or 0)
        if day is not None and h < 24 and mi < 60 and se < 60:
            cell = {"v": day + (h * 3600 + mi * 60 + se) / 86400, "f": "datetime" if m.group(1) else "time"}
            if M.shown(cell) == text:
                return cell
    if text in ("TRUE", "FALSE"):
        return {"v": text == "TRUE"}
    return {"v": text}


def read(data: bytes) -> dict:
    """{"sheet", "dialect": {"sep", "bom", "crlf"}}."""
    text, enc = decode(data)
    sep = detect_separator(text)
    crlf = "\r\n" in text[:20000]
    cells = {}
    rows = 0
    try:
        reader = csv.reader(io.StringIO(text, newline=""), delimiter=sep)
        for r, row in enumerate(reader):
            if r >= M.MAX_ROWS:
                if any(x for x in row):
                    raise CsvError(f"This .csv has more than {M.MAX_ROWS:,} rows, more than the app opens.")
                continue
            for c, value in enumerate(row):
                if value == "":
                    continue
                if c >= M.MAX_COLS:
                    raise CsvError(f"This .csv has more than {M.MAX_COLS} columns, more than the app opens.")
                cell = _cell_of(value)
                if cell is not None:
                    if len(value) > M.MAX_TEXT:
                        raise CsvError(f"A value in this .csv is longer than {M.MAX_TEXT} characters.")
                    cells[M.addr(c, r)] = cell
                    if len(cells) > M.MAX_CELLS:
                        raise CsvError(f"This .csv has more than {M.MAX_CELLS:,} filled cells, more than the app opens.")
            rows = r
    except csv.Error as e:
        raise CsvError(f"This .csv couldn't be read: {e}")
    sheet = M.empty_sheet()
    sheet["tabs"][0]["cells"] = cells
    return {"sheet": sheet, "dialect": {"sep": sep, "bom": enc["bom"] or enc["encoding"] == "utf-16",
                                       "crlf": crlf, "encoding": enc["encoding"]}, "rows": rows}


def text_of(cell: dict | None) -> str:
    if not cell:
        return ""
    v = cell.get("v")
    if v is None:
        return ""
    if isinstance(v, bool):
        return "TRUE" if v else "FALSE"
    if isinstance(v, (int, float)):
        if cell.get("t") is not None:
            return cell["t"]
        if cell.get("f") in ("date", "time", "datetime") and 0 <= v < 2958466:
            return M.shown(cell)
        return M.num_text(v)
    return str(v)


def write(sheet: dict, dialect: dict | None = None) -> bytes:
    """The first grid tab as .csv (formats and other tabs aren't kept)."""
    d = dialect or {}
    tab = next(t for t in sheet["tabs"] if t["kind"] == "grid")
    cells = tab.get("cells") or {}
    last_r, last_c = -1, -1
    for ref in cells:
        p = M.parse_ref(ref)
        if p:
            last_c, last_r = max(last_c, p[0]), max(last_r, p[1])
    buf = io.StringIO()
    w = csv.writer(buf, delimiter=d.get("sep") or ",", lineterminator="\r\n" if d.get("crlf") else "\n")
    for r in range(last_r + 1):
        row = [text_of(cells.get(M.addr(c, r))) for c in range(last_c + 1)]
        while row and row[-1] == "":
            row.pop()
        w.writerow(row)
    text = buf.getvalue()
    return (b"\xef\xbb\xbf" if d.get("bom") else b"") + text.encode("utf-8")


def export_tab(tab: dict, mode: str) -> list[list]:
    """A tab's rows for the CSV export, each value ("num" | "text" | "formula", value): "values" = what the
    cells show (formulas' results), "formulas" = formulas as typed. The caller guards text (common/csv_export)
    but never a formula it was asked for."""
    cells = tab.get("cells") or {}
    last_r, last_c = -1, -1
    for ref in cells:
        p = M.parse_ref(ref)
        if p:
            last_c, last_r = max(last_c, p[0]), max(last_r, p[1])
    rows = []
    for r in range(last_r + 1):
        row = []
        for c in range(last_c + 1):
            cell = cells.get(M.addr(c, r))
            if not cell:
                row.append(("text", ""))
                continue
            if mode == "formulas" and M.is_formula(cell):
                row.append(("formula", cell["v"]))
                continue
            v = cell.get("c") if M.is_formula(cell) else cell.get("v")
            if isinstance(v, (int, float)) and not isinstance(v, bool) and cell.get("f") not in ("date", "percent", "time", "datetime"):
                row.append(("num", v))
            else:
                row.append(("text", M.shown(cell)))
        rows.append(row)
    return rows
