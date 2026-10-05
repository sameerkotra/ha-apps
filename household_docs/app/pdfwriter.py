"""A small PDF writer (SPEC §17.12: *Download as PDF* for notes, Markdown notes and checklists). Pure Python, no
dependencies: text in paragraphs with bold / italic / monospace runs, headings, bullets, tick boxes (drawn, ticked
or not), quotes, horizontal rules, page numbers. A4 pages.

Fonts (*decision*): the PDF standard fonts — Helvetica (regular, bold, italic, bold italic) and Courier — with the
Windows-1252 ("WinAnsi") encoding. Every PDF reader has them, so nothing is embedded and a PDF of a page of text
is a few kilobytes. They cover English and the Western European languages (accents, €, “quotes”, – —, •).
Characters outside that set (Greek, Cyrillic, Indian scripts, Chinese, emoji …) are written as "?" and counted
(`PdfDoc.unsupported`); the app then suggests *Print → Save as PDF*, which uses the device's own fonts. Embedding
a Unicode font (DejaVu Sans is permissively licensed) would need ~700 KB per font in the image and a TrueType
subsetter; not worth it for the household's notes.
"""
import zlib

from .pdf_fonts import FIRST, WIDTHS

A4 = (595.28, 841.89)
FONTS = {"R": ("F1", "Helvetica"), "B": ("F2", "Helvetica-Bold"), "I": ("F3", "Helvetica-Oblique"),
         "BI": ("F4", "Helvetica-BoldOblique"), "M": ("F5", "Courier")}
GREY = (0.45, 0.45, 0.45)


def encode(text: str) -> tuple[bytes, int]:
    """Text → Windows-1252 bytes for a PDF string; (bytes, how many characters had to become "?")."""
    out = bytearray()
    bad = 0
    for ch in text:
        if ch in "   ":
            ch = " "
        elif ch == "\t":
            ch = "    "
        try:
            b = ch.encode("cp1252")
        except UnicodeEncodeError:
            out += b"?"
            bad += 1
            continue
        if any(x < 32 for x in b):
            continue
        out += b
    return bytes(out), bad


def width(data: bytes, font: str, size: float) -> float:
    if font == "M":
        return 0.6 * size * len(data)
    table = WIDTHS[FONTS[font][1]]
    return sum(table[c - FIRST] if c >= FIRST else 0 for c in data) * size / 1000.0


def pdf_string(data: bytes) -> bytes:
    out = bytearray(b"(")
    for c in data:
        if c in (0x28, 0x29, 0x5C):
            out += b"\\" + bytes([c])
        elif c < 32 or c > 126:
            out += b"\\%03o" % c
        else:
            out.append(c)
    out += b")"
    return bytes(out)


def _num(x: float) -> bytes:
    s = f"{x:.2f}".rstrip("0").rstrip(".")
    return (s if s not in ("", "-0") else "0").encode()


