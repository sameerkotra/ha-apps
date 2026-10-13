"""In-app PDF viewer helpers: page images plus word boxes, from poppler.

Why not just link to the PDF? A link that opens in a new tab is handed to the
phone's own browser by the Home Assistant companion app, and that browser has no
Home Assistant session, so it answers "401 unauthorized". Instead the viewer
page (routes/pdf_view.py) shows each PDF page as a PNG rendered here with
`pdftoppm`, inside the same page, and highlights figures using the word
positions from `pdftotext -bbox`. Both tools ship in poppler-utils, the package
the parsers already need, so this adds no dependency — and PNGs display in every
WebView, which an embedded PDF frame does not.
"""
import html
import os
import re
import threading
from collections import OrderedDict

from .common import sandbox_run
from .parser.deterministic import run_pdftotext

# ~2x a phone screen's CSS pixels, so text stays sharp when pinch-zoomed.
RENDER_DPI = 200
_TIMEOUT = 60
# The word boxes of one PDF are about 100 bytes a word: 20 MB is far more than any statement or bill.
MAX_BBOX_BYTES = 20 * 1024 * 1024
MAX_PAGE_PNG_BYTES = 40 * 1024 * 1024

_PAGE_RE = re.compile(r'<page width="([\d.]+)" height="([\d.]+)">(.*?)</page>', re.S)
_WORD_RE = re.compile(r'<word xMin="([\d.]+)" yMin="([\d.]+)" xMax="([\d.]+)" yMax="([\d.]+)">(.*?)</word>', re.S)

# An amount as printed: 1,234.56  .50  $12.00  (12.00)  12.00CR  -12.00 — always two decimals.
_AMOUNT_WORD = re.compile(r"^\(?-?\$?(\d[\d,]*|)\.(\d{2})\)?(?:CR|DR|-)?$", re.IGNORECASE)

# Rendered pages and word lists are kept in memory by size, not by count: a page image of a big
# scanned bill can be several MB, so "the last 8" could mean very different amounts of memory.
CACHE_BYTES = 64 * 1024 * 1024
# Rendering is CPU-heavy: two at a time, others wait their turn (a viewer opens pages one by one).
RENDER_SLOTS = threading.BoundedSemaphore(2)


class _ByteCache:
    """Least recently used entries are dropped once the total size passes `limit`."""

    def __init__(self, limit: int):
        self.limit, self.total = limit, 0
        self._items: "OrderedDict[tuple, tuple]" = OrderedDict()
        self._lock = threading.Lock()

    def get(self, key):
        with self._lock:
            item = self._items.get(key)
            if item is None:
                return None
            self._items.move_to_end(key)
            return item[0]

    def put(self, key, value, size: int):
        with self._lock:
            if key in self._items:
                self.total -= self._items.pop(key)[1]
            if size <= self.limit:
                self._items[key] = (value, size)
                self.total += size
            while self.total > self.limit and self._items:
                self.total -= self._items.popitem(last=False)[1][1]
        return value

    def __contains__(self, key) -> bool:
        with self._lock:
            return key in self._items

    def __len__(self) -> int:
        return len(self._items)

    def clear(self) -> None:
        with self._lock:
            self._items.clear()
            self.total = 0


