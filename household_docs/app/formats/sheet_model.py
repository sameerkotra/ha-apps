"""The sheet as the app holds it (SPEC §8): the JSON the editor and the server exchange, checked here, plus what
the .xlsx and .csv formats share — cell addresses, number formats, the functions the formula engine knows, the
conditional-colour palette and the text that goes into search.

    sheet = {"tabs": [tab, …]}                                   (≤ 10 tabs)
    tab   = {"name", "kind": "grid", "cells": {"A1": cell, …}, "cols": {"A": px}, "freeze": {"r", "c"},
             "totals": {"B": "SUM", …} | None, "cond": [rule, …], "charts": [chart, …]}
          | {"name", "kind": "chart", "chart": {"type", "source": {"tab", "range"}, "title"}}
    cell  = {"v": number | text | bool | "=formula", "q": 1 (text that starts with "=", kept as text),
             "c": the value the browser computed (number | text | bool | {"e": "#N/A"}),
             "f": "general|number|currency|percent|date|text", "d": decimals, "red": 1, "b": 1,
             "al": "left|center|right", "t": the text a .csv had (kept while the cell isn't changed),
             "x": 1 (a formula the app can't calculate: its value is Excel's)}
    rule  = {"range": "B2:B20", "type": "gt|lt|between|contains|before|after|top|bottom|dup",
             "a", "b", "n", "fill": colour | None, "text": colour | None}
    chart = {"id", "type": "bar|line|pie", "range": "A1:C6", "title"}
"""
import math
import re

MAX_TABS = 10
MAX_ROWS = 5000
MAX_COLS = 100
MAX_CELLS = 200_000
MAX_TEXT = 32767
MAX_FORMULA = 8192
INDEX_CELLS = 20_000

FORMATS = ("general", "number", "currency", "accounting", "percent", "scientific", "date", "time", "datetime", "text")
ALIGNS = ("left", "center", "right")
TOTALS = ("SUM", "AVERAGE", "COUNT", "MIN", "MAX")
CHART_TYPES = ("bar", "line", "pie")
RULE_TYPES = ("gt", "lt", "between", "contains", "before", "after", "top", "bottom", "dup")
ERRORS = ("#DIV/0!", "#REF!", "#NAME?", "#VALUE!", "#CYCLE!", "#N/A", "#NUM!")

# the functions static/sheetcalc.js knows (tests/test_sheets.py checks the two lists match)
KNOWN_FUNCTIONS = frozenset("""
SUM AVERAGE MIN MAX MEDIAN COUNT COUNTA ROUND ROUNDUP ROUNDDOWN ABS IF AND OR NOT IFERROR SUMIF COUNTIF AVERAGEIF
SUMIFS COUNTIFS AVERAGEIFS VLOOKUP HLOOKUP XLOOKUP INDEX MATCH LEN UPPER LOWER CONCAT CONCATENATE TEXT TODAY DAYS
DATE YEAR MONTH DAY EOMONTH NETWORKDAYS PMT
""".split())
# functions Excel stores with a prefix (newer than Excel 2007): written as _xlfn.NAME, read without it
XLFN = frozenset({"CONCAT", "DAYS", "XLOOKUP"})

# conditional colours: a name per colour (the app draws them from the theme; Excel gets these)
PALETTE = {"red": "F4C7C3", "amber": "FCE8B2", "green": "B7E1CD", "blue": "C6DAFC", "purple": "E1D5F5",
           "grey": "E0E0E0"}
TEXT_PALETTE = {"red": "C5221F", "amber": "B06000", "green": "137333", "blue": "1A56C4", "purple": "7B3FC4",
                "grey": "5F6368"}

CURRENCY_SYMBOLS = {"EUR": "€", "USD": "$", "GBP": "£", "JPY": "¥", "INR": "₹", "CNY": "¥", "KRW": "₩",
                    "CHF": "CHF", "AUD": "A$", "CAD": "C$", "NZD": "NZ$", "SEK": "kr", "NOK": "kr", "DKK": "kr",
                    "PLN": "zł", "CZK": "Kč", "HUF": "Ft", "BRL": "R$", "MXN": "MX$", "ZAR": "R", "RUB": "₽",
                    "TRY": "₺", "ILS": "₪", "SGD": "S$", "HKD": "HK$"}


class SheetError(ValueError):
    pass


