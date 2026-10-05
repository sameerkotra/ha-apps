"""Download as PDF and *Save PDF here* (SPEC §17.12) for notes, Markdown notes and checklists, through the app's own
PDF writer (pdfwriter.py): the title, the owner and the date at the top, then the text — a plain note line by
line, a Markdown note with its headings, lists, tick boxes, quotes, code and tables, a checklist with its boxes
(ticked or empty) and indents. Sheets print from their editor (§8.5)."""
import os
import re

from fastapi import HTTPException

from . import config, documents, files, sharing
from .formats import checklist_md, text as text_fmt
from .pdfwriter import PdfDoc
from .store import nodes, paths

PDF_KINDS = ("note", "markdown", "checklist")

# ---------------------------------------------------------------- Markdown (blocks and inline runs)
_INLINE = re.compile(r"(\*\*|__)(?P<b>.+?)\1|(?<![\w*])([*_])(?P<i>[^*_\s](?:.*?[^*_\s])?)\3(?![\w*])|`(?P<c>[^`]+)`"
                     r"|\[\[(?P<doc>[^\[\]\n]+)\]\]|\[(?P<lt>[^\]]+)\]\((?P<lu>[^)\s]+)\)|!\[(?P<alt>[^\]]*)\]\([^)]*\)")


def inline_runs(text: str, base: str = "R") -> list:
    """Markdown inline → [(text, font)]: **bold**, *italic*, `code`, [text](url) → "text (url)", [[Doc]] → Doc."""
    runs = []
    pos = 0
    bold = "B" if base == "R" else ("BI" if base == "I" else base)
    ital = "I" if base == "R" else ("BI" if base == "B" else base)
    for m in _INLINE.finditer(text):
        if m.start() > pos:
            runs.append((text[pos:m.start()], base))
        if m.group("b") is not None:
            runs += inline_runs(m.group("b"), bold)
        elif m.group("i") is not None:
            runs += inline_runs(m.group("i"), ital)
        elif m.group("c") is not None:
            runs.append((m.group("c"), "M"))
        elif m.group("doc") is not None:
            runs.append((m.group("doc"), bold))
        elif m.group("lt") is not None:
            runs.append((m.group("lt"), base))
            if re.match(r"https?://", m.group("lu"), re.I):
                runs.append((f" ({m.group('lu')})", base))
        elif m.group("alt") is not None:
            runs.append((f"[{m.group('alt') or 'image'}]", ital))
        pos = m.end()
    if pos < len(text):
        runs.append((text[pos:], base))
    return runs or [("", base)]


_LIST = re.compile(r"^(\s*)([-*+]|\d{1,9}[.)])\s+(.*)$")
_TASK = re.compile(r"^\[([ xX])\]\s+(.*)$")
_HR = re.compile(r"^\s{0,3}([-*_])(\s*\1){2,}\s*$")
_HEAD = re.compile(r"^\s{0,3}(#{1,6})\s+(.*?)\s*#*\s*$")
_FENCE = re.compile(r"^\s{0,3}(```|~~~)")
_TABLE_SEP = re.compile(r"^\s*\|?\s*:?-{1,}:?\s*(\|\s*:?-{1,}:?\s*)*\|?\s*$")


