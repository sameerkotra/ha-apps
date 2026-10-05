"""Reads an untrusted .xlsx in a separate, time-limited process (SPEC §3.4 risk 5, §8.4).

Run as `python -I xlsx_worker.py '<limits JSON>'` with the file's bytes on stdin; writes one JSON object to
stdout. It needs only openpyxl (which parses every XML part with defusedxml when it's installed — entity
expansion and external entities are refused) and the standard library, and it limits itself before reading
anything: address space, CPU time, no files written. The app also kills it after a wall-clock limit.

Checks, in order:
1. the zip: at most `parts` entries and `unzipped` bytes in total (the central directory's sizes, which the
   zip reader also enforces while reading — a part can't inflate past its declared size), no encrypted parts;
2. the parts present: macros, pivot tables, images, comments, external links, Excel tables, charts and other
   parts the app doesn't know are listed as features;
3. a streaming pass (openpyxl read-only mode) counts the filled cells, rows and columns against the caps and
   collects each cell's cached value — before anything is loaded whole;
4. the full load: values, formulas, number formats, bold, alignment, widths, frozen panes, conditional
   formatting, charts, merged cells, data validation, hidden tabs, defined names, other formatting.
The app (formats/sheet_xlsx.py) turns this into a sheet and decides whether it can be edited.
"""
import io
import json
import sys

try:
    import resource
except ImportError:      # not on Linux
    resource = None

DEFAULT_LIMITS = {"unzipped": 64 * 1024 * 1024, "parts": 4000, "cells": 200_000, "rows": 5000, "cols": 100,
                  "tabs": 10, "mem": 1024 * 1024 * 1024, "cpu": 30}
EPOCH_ORDINAL = 693594          # date(1899, 12, 30).toordinal()


def _limit_self(lim: dict) -> None:
    if resource is None:
        return
    for what, value in ((getattr(resource, "RLIMIT_AS", None), lim["mem"]),
                        (getattr(resource, "RLIMIT_CPU", None), lim["cpu"]),
                        (getattr(resource, "RLIMIT_FSIZE", None), 0),
                        (getattr(resource, "RLIMIT_NOFILE", None), 64)):
        if what is None:
            continue
        try:
            resource.setrlimit(what, (value, value))
        except (ValueError, OSError):
            pass


class Refused(Exception):
    pass


KNOWN_PARTS = ("[Content_Types].xml", "_rels/", "docProps/", "xl/workbook.xml", "xl/_rels/", "xl/styles.xml",
               "xl/sharedStrings.xml", "xl/theme/", "xl/worksheets/", "xl/chartsheets/", "xl/calcChain.xml",
               "xl/printerSettings/", "xl/charts/", "xl/drawings/", "customXml/", "xl/metadata.xml")