# ---------------------------------------------------------------- addresses
def col_name(c: int) -> str:
    s = ""
    c += 1
    while c > 0:
        m = (c - 1) % 26
        s = chr(65 + m) + s
        c = (c - 1) // 26
    return s


def col_index(name: str) -> int:
    n = 0
    for ch in name.upper():
        n = n * 26 + (ord(ch) - 64)
    return n - 1


_REF = re.compile(r"^([A-Z]{1,3})([1-9][0-9]{0,6})$")


def parse_ref(ref: str):
    """"B4" → (col, row), 0-based; None if it isn't a cell address inside the app's limits."""
    m = _REF.match(ref or "")
    if not m:
        return None
    c, r = col_index(m.group(1)), int(m.group(2)) - 1
    if c >= MAX_COLS or r >= MAX_ROWS:
        return None
    return c, r


def addr(c: int, r: int) -> str:
    return f"{col_name(c)}{r + 1}"


def parse_range(text: str):
    """"A1:C6" → (c1, r1, c2, r2) inside the limits, else None."""
    parts = str(text or "").replace("$", "").upper().split(":")
    if len(parts) == 1:
        a = parse_ref(parts[0])
        return (a[0], a[1], a[0], a[1]) if a else None
    if len(parts) != 2:
        return None
    a, b = parse_ref(parts[0]), parse_ref(parts[1])
    if not a or not b:
        return None
    return min(a[0], b[0]), min(a[1], b[1]), max(a[0], b[0]), max(a[1], b[1])


def range_text(c1, r1, c2, r2) -> str:
    return addr(c1, r1) if (c1, r1) == (c2, r2) else f"{addr(c1, r1)}:{addr(c2, r2)}"


# ---------------------------------------------------------------- tab names (Excel's rules)
_BAD_TAB = re.compile(r"[:\\/?*\[\]]")


def check_tab_name(name) -> str:
    if not isinstance(name, str):
        raise SheetError("A tab needs a name.")
    n = name.strip()
    if not n:
        raise SheetError("A tab needs a name.")
    if len(n) > 31:
        raise SheetError("A tab name can be at most 31 characters.")
    if _BAD_TAB.search(n) or n.startswith("'") or n.endswith("'"):
        raise SheetError("A tab name can't contain : \\ / ? * [ ] or start or end with '.")
    if n.casefold() == "history":
        raise SheetError("“History” is a name Excel keeps for itself — choose another.")
    if any(ord(ch) < 32 for ch in n):
        raise SheetError("A tab name can't contain control characters.")
    return n


# ---------------------------------------------------------------- formulas
_STRING = re.compile(r'"(?:[^"]|"")*"')
_FUNC = re.compile(r"(?<![A-Za-z0-9_.])((?:_xlfn\.|_xlws\.)?[A-Za-z][A-Za-z0-9_.]*)\s*\(")


def _outside_strings(formula: str, fn):
    out, pos = [], 0
    for m in _STRING.finditer(formula):
        out.append(fn(formula[pos:m.start()]))
        out.append(m.group(0))
        pos = m.end()
    out.append(fn(formula[pos:]))
    return "".join(out)


def functions_in(formula: str) -> set[str]:
    names = set()
    _outside_strings(formula, lambda part: names.update(
        m.group(1).upper().replace("_XLFN.", "").replace("_XLWS.", "") for m in _FUNC.finditer(part)) or part)
    return names


def to_excel_formula(formula: str) -> str:
    """=CONCAT(…) → =_xlfn.CONCAT(…) (Excel shows #NAME? for these without the prefix)."""
    def fix(part):
        return _FUNC.sub(lambda m: ("_xlfn." if m.group(1).upper() in XLFN else "") + m.group(0), part)
    return _outside_strings(formula, fix)


def from_excel_formula(formula: str) -> str:
    return _outside_strings(formula, lambda part: re.sub(r"(?i)_xl(fn|ws)\.", "", part))


def can_calculate(formula: str, names: set[str] = frozenset()) -> bool:
    """Whether the app's engine can work this formula out: known functions, no structured references
    (Table[Col]), array constants or defined names."""
    if not functions_in(formula) <= KNOWN_FUNCTIONS:
        return False
    plain = _outside_strings(formula, lambda part: part)
    stripped = _STRING.sub('""', plain)
    if "[" in stripped or "{" in stripped:
        return False
    if names:
        words = {w.upper() for w in re.findall(r"(?<![A-Za-z0-9_.!'$])([A-Za-z_][A-Za-z0-9_.]*)(?![A-Za-z0-9_.!(])",
                                               stripped)}
        if words & {n.upper() for n in names}:
            return False
    return True