class PdfDoc:
    def __init__(self, title: str = "", page: tuple = A4, margin: float = 54.0):
        self.title = title
        self.w, self.h = page
        self.margin = margin
        self.pages: list[list[bytes]] = []
        self.unsupported = 0
        self.y = 0.0
        self._new_page()

    # ---------------------------------------------------------------- pages
    def _new_page(self) -> None:
        self.pages.append([])
        self.y = self.h - self.margin

    @property
    def ops(self) -> list:
        return self.pages[-1]

    def _room(self, need: float) -> None:
        if self.y - need < self.margin + 14:
            self._new_page()

    def space(self, pts: float) -> None:
        self.y -= pts
        if self.y < self.margin + 14:
            self._new_page()

    # ---------------------------------------------------------------- drawing
    def _text_at(self, x: float, y: float, data: bytes, font: str, size: float, colour=None) -> None:
        if colour:
            self.ops.append(b"%s %s %s rg" % tuple(_num(c) for c in colour))
        self.ops.append(b"BT /%s %s Tf %s %s Td %s Tj ET" % (FONTS[font][0].encode(), _num(size), _num(x), _num(y),
                                                             pdf_string(data)))
        if colour:
            self.ops.append(b"0 0 0 rg")

    def _box(self, x: float, y: float, s: float, ticked: bool) -> None:
        self.ops.append(b"0.8 w %s %s %s %s re S" % (_num(x), _num(y), _num(s), _num(s)))
        if ticked:
            self.ops.append(b"1.2 w %s %s m %s %s l %s %s l S 0.8 w" % (
                _num(x + s * 0.2), _num(y + s * 0.5), _num(x + s * 0.42), _num(y + s * 0.22),
                _num(x + s * 0.82), _num(y + s * 0.82)))

    def rule(self) -> None:
        self._room(12)
        self.y -= 6
        self.ops.append(b"0.6 0.6 0.6 RG 0.6 w %s %s m %s %s l S 0 0 0 RG" % (
            _num(self.margin), _num(self.y), _num(self.w - self.margin), _num(self.y)))
        self.y -= 8

    # ---------------------------------------------------------------- text
    def _words(self, runs):
        """runs [(text, font)] → [(bytes, font, is_space)] pieces."""
        out = []
        for text, font in runs:
            data, bad = encode(text)
            self.unsupported += bad
            cur = bytearray()
            for c in data:
                if c == 0x20:
                    if cur:
                        out.append((bytes(cur), font, False))
                        cur = bytearray()
                    out.append((b" ", font, True))
                else:
                    cur.append(c)
            if cur:
                out.append((bytes(cur), font, False))
        return out

    def _lines(self, runs, size: float, avail: float):
        """Greedy line breaking; a word longer than a line is cut."""
        lines, line, lw = [], [], 0.0
        for piece, font, is_space in self._words(runs):
            pw = width(piece, font, size)
            if is_space:
                if line:
                    line.append((piece, font))
                    lw += pw
                continue
            if lw + pw > avail and line:
                while line and line[-1][0] == b" ":
                    line.pop()
                lines.append(line)
                line, lw = [], 0.0
            while pw > avail and len(piece) > 1:                 # a very long word: cut it
                n = len(piece)
                while n > 1 and width(piece[:n], font, size) > avail:
                    n -= 1
                lines.append([(piece[:n], font)])
                piece = piece[n:]
                pw = width(piece, font, size)
            line.append((piece, font))
            lw += pw
        while line and line[-1][0] == b" ":
            line.pop()
        lines.append(line)
        return lines

    def paragraph(self, runs, size: float = 10.5, indent: float = 0.0, *, after: float = 4.0, box=None,
                  bullet: str | None = None, colour=None, bar: bool = False, leading: float = 1.35) -> None:
        """A wrapped paragraph. box: None / False (an empty tick box) / True (ticked); bullet: "•" or "1."."""
        if isinstance(runs, str):
            runs = [(runs, "R")]
        lead = size * leading
        x0 = self.margin + indent
        marker_w = 0.0
        if box is not None:
            marker_w = size * 1.5
        elif bullet:
            marker_w = max(size * 1.2, width(encode(bullet)[0], "R", size) + size * 0.5)
        avail = self.w - self.margin - x0 - marker_w
        lines = self._lines(runs, size, avail)
        for i, line in enumerate(lines):
            self._room(lead)
            self.y -= lead
            base = self.y + (lead - size) * 0.35
            if i == 0 and box is not None:
                s = size * 0.85
                self._box(x0, base - size * 0.08, s, bool(box))
            elif i == 0 and bullet:
                self._text_at(x0, base, encode(bullet)[0], "R", size)
            if bar:
                self.ops.append(b"0.7 0.7 0.7 RG 2 w %s %s m %s %s l S 0 0 0 RG 0.8 w" % (
                    _num(x0 - 8), _num(self.y), _num(x0 - 8), _num(self.y + lead)))
            x = x0 + marker_w
            # merge pieces of the same font into one show operation
            chunks: list = []
            for piece, font in line:
                if chunks and chunks[-1][1] == font:
                    chunks[-1][0] += piece
                else:
                    chunks.append([bytearray(piece), font])
            for data, font in chunks:
                self._text_at(x, base, bytes(data), font, size, colour)
                x += width(bytes(data), font, size)
        self.y -= after

    def heading(self, text: str, level: int = 1) -> None:
        size = {1: 18, 2: 15, 3: 13}.get(level, 11.5)
        self._room(size * 3)
        self.space(size * 0.5)
        self.paragraph([(text, "B")], size=size, after=size * 0.3, leading=1.25)

    def code_line(self, text: str, indent: float = 12.0) -> None:
        self.paragraph([(text if text else " ", "M")], size=9.0, indent=indent, after=0, leading=1.3)

    # ---------------------------------------------------------------- output
    def output(self) -> bytes:
        objs: list[bytes] = []

        def add(body: bytes) -> int:
            objs.append(body)
            return len(objs)

        catalog = add(b"")            # filled in below
        pages_id = add(b"")
        font_ids = {}
        for key, (res, name) in FONTS.items():
            font_ids[res] = add(b"<< /Type /Font /Subtype /Type1 /BaseFont /%s /Encoding /WinAnsiEncoding >>" % name.encode())
        fonts = b" ".join(b"/%s %d 0 R" % (res.encode(), oid) for res, oid in font_ids.items())
        page_ids = []
        total = len(self.pages)
        for n, ops in enumerate(self.pages, 1):
            footer = encode(f"{n} / {total}")[0]
            fw = width(footer, "R", 8)
            stream = b"\n".join(ops + [b"0.45 0.45 0.45 rg BT /F1 8 Tf %s %s Td %s Tj ET 0 0 0 rg" % (
                _num((self.w - fw) / 2), _num(self.margin / 2), pdf_string(footer))])
            data = zlib.compress(stream)
            content = add(b"<< /Length %d /Filter /FlateDecode >>\nstream\n" % len(data) + data + b"\nendstream")
            page_ids.append(add(b"<< /Type /Page /Parent %d 0 R /MediaBox [0 0 %s %s] /Resources << /Font << %s >> >> "
                                b"/Contents %d 0 R >>" % (pages_id, _num(self.w), _num(self.h), fonts, content)))
        objs[pages_id - 1] = b"<< /Type /Pages /Kids [%s] /Count %d >>" % (
            b" ".join(b"%d 0 R" % i for i in page_ids), len(page_ids))
        objs[catalog - 1] = b"<< /Type /Catalog /Pages %d 0 R >>" % pages_id
        title_hex = ("feff" + self.title.encode("utf-16-be").hex()).encode()
        info = add(b"<< /Title <%s> /Producer (Household Docs) /Creator (Household Docs) >>" % title_hex)
        out = bytearray(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n")
        offsets = []
        for i, body in enumerate(objs, 1):
            offsets.append(len(out))
            out += b"%d 0 obj\n" % i + body + b"\nendobj\n"
        xref = len(out)
        out += b"xref\n0 %d\n0000000000 65535 f \n" % (len(objs) + 1)
        for off in offsets:
            out += b"%010d 00000 n \n" % off
        out += b"trailer\n<< /Size %d /Root %d 0 R /Info %d 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (
            len(objs) + 1, catalog, info, xref)
        return bytes(out)