def check_zip(data: bytes, lim: dict):
    import zipfile
    try:
        z = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile:
        raise Refused("This isn't an .xlsx file (it isn't a zip).")
    infos = z.infolist()
    if len(infos) > lim["parts"]:
        raise Refused("This .xlsx has too many parts to open safely.")
    total = 0
    for i in infos:
        if i.flag_bits & 0x1:
            raise Refused("This .xlsx is password-protected (encrypted); the app can't open it.")
        total += i.file_size
        if total > lim["unzipped"]:
            raise Refused(f"This .xlsx unpacks to more than {lim['unzipped'] // (1024 * 1024)} MB, so it isn't "
                          "opened (it could be a zip bomb).")
    names = [i.filename for i in infos]
    if "xl/workbook.xml" not in names:
        if any(n.endswith("EncryptionInfo") for n in names) or not names:
            raise Refused("This .xlsx is password-protected (encrypted); the app can't open it.")
        raise Refused("This isn't an .xlsx workbook.")
    for i in infos:          # no DTDs or entities anywhere (defusedxml refuses them too; this says why, early)
        if i.filename.lower().endswith((".xml", ".rels", ".vml")) and i.file_size:
            with z.open(i) as f:
                head = f.read(65536)
            if b"<!DOCTYPE" in head or b"<!ENTITY" in head:
                raise Refused("This .xlsx contains XML the app refuses for safety (entities or a DTD).")
    features = {}
    ct = z.read("[Content_Types].xml") if "[Content_Types].xml" in names else b""
    if any(n.lower().endswith("vbaproject.bin") for n in names) or b"macroEnabled" in ct:
        features["macros"] = "Macros (VBA)"
    if any(n.startswith(("xl/pivotTables/", "xl/pivotCache/")) for n in names):
        features["pivots"] = "Pivot tables"
    if any(n.startswith("xl/media/") for n in names):
        features["images"] = "Pictures"
    if any(n.startswith(("xl/comments", "xl/threadedComments/", "xl/persons/")) or "/comments" in n
           for n in names if n.startswith("xl/")):
        features["comments"] = "Comments or notes"
    if any(n.startswith("xl/externalLinks/") for n in names):
        features["links"] = "Links to other workbooks"
    if any(n.startswith("xl/tables/") for n in names):
        features["tables"] = "Excel tables"
    if any(n.startswith(("xl/activeX/", "xl/ctrlProps/", "xl/embeddings/")) for n in names):
        features["controls"] = "Form controls or embedded objects"
    if any(n.startswith(("xl/slicers/", "xl/slicerCaches/", "xl/timelines/", "xl/timelineCaches/")) for n in names):
        features["slicers"] = "Slicers or timelines"
    for n in names:
        if n.endswith("/") or n.startswith(KNOWN_PARTS):
            continue
        if any(n.startswith(p) for p in ("xl/pivot", "xl/media/", "xl/comments", "xl/threadedComments/", "xl/persons/",
                                          "xl/externalLinks/", "xl/tables/", "xl/activeX/", "xl/ctrlProps/",
                                          "xl/embeddings/", "xl/slicer", "xl/timeline")) or n.lower().endswith("vbaproject.bin"):
            continue
        features.setdefault("other", "Other Excel features")
    chart_parts = sum(1 for n in names if n.startswith("xl/charts/chart") and n.endswith(".xml"))
    if chart_parts and any(n.startswith("xl/drawings/") and n.endswith(".xml") for n in names):
        for n in names:
            if n.startswith("xl/drawings/") and n.endswith(".xml") and "/_rels/" not in n:
                if b":pic>" in z.read(n) or b"<pic>" in z.read(n):
                    features["images"] = "Pictures"
    if b"/cellimages" in ct.lower():
        features["images"] = "Pictures"
    return features, chart_parts


def serial_of(v):
    import datetime as dt
    if isinstance(v, dt.datetime):
        days = v.date().toordinal() - EPOCH_ORDINAL
        frac = (v.hour * 3600 + v.minute * 60 + v.second + v.microsecond / 1e6) / 86400
        s = days + frac
    elif isinstance(v, dt.date):
        s = v.toordinal() - EPOCH_ORDINAL
    elif isinstance(v, dt.time):
        return (v.hour * 3600 + v.minute * 60 + v.second) / 86400
    elif isinstance(v, dt.timedelta):
        return v.total_seconds() / 86400
    else:
        return None
    if s < 61:
        s -= 1                  # Excel's 29 Feb 1900
    return s


def plain(v):
    """A JSON-able cell value: number, str, bool, None, {"e": code} for errors, {"d": serial} for dates."""
    import datetime as dt
    if v is None or isinstance(v, (bool, str)):
        return v
    if isinstance(v, (int, float)):
        if v != v or v in (float("inf"), float("-inf")):
            return {"e": "#NUM!"}
        return v
    if isinstance(v, (dt.datetime, dt.date, dt.time, dt.timedelta)):
        return {"d": serial_of(v)}
    return str(v)


ERRORS = {"#NULL!", "#DIV/0!", "#VALUE!", "#REF!", "#NAME?", "#NUM!", "#N/A", "#GETTING_DATA"}


def first_pass(data: bytes, lim: dict):
    """Count cells and collect cached values (read-only mode streams the sheets)."""
    from openpyxl import load_workbook
    wb = load_workbook(io.BytesIO(data), read_only=True, data_only=True, keep_links=False)
    try:
        if len(wb.sheetnames) > lim["tabs"]:
            raise Refused(f"This workbook has {len(wb.sheetnames)} tabs; the app opens at most {lim['tabs']}.")
        total = 0
        cached = {}
        for ws in wb.worksheets:
            got = {}
            for row in ws.iter_rows():
                for c in row:
                    v = getattr(c, "value", None)
                    if v is None:
                        continue
                    r, col = c.row, c.column
                    if r > lim["rows"] or col > lim["cols"]:
                        raise Refused(f"“{ws.title}” goes past {lim['rows']:,} rows or {lim['cols']} columns, more "
                                      "than the app opens.")
                    total += 1
                    if total > lim["cells"]:
                        raise Refused(f"This workbook has more than {lim['cells']:,} filled cells, more than the "
                                      "app opens.")
                    if isinstance(v, str) and v in ERRORS:
                        got[c.coordinate] = {"e": v}
                    else:
                        got[c.coordinate] = plain(v)
            cached[ws.title] = got
        return cached
    finally:
        wb.close()