# ---------------------------------------------------------------- number formats
def currency_symbol(code: str | None) -> str:
    code = (code or "EUR").upper()
    return CURRENCY_SYMBOLS.get(code, code)


def excel_format(cell: dict, currency: str | None) -> str:
    """The Excel number format of a cell's format."""
    f = cell.get("f") or "general"
    d = cell.get("d")
    red = bool(cell.get("red"))

    def dec(default):
        n = default if d is None else max(0, min(10, int(d)))
        return ("." + "0" * n) if n else ""
    if f == "number":
        base = "#,##0" + dec(2)
    elif f in ("currency", "accounting"):
        sym = currency_symbol(currency).replace('"', "")
        base = f'"{sym}"#,##0' + dec(2) if len(sym) == 1 else f'#,##0{dec(2)} "{sym}"'
        if f == "accounting":                          # negatives in brackets
            return f"{base};({base})"
    elif f == "percent":
        base = "0" + dec(0) + "%"
    elif f == "scientific":
        base = "0" + dec(2) + "E+00"
    elif f == "date":
        return "yyyy-mm-dd"
    elif f == "time":
        return "hh:mm"
    elif f == "datetime":
        return "yyyy-mm-dd hh:mm"
    elif f == "text":
        return "@"
    else:
        return "General;[Red]-General" if red else "General"
    return f"{base};[Red]-{base}" if red else base


_DATE_TOKENS = re.compile(r"(?i)(y|d|mmm|h|s|am/pm)")


def format_from_excel(code: str | None) -> dict | None:
    """An Excel number format → the cell's format keys ({} for General), or None when the app has no such
    format (fractions, custom text …)."""
    code = (code or "General").strip()
    if code in ("General", ""):
        return {}
    if code.replace(" ", "") in ("General;[Red]-General", "General;[RED]-General"):
        return {"red": 1}
    if code == "@":
        return {"f": "text"}
    sections = code.split(";")
    first = sections[0]
    red = len(sections) > 1 and "[red]" in sections[1].lower()
    lits = re.sub(r'"[^"]*"|\\.|\[\$[^\]]*\]|\[[^\]]*\]', "", first)
    if _DATE_TOKENS.search(lits) or ("m" in lits.lower() and not re.search(r"[0#]", lits)):
        if re.search(r"(?i)[hs]|am/pm", lits):
            return {"f": "datetime"} if re.search(r"(?i)[yd]", lits) else {"f": "time"}
        return {"f": "date"}
    if re.search(r"[eE][+-]", lits):
        m = re.search(r"0(\.(0+))?[eE]", lits)
        return {"f": "scientific", "d": len(m.group(2) or "") if m else 2}
    if re.search(r"\?/|/\?", lits):
        return None
    m = re.search(r"[0#][0#,]*(\.([0#]+))?", lits)
    if not m:
        return None
    decimals = len(m.group(2) or "")
    out = {"d": decimals}
    if red:
        out["red"] = 1
    if "%" in lits:
        out["f"] = "percent"
        return out
    if re.search(r'"[^"]*[^\d\s,.#0"]+[^"]*"|\[\$[^\]]+\]|[$€£¥₹]', first):
        if len(sections) > 1 and not red and "(" in re.sub(r'"[^"]*"', "", sections[1]):
            out.pop("red", None)
            out["f"] = "accounting"                  # negatives in brackets
        else:
            out["f"] = "currency"
        return out
    out["f"] = "number"
    if "," not in m.group(0) and decimals == 0 and not red:
        return {"f": "number", "d": 0}
    return out


# ---------------------------------------------------------------- values
def num_text(n) -> str:
    if isinstance(n, bool):
        return "TRUE" if n else "FALSE"
    if isinstance(n, int):
        return str(n)
    if not math.isfinite(n):
        return "#NUM!"
    if n == int(n) and abs(n) < 1e15:
        return str(int(n))
    s = format(n, ".15g")
    if "e" in s:
        mant, exp = s.split("e")
        return f"{mant}E{'+' if int(exp) >= 0 else '-'}{abs(int(exp)):02d}"
    return s


def serial_to_iso(s) -> str:
    import datetime as dt
    s = int(math.floor(s))
    if s == 60:
        return "1900-02-29"
    d = dt.date(1899, 12, 30) + dt.timedelta(days=s + 1 if s < 60 else s)
    return d.isoformat()