def md_blocks(text: str) -> list[dict]:
    """A Markdown note's blocks: heading, para, item (bullet / number / task, level), quote, code, hr, table."""
    lines = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    out, para = [], []

    def flush():
        if para:
            out.append({"t": "para", "text": " ".join(x.strip() for x in para)})
            para.clear()
    i = 0
    while i < len(lines):
        line = lines[i]
        if _FENCE.match(line):
            flush()
            fence = _FENCE.match(line).group(1)
            i += 1
            code = []
            while i < len(lines) and not lines[i].strip().startswith(fence):
                code.append(lines[i])
                i += 1
            out.append({"t": "code", "lines": code})
            i += 1
            continue
        if not line.strip():
            flush()
            i += 1
            continue
        m = _HEAD.match(line)
        if m:
            flush()
            out.append({"t": "heading", "level": len(m.group(1)), "text": m.group(2)})
            i += 1
            continue
        if _HR.match(line):
            flush()
            out.append({"t": "hr"})
            i += 1
            continue
        if line.lstrip().startswith(">"):
            flush()
            q = []
            while i < len(lines) and lines[i].lstrip().startswith(">"):
                q.append(lines[i].lstrip()[1:].strip())
                i += 1
            out.append({"t": "quote", "text": " ".join(x for x in q if x)})
            continue
        if "|" in line and i + 1 < len(lines) and _TABLE_SEP.match(lines[i + 1]) and "-" in lines[i + 1]:
            flush()
            rows = [line]
            i += 2
            while i < len(lines) and "|" in lines[i] and lines[i].strip():
                rows.append(lines[i])
                i += 1
            cells = [[c.strip() for c in r.strip().strip("|").split("|")] for r in rows]
            out.append({"t": "table", "rows": cells})
            continue
        m = _LIST.match(line)
        if m:
            flush()
            level = min(4, len(m.group(1).replace("\t", "  ")) // 2)
            body = m.group(3)
            task = _TASK.match(body) if m.group(2) in "-*+" else None
            if task:
                out.append({"t": "item", "level": level, "box": task.group(1) != " ", "text": task.group(2)})
            else:
                out.append({"t": "item", "level": level, "bullet": "•" if m.group(2) in "-*+" else m.group(2),
                            "text": body})
            i += 1
            continue
        para.append(line)
        i += 1
    flush()
    return out


# ---------------------------------------------------------------- the PDF
def _head(pdf: PdfDoc, title: str, sub: str) -> None:
    pdf.paragraph([(title, "B")], size=20, after=2, leading=1.2)
    pdf.paragraph([(sub, "R")], size=9, after=10, colour=(0.4, 0.4, 0.4))


def build(title: str, sub: str, kind: str, text: str | None = None, items=None) -> PdfDoc:
    pdf = PdfDoc(title)
    _head(pdf, title, sub)
    if kind == "checklist":
        for it in items or []:
            pdf.paragraph([(it.text, "R")], indent=18 * it.level, box=it.done, after=2,
                          colour=(0.35, 0.35, 0.35) if it.done else None)
        if not items:
            pdf.paragraph([("(no items)", "I")])
    elif kind == "markdown":
        for b in md_blocks(text or ""):
            t = b["t"]
            if t == "heading":
                pdf.heading(b["text"].replace("**", ""), b["level"])
            elif t == "para":
                pdf.paragraph(inline_runs(b["text"]), after=6)
            elif t == "item":
                indent = 14 * b["level"]
                if "box" in b:
                    pdf.paragraph(inline_runs(b["text"]), indent=indent, box=b["box"], after=2)
                else:
                    pdf.paragraph(inline_runs(b["text"]), indent=indent, bullet=b["bullet"], after=2)
            elif t == "quote":
                pdf.paragraph(inline_runs(b["text"], "I"), indent=14, bar=True, after=6, colour=(0.3, 0.3, 0.3))
            elif t == "code":
                pdf.space(2)
                for ln in b["lines"] or [""]:
                    pdf.code_line(ln)
                pdf.space(6)
            elif t == "hr":
                pdf.rule()
            elif t == "table":
                for n, row in enumerate(b["rows"]):
                    runs = []
                    for j, cell in enumerate(row):
                        if j:
                            runs.append(("   |   ", "R"))
                        runs += inline_runs(cell, "B" if n == 0 else "R")
                    pdf.paragraph(runs, size=9.5, after=2)
                pdf.space(4)
    else:
        lines = (text or "").replace("\r\n", "\n").replace("\r", "\n").split("\n")
        for ln in lines:
            if ln.strip():
                pdf.paragraph([(ln, "R")], after=1)
            else:
                pdf.space(8)
    return pdf


def make(conn, user: dict, node_id: str) -> tuple[str, bytes, int]:
    """(file name, PDF bytes, characters the fonts can't show) of a document the person can open."""
    node, _role = sharing.require(conn, user, node_id, "viewer")
    if node["kind"] not in PDF_KINDS:
        raise HTTPException(415, "Notes and checklists download as PDF; sheets print from their editor.")
    _root, _real, data, _etag, _st, _sha = documents._read(conn, node)
    try:
        text, _meta = text_fmt.decode(data)
    except text_fmt.NotText as e:
        raise HTTPException(415, str(e))
    stem = paths.split_ext(node["name"])[0] if node["ext"] else node["name"]
    owner = sharing.owner_id(conn, node)
    who = documents._name_of(conn, owner) if owner else None
    when = config.now().strftime("%Y-%m-%d %H:%M")
    sub = " · ".join(x for x in (who, f"changed {config.parse_iso(node['mtime']).astimezone(config.now().tzinfo).strftime('%Y-%m-%d %H:%M')}"
                                 if node["mtime"] else None, f"printed {when}") if x)
    items = None
    kind = node["kind"]
    if kind == "checklist":
        try:
            items = checklist_md.parse(text)
        except ValueError:
            kind = "markdown"
    pdf = build(stem, sub, kind, text=text, items=items)
    return stem + ".pdf", pdf.output(), pdf.unsupported


def save_here(user: dict, node_id: str) -> tuple[str, int]:
    """Write the PDF beside the document (the person must be able to add files there). → (new node id, unsupported)."""
    from . import db
    with db.get_conn() as conn:
        node, _role = sharing.require(conn, user, node_id, "viewer")
        ref = nodes.parent_ref(node, nodes.roots_info(conn))
        if ref is None and sharing.owner_id(conn, node) != user["id"]:
            raise HTTPException(403, "You can't add files beside this document — download the PDF instead.")
        name, data, bad = make(conn, user, node_id)
    try:
        prep = files.prepare_upload(user, ref, name, len(data))
    except HTTPException as e:
        if e.status_code in (403, 404):
            raise HTTPException(403, "You can't add files beside this document — download the PDF instead.")
        raise
    fd, tmp = files.new_temp(prep)
    with os.fdopen(fd, "wb") as f:
        f.write(data)
        f.flush()
        os.fsync(f.fileno())
    return files.finish_upload(user, prep, tmp, len(data), "keep"), bad