def _rgb(color):
    try:
        if color is None:
            return None
        if color.type == "rgb" and isinstance(color.rgb, str):
            return color.rgb[-6:].upper()
        if color.type == "theme":
            return "theme:%s" % color.theme
        if color.type == "indexed":
            return "indexed:%s" % color.indexed
    except Exception:
        return None
    return None


def _styled(cell) -> bool:
    """Formatting the app can't keep: a fill, borders, a font other than plain/bold, rotation, wrapping …"""
    try:
        if not cell.has_style:
            return False
        f = cell.fill
        if f is not None and f.fill_type not in (None, "none"):
            return True
        b = cell.border
        for side in (b.left, b.right, b.top, b.bottom, b.diagonal):
            if side is not None and side.style:
                return True
        ft = cell.font
        if ft is not None and (ft.i or ft.u or ft.strike or (ft.color is not None and _rgb(ft.color) not in
                                                             (None, "000000", "theme:1", "indexed:64"))):
            return True
        a = cell.alignment
        if a is not None and (a.textRotation or a.indent or (a.vertical not in (None, "bottom"))):
            return True
    except Exception:
        return True
    return False


def second_pass(data: bytes, lim: dict, cached: dict):
    from openpyxl import load_workbook
    from openpyxl.worksheet.formula import ArrayFormula, DataTableFormula
    wb = load_workbook(io.BytesIO(data), data_only=False, keep_links=False, rich_text=False)
    out = {"props": {}, "names": [], "sheets": []}
    try:
        for p in getattr(wb, "custom_doc_props", None) or []:
            try:
                out["props"][str(p.name)] = str(p.value)
            except Exception:
                pass
    except Exception:
        pass
    try:
        dn = wb.defined_names
        items = dn.items() if hasattr(dn, "items") else [(d.name, d) for d in dn.definedName]
        for name, d in items:
            if not str(name).startswith("_xlnm."):
                out["names"].append(str(name))
        for ws in wb.worksheets:
            for name in getattr(ws, "defined_names", {}) or {}:
                if not str(name).startswith("_xlnm."):
                    out["names"].append(str(name))
    except Exception:
        pass
    from openpyxl.chartsheet import Chartsheet
    for sh in wb._sheets:
        if isinstance(sh, Chartsheet):
            out["sheets"].append({"kind": "chart", "title": sh.title, "state": sh.sheet_state,
                                  "charts": len(getattr(sh, "_charts", []))})
            continue
        ws = sh
        cells = []
        styled = 0
        special = 0
        cache = cached.get(ws.title, {})
        for (r, c), cell in ws._cells.items():
            v = cell.value
            if r > lim["rows"] or c > lim["cols"]:
                continue
            fmt = cell.number_format if cell.has_style else "General"
            bold = bool(cell.has_style and cell.font is not None and cell.font.b)
            align = cell.alignment.horizontal if cell.has_style and cell.alignment is not None else None
            quote = bool(cell.has_style and getattr(cell, "quotePrefix", False))
            if _styled(cell):
                styled += 1
            if v is None:
                if fmt != "General" or bold or align:
                    cells.append([cell.coordinate, None, "n", fmt, bold, align, quote, None])
                continue
            if isinstance(v, (ArrayFormula, DataTableFormula)):
                special += 1
                text = getattr(v, "text", None) or ""
                cells.append([cell.coordinate, text if isinstance(text, str) else "", "f", fmt, bold, align, quote,
                              cache.get(cell.coordinate)])
                continue
            if cell.data_type == "f":
                cells.append([cell.coordinate, str(v), "f", fmt, bold, align, quote, cache.get(cell.coordinate)])
            elif cell.data_type == "e":
                cells.append([cell.coordinate, {"e": str(v)}, "e", fmt, bold, align, quote, None])
            else:
                cells.append([cell.coordinate, plain(v), cell.data_type, fmt, bold, align, quote, None])
        cols = {}
        for letter, dim in ws.column_dimensions.items():
            try:
                if dim.customWidth and dim.width:
                    lo, hi = dim.min or None, dim.max or None
                    cols[letter] = [float(dim.width), lo, hi]
            except Exception:
                pass
        cf = []
        try:
            for rng in ws.conditional_formatting:
                for rule in rng.rules:
                    d = rule.dxf
                    fill = _rgb(d.fill.fgColor) or _rgb(d.fill.bgColor) if d is not None and d.fill is not None else None
                    font = _rgb(d.font.color) if d is not None and d.font is not None else None
                    cf.append({"sqref": str(rng.sqref), "type": rule.type, "operator": rule.operator,
                               "formula": list(rule.formula or []), "text": rule.text, "rank": rule.rank,
                               "bottom": bool(rule.bottom), "percent": bool(rule.percent),
                               "timePeriod": rule.timePeriod, "fill": fill, "font": font})
        except Exception:
            cf.append({"type": "unreadable"})
        dv = 0
        try:
            dv = len(ws.data_validations.dataValidation)
        except Exception:
            pass
        heights = 0
        try:
            heights = sum(1 for _r, d in ws.row_dimensions.items() if d.hidden)
        except Exception:
            pass
        hidden_cols = sum(1 for _l, d in ws.column_dimensions.items() if getattr(d, "hidden", False))
        out["sheets"].append({
            "kind": "grid", "title": ws.title, "state": ws.sheet_state, "cells": cells, "cols": cols,
            "freeze": ws.freeze_panes, "merged": len(ws.merged_cells.ranges), "validations": dv, "cf": cf,
            "charts": len(getattr(ws, "_charts", [])), "images": len(getattr(ws, "_images", [])),
            "styled": styled, "special": special, "hiddenRows": heights, "hiddenCols": hidden_cols,
            "autofilter": bool(ws.auto_filter and ws.auto_filter.ref), "protected": bool(ws.protection.sheet)})
    return out