def iso_to_serial(text: str):
    import datetime as dt
    m = re.match(r"^(\d{4})-(\d{1,2})-(\d{1,2})$", text.strip())
    if not m:
        return None
    try:
        d = dt.date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
    except ValueError:
        return None
    s = (d - dt.date(1899, 12, 30)).days
    return s - 1 if s < 61 else s


def shown(cell: dict) -> str:
    """What a cell shows, roughly (for search and CSV of values): text as it is, numbers plainly, dates as
    yyyy-mm-dd, formulas their computed value."""
    v = cell.get("v")
    if isinstance(v, str) and v.startswith("=") and not cell.get("q"):
        v = cell.get("c")
    if isinstance(v, dict):
        return str(v.get("e", ""))
    if v is None:
        return ""
    if isinstance(v, bool):
        return "TRUE" if v else "FALSE"
    if isinstance(v, (int, float)):
        if cell.get("f") == "date" and 0 <= v < 2958466:
            try:
                return serial_to_iso(v)
            except (ValueError, OverflowError):
                pass
        if cell.get("f") == "percent":
            return num_text(round(v * 100, 10)) + "%"
        if cell.get("f") in ("time", "datetime") and 0 <= v < 2958466:
            secs = round((v - math.floor(v)) * 86400)
            hm = f"{secs // 3600 % 24:02d}:{secs // 60 % 60:02d}" + (f":{secs % 60:02d}" if secs % 60 else "")
            return hm if cell["f"] == "time" else f"{serial_to_iso(v)} {hm}"
        return num_text(v)
    return str(v)


def is_formula(cell: dict) -> bool:
    v = cell.get("v")
    return isinstance(v, str) and len(v) > 1 and v.startswith("=") and not cell.get("q")


CONTENT_KEYS = ("v", "q", "f", "d", "red", "b", "al")


def content(cell: dict | None) -> tuple:
    """What a person changes in a cell (its value or formula and its format) — the per-cell conflict check
    compares this; computed values aren't part of it."""
    if not cell:
        return (None,) * len(CONTENT_KEYS)
    out = []
    for k in CONTENT_KEYS:
        v = cell.get(k)
        if k in ("q", "red", "b"):
            v = bool(v)
        if k == "f" and v == "general":
            v = None
        if isinstance(v, float) and v == int(v) and abs(v) < 1e15 and k == "v":
            v = int(v)
        out.append(v)
    return tuple(out)


def index_text(sheet: dict) -> tuple[str, list[str]]:
    """The searchable text of a sheet — one line per filled cell (what it shows, all tabs) — and, for each
    line, "Tab\\tC14" (search opens the sheet at that cell)."""
    lines, refs = [], []
    for tab in sheet.get("tabs", []):
        if tab.get("kind") != "grid":
            continue
        cells = tab.get("cells") or {}
        keyed = []
        for ref, cell in cells.items():
            p = parse_ref(ref)
            if p:
                keyed.append((p[1], p[0], ref, cell))
        keyed.sort()
        for _r, _c, ref, cell in keyed:
            text = shown(cell).replace("\n", " ").replace("\r", " ").strip()
            if not text:
                continue
            lines.append(text[:500])
            refs.append(f"{tab['name']}\t{ref}")
            if len(lines) >= INDEX_CELLS:
                return "\n".join(lines), refs
    return "\n".join(lines), refs


# ---------------------------------------------------------------- checking a sheet from the browser
def _value(v, what="value"):
    if v is None or isinstance(v, bool):
        return v
    if isinstance(v, (int, float)):
        if isinstance(v, float) and not math.isfinite(v):
            raise SheetError("A number in the sheet isn't finite.")
        return v
    if isinstance(v, str):
        if len(v) > MAX_TEXT:
            raise SheetError(f"A cell can hold at most {MAX_TEXT} characters.")
        return v
    if isinstance(v, dict) and what == "computed" and set(v) == {"e"} and v["e"] in ERRORS:
        return {"e": v["e"]}
    raise SheetError(f"A cell's {what} isn't a number, text or TRUE/FALSE.")


