"""The layout of *Download as PDF* (SPEC §17.12) — Markdown blocks and inline runs, and the PDF itself — kept apart
from the app (only pdfwriter and the checklist format), so formats/docpdf_worker.py can run it in a separate,
time-limited process (review 2026-10 D2). Every pattern here runs in linear time: emphasis, links and document
links have a bounded length, and headings and table rules are taken apart without regular expressions that
backtrack."""
import re

from ..pdfwriter import PdfDoc

# ---------------------------------------------------------------- Markdown (blocks and inline runs)
# Each part has a longest length (SPAN), so a paragraph full of "*" or "[" that never close costs SPAN steps per
# character at most — not the rest of the paragraph each time.
SPAN = 300
_INLINE = re.compile(r"(\*\*|__)(?P<b>.{1,%d}?)\1|(?<![\w*])([*_])(?P<i>[^*_\s](?:.{0,%d}?[^*_\s])?)\3(?![\w*])"
                     r"|`(?P<c>[^`]{1,%d})`|\[\[(?P<doc>[^\[\]\n]{1,%d})\]\]|\[(?P<lt>[^\]]{1,%d})\]\((?P<lu>[^)\s]{1,%d})\)"
                     r"|!\[(?P<alt>[^\]]{0,%d})\]\([^)]{0,%d}\)" % ((SPAN,) * 8))


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
_HEAD = re.compile(r"^\s{0,3}(#{1,6})\s+(.*)$")
_FENCE = re.compile(r"^\s{0,3}(```|~~~)")
_SEP_CELL = re.compile(r":?-+:?")


def heading_text(rest: str) -> str:
    """A heading's text without its closing #s and spaces ("Title ##  " → "Title") — what `(.*?)\\s*#*\\s*$`
    gave, without the regular expression (it took cubic time on a long line of spaces and #s)."""
    return rest.rstrip().rstrip("#").rstrip()


def is_table_sep(line: str) -> bool:
    """`| --- | :-: |` — a table's rule line: optional outer pipes, cells of dashes with optional colons."""
    s = line.strip()
    if s.startswith("|"):
        s = s[1:]
    if s.endswith("|"):
        s = s[:-1]
    return all(_SEP_CELL.fullmatch(c.strip()) for c in s.split("|"))


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
            out.append({"t": "heading", "level": len(m.group(1)), "text": heading_text(m.group(2))})
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
        if "|" in line and i + 1 < len(lines) and is_table_sep(lines[i + 1]) and "-" in lines[i + 1]:
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


def render(title: str, sub: str, kind: str, text: str) -> tuple[bytes, int]:
    """(PDF bytes, characters the fonts can't show) of a note, Markdown note or checklist's text. A checklist
    that isn't a pure task list any more prints as a Markdown note."""
    from . import checklist_md
    items = None
    if kind == "checklist":
        try:
            items = checklist_md.parse(text)
        except ValueError:
            kind = "markdown"
    pdf = build(title, sub, kind, text=text, items=items)
    return pdf.output(), pdf.unsupported