MAIN_TYPE = b"application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"
OTHER_MAIN_TYPES = (b"application/vnd.ms-excel.sheet.macroEnabled.main+xml",
                    b"application/vnd.openxmlformats-officedocument.spreadsheetml.template.main+xml",
                    b"application/vnd.ms-excel.template.macroEnabled.main+xml")


def as_plain_workbook(data: bytes) -> bytes:
    """A macro-enabled workbook or a template, relabelled (in memory only) so openpyxl reads its cells; the
    macros are listed as a feature and the file opens read only. Nothing is ever written back."""
    import zipfile
    z = zipfile.ZipFile(io.BytesIO(data))
    ct = z.read("[Content_Types].xml")
    if not any(t in ct for t in OTHER_MAIN_TYPES):
        return data
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w", zipfile.ZIP_STORED) as w:
        for info in z.infolist():
            raw = z.read(info.filename)
            if info.filename == "[Content_Types].xml":
                for t in OTHER_MAIN_TYPES:
                    raw = raw.replace(t, MAIN_TYPE)
            w.writestr(info.filename, raw)
    return out.getvalue()


def run(data: bytes, lim: dict) -> dict:
    features, chart_parts = check_zip(data, lim)
    data = as_plain_workbook(data)
    cached = first_pass(data, lim)
    out = second_pass(data, lim, cached)
    out["features"] = features
    out["chartParts"] = chart_parts
    out["ok"] = True
    return out


def main() -> None:
    lim = dict(DEFAULT_LIMITS)
    if len(sys.argv) > 1:
        lim.update(json.loads(sys.argv[1]))
    _limit_self(lim)
    data = sys.stdin.buffer.read()
    try:
        out = run(data, lim)
    except Refused as e:
        out = {"ok": False, "error": str(e)}
    except MemoryError:
        out = {"ok": False, "error": "This .xlsx needs too much memory to open safely."}
    except Exception as e:      # anything openpyxl or defusedxml refuse
        name = type(e).__name__
        chain, x = [], e
        while x is not None and len(chain) < 5:
            chain.append(type(x).__name__)
            x = x.__cause__ or x.__context__
        if set(chain) & {"EntitiesForbidden", "DTDForbidden", "ExternalReferenceForbidden", "NotSupportedError"}:
            out = {"ok": False, "error": "This .xlsx contains XML the app refuses for safety (entities or a DTD)."}
        else:
            out = {"ok": False, "error": "This .xlsx couldn't be read (" + name + ")."}
    sys.stdout.write(json.dumps(out, separators=(",", ":"), default=str))
    sys.stdout.flush()


if __name__ == "__main__":
    main()