def clean_cell(cell) -> dict | None:
    if cell is None:
        return None
    if not isinstance(cell, dict):
        raise SheetError("A cell must be an object.")
    out = {}
    v = _value(cell.get("v"))
    if isinstance(v, str) and v.startswith("=") and not cell.get("q") and len(v) > MAX_FORMULA:
        raise SheetError(f"A formula can be at most {MAX_FORMULA} characters.")
    if v is not None:
        out["v"] = v
    if cell.get("q") and isinstance(v, str):
        out["q"] = 1
    if "c" in cell and cell["c"] is not None and is_formula(out):
        out["c"] = _value(cell["c"], "computed")
    f = cell.get("f")
    if f not in (None, "general"):
        if f not in FORMATS:
            raise SheetError("Unknown number format.")
        out["f"] = f
    d = cell.get("d")
    if d is not None:
        if not isinstance(d, int) or isinstance(d, bool) or not 0 <= d <= 10:
            raise SheetError("Decimals must be 0 to 10.")
        out["d"] = d
    for k in ("red", "b", "x"):
        if cell.get(k):
            out[k] = 1
    al = cell.get("al")
    if al is not None:
        if al not in ALIGNS:
            raise SheetError("Unknown alignment.")
        out["al"] = al
    t = cell.get("t")
    if isinstance(t, str) and len(t) <= 200 and isinstance(v, (int, float)) and not isinstance(v, bool):
        out["t"] = t
    if not out or set(out) <= {"x"}:
        return None
    return out


def _colour(c, palette):
    if c is None:
        return None
    if c not in palette:
        raise SheetError("Unknown colour.")
    return c


def clean_rule(rule) -> dict:
    if not isinstance(rule, dict):
        raise SheetError("A colour rule must be an object.")
    if parse_range(rule.get("range")) is None:
        raise SheetError("A colour rule needs a range like B2:B20.")
    t = rule.get("type")
    if t not in RULE_TYPES:
        raise SheetError("Unknown colour rule.")
    out = {"range": range_text(*parse_range(rule["range"])), "type": t,
           "fill": _colour(rule.get("fill"), PALETTE), "text": _colour(rule.get("text"), TEXT_PALETTE)}
    if not out["fill"] and not out["text"]:
        raise SheetError("A colour rule needs a fill or a text colour.")
    if t in ("gt", "lt", "between", "before", "after"):
        for k in ("a",) + (("b",) if t == "between" else ()):
            v = rule.get(k)
            if not isinstance(v, (int, float)) or isinstance(v, bool) or not math.isfinite(v):
                raise SheetError("A colour rule needs a number (or a date).")
            out[k] = v
    elif t == "contains":
        v = rule.get("a")
        if not isinstance(v, str) or not v or len(v) > 200:
            raise SheetError("“Text contains” needs the text.")
        out["a"] = v
    elif t in ("top", "bottom"):
        n = rule.get("n")
        if not isinstance(n, int) or isinstance(n, bool) or not 1 <= n <= 1000:
            raise SheetError("Top / bottom needs a number from 1 to 1000.")
        out["n"] = n
    return out


def clean_chart(ch, *, own_tab=False) -> dict:
    if not isinstance(ch, dict):
        raise SheetError("A chart must be an object.")
    t = ch.get("type")
    if t not in CHART_TYPES:
        raise SheetError("A chart is bar, line or pie.")
    title = ch.get("title") or ""
    if not isinstance(title, str) or len(title) > 100:
        raise SheetError("A chart title can be at most 100 characters.")
    if own_tab:
        src = ch.get("source")
        if not isinstance(src, dict) or not isinstance(src.get("tab"), str) or parse_range(src.get("range")) is None:
            raise SheetError("A chart tab needs the tab and range it shows.")
        return {"type": t, "title": title.strip(), "source": {"tab": src["tab"], "range": range_text(*parse_range(src["range"]))}}
    rng = parse_range(ch.get("range"))
    if rng is None or (rng[2] - rng[0] < 0):
        raise SheetError("A chart needs a range like A1:C6.")
    cid = ch.get("id") or ""
    if not isinstance(cid, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,20}", cid):
        raise SheetError("A chart needs an id.")
    return {"id": cid, "type": t, "title": title.strip(), "range": range_text(*rng)}