_words_cache = _ByteCache(CACHE_BYTES // 2)
_image_cache = _ByteCache(CACHE_BYTES // 2)


def _stamp(path: str) -> tuple:
    st = os.stat(path)
    return path, st.st_mtime_ns, st.st_size


def stamp_tag(path: str) -> str:
    """A short tag that changes whenever the file does (for the page images' cache headers)."""
    _, mtime, size = _stamp(path)
    return f"{mtime:x}-{size:x}"


def load_pages(path: str) -> list[dict]:
    """[{"width", "height", "words": [(text, x0, y0, x1, y1), ...]}, ...] in PDF points."""
    key = _stamp(path)
    cached = _words_cache.get(key)
    if cached is not None:
        return cached
    # A copy, as pdfworker, with limits; the word list goes to a file and at most MAX_BBOX_BYTES is read.
    with RENDER_SLOTS:
        text = run_pdftotext(path, "-bbox", max_bytes=MAX_BBOX_BYTES, timeout=_TIMEOUT)
    pages = []
    for width, height, body in _PAGE_RE.findall(text):
        words = [
            (html.unescape(text).strip(), float(x0), float(y0), float(x1), float(y1))
            for x0, y0, x1, y1, text in _WORD_RE.findall(body)
        ]
        pages.append({"width": float(width), "height": float(height), "words": words})
    return _words_cache.put(key, pages, 200 + sum(100 + sum(len(w[0]) for w in p["words"]) * 2 + 80 * len(p["words"])
                                                  for p in pages))


def render_page(path: str, page: int) -> bytes:
    """PNG bytes of one 1-based page."""
    key = _stamp(path) + (page,)
    cached = _image_cache.get(key)
    if cached is not None:
        return cached
    with RENDER_SLOTS, sandbox_run.Scratch(prefix="pdftoppm-") as box:   # a copy, as pdfworker, with limits
        src = box.add_file(path, "in.pdf")
        root = os.path.join(box.path, "page")
        result = box.run(
            ["pdftoppm", "-png", "-r", str(RENDER_DPI), "-f", str(page), "-l", str(page), "-singlefile", src, root],
            capture_output=True, timeout=_TIMEOUT,
        )
        out = root + ".png"
        if result.returncode != 0 or not os.path.exists(out):
            raise RuntimeError(f"pdftoppm failed: {result.stderr.decode(errors='replace').strip()}")
        png = box.read("page.png", max_bytes=MAX_PAGE_PNG_BYTES)
    return _image_cache.put(key, png, len(png))


def word_amount(text: str) -> float | None:
    """The absolute value of a printed money amount, or None if the word isn't one."""
    m = _AMOUNT_WORD.match(text.strip())
    if not m:
        return None
    whole = m.group(1).replace(",", "") or "0"
    return float(f"{whole}.{m.group(2)}")


def _norm(text: str) -> str:
    return re.sub(r"[^\w.]+", "", text.lower())


def _word_matches(token: str, word: str) -> bool:
    """Exact for short tokens ("in" must not light up "interest"), substring for longer ones."""
    return bool(word) and (word == token or (len(token) >= 4 and token in word))


def find_hits(pages: list[dict], targets: list[dict]) -> list[list[dict]]:
    """For each page, the boxes to highlight, as percentages of the page size.

    A target is {"kind": "in" | "out" | "find", "amount": float} (matches every printed
    amount equal to it, in any of the usual formats) or {"kind": "find", "text": str}
    (matches that word or run of words, case-insensitively)."""
    per_page: list[list[dict]] = []
    for page in pages:
        words, hits = page["words"], []
        w, h = page["width"] or 1, page["height"] or 1

        def add(kind: str, label: str, chunk) -> None:
            x0 = min(c[1] for c in chunk)
            y0 = min(c[2] for c in chunk)
            x1 = max(c[3] for c in chunk)
            y1 = max(c[4] for c in chunk)
            hits.append({
                "kind": kind, "label": label,
                "left": 100 * x0 / w, "top": 100 * y0 / h,
                "width": 100 * (x1 - x0) / w, "height": 100 * (y1 - y0) / h,
            })

        for target in targets:
            if target.get("amount") is not None:
                want = round(abs(target["amount"]), 2)
                for word in words:
                    got = word_amount(word[0])
                    if got is not None and round(got, 2) == want:
                        add(target["kind"], target.get("label", ""), [word])
            elif target.get("text"):
                tokens = [t for t in (_norm(x) for x in target["text"].split()) if t]
                if not tokens:
                    continue
                normed = [_norm(word[0]) for word in words]
                for i in range(len(words) - len(tokens) + 1):
                    if all(_word_matches(tokens[j], normed[i + j]) for j in range(len(tokens))):
                        add(target["kind"], target.get("label", target["text"]), words[i:i + len(tokens)])
        per_page.append(hits)
    return per_page