def clean_tab_meta(tab: dict, out: dict) -> None:
    cols = tab.get("cols") or {}
    if not isinstance(cols, dict) or len(cols) > MAX_COLS:
        raise SheetError("Column widths must be a list of columns.")
    out["cols"] = {}
    for k, v in cols.items():
        if not isinstance(k, str) or parse_ref(k + "1") is None:
            raise SheetError("Unknown column.")
        if not isinstance(v, (int, float)) or isinstance(v, bool) or not 24 <= v <= 1000:
            raise SheetError("A column is 24 to 1000 pixels wide.")
        out["cols"][k] = int(v)
    fr = tab.get("freeze") or {}
    if not isinstance(fr, dict):
        raise SheetError("Frozen rows and columns must be numbers.")
    r, c = fr.get("r", 0) or 0, fr.get("c", 0) or 0
    if not all(isinstance(x, int) and not isinstance(x, bool) for x in (r, c)) or not (0 <= r <= 50 and 0 <= c <= 20):
        raise SheetError("Freeze at most 50 rows and 20 columns.")
    out["freeze"] = {"r": r, "c": c}
    tot = tab.get("totals")
    if tot:
        if not isinstance(tot, dict) or len(tot) > MAX_COLS:
            raise SheetError("Totals must be per column.")
        out["totals"] = {}
        for k, v in tot.items():
            if not isinstance(k, str) or parse_ref(k + "1") is None or v not in TOTALS:
                raise SheetError("A total is SUM, AVERAGE, COUNT, MIN or MAX of a column.")
            out["totals"][k] = v
    else:
        out["totals"] = None
    cond = tab.get("cond") or []
    if not isinstance(cond, list) or len(cond) > 50:
        raise SheetError("At most 50 colour rules per tab.")
    out["cond"] = [clean_rule(x) for x in cond]
    charts = tab.get("charts") or []
    if not isinstance(charts, list) or len(charts) > 10:
        raise SheetError("At most 10 charts per tab.")
    out["charts"] = [clean_chart(x) for x in charts]
    if len({c["id"] for c in out["charts"]}) != len(out["charts"]):
        raise SheetError("Two charts have the same id.")


def clean_sheet(sheet) -> dict:
    """Check a whole sheet from the browser (limits SPEC §7.5) → a clean copy, or SheetError."""
    if not isinstance(sheet, dict) or not isinstance(sheet.get("tabs"), list):
        raise SheetError("Send the sheet's tabs.")
    tabs = sheet["tabs"]
    if not 1 <= len(tabs) <= MAX_TABS:
        raise SheetError(f"A sheet has 1 to {MAX_TABS} tabs.")
    out_tabs, names, total = [], set(), 0
    for tab in tabs:
        if not isinstance(tab, dict):
            raise SheetError("A tab must be an object.")
        name = check_tab_name(tab.get("name"))
        if name.casefold() in names:
            raise SheetError(f"Two tabs are called “{name}”.")
        names.add(name.casefold())
        kind = tab.get("kind") or "grid"
        if kind == "chart":
            out_tabs.append({"name": name, "kind": "chart", "chart": clean_chart(tab.get("chart"), own_tab=True)})
            continue
        if kind != "grid":
            raise SheetError("Unknown kind of tab.")
        cells = tab.get("cells") or {}
        if not isinstance(cells, dict):
            raise SheetError("A tab's cells must be an object.")
        clean = {}
        for ref, cell in cells.items():
            if parse_ref(ref) is None:
                raise SheetError(f"Cells go up to {MAX_ROWS:,} rows and {MAX_COLS} columns ({col_name(MAX_COLS - 1)}).")
            cc = clean_cell(cell)
            if cc is not None:
                clean[ref] = cc
        total += len(clean)
        if total > MAX_CELLS:
            raise SheetError(f"A sheet can have at most {MAX_CELLS:,} filled cells.")
        t = {"name": name, "kind": "grid", "cells": clean}
        clean_tab_meta(tab, t)
        out_tabs.append(t)
    if not any(t["kind"] == "grid" for t in out_tabs):
        raise SheetError("A sheet needs at least one tab with cells.")
    for t in out_tabs:
        if t["kind"] == "chart" and t["chart"]["source"]["tab"].casefold() not in names:
            raise SheetError("A chart tab shows a tab that isn't there.")
    return {"tabs": out_tabs}


def empty_sheet() -> dict:
    return {"tabs": [{"name": "Sheet1", "kind": "grid", "cells": {}, "cols": {}, "freeze": {"r": 0, "c": 0},
                      "totals": None, "cond": [], "charts": []}]}


def last_row(cells: dict) -> int:
    """The last filled row (0-based), -1 if none."""
    m = -1
    for ref in cells:
        p = parse_ref(ref)
        if p and p[1] > m:
            m = p[1]
    return m
