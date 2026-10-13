"""Reading fetched web pages in one pass (linear time, whatever the page holds).

Regular expressions such as ``<(script|style)\\b.*?</\\1>`` or ``<[^>]+>`` look harmless, but on a hostile page (a
few hundred kilobytes of ``<svg `` with no closing tag, or of ``<`` with no ``>``) each start position scans to the
end of the page: the time grows with the square of the size, and a 200 KB page took about a minute. Everything here
walks the page once with ``str.find``, and pages are cut to ``MAX_SCAN_CHARS`` first.

* ``tags(raw)``: every tag as ``(name, tag_text, body)``; ``body`` is the contents of a ``script``, ``style``,
  ``noscript``, ``svg``, ``title`` or ``textarea`` element (skipped over, not read as tags), else None;
* ``strip(raw, drop=..., block=...)``: the page's text, with the bodies of ``drop`` elements removed, ``block`` tags
  turned into line breaks and every other tag into a space (entities are decoded);
* ``jsonld(raw)``: the contents of each ``<script type="application/ld+json">``;
* ``attr(tag_text, name)``: an attribute's value in a tag, or None.
"""

from __future__ import annotations

import html as htmllib
import re
from typing import Iterator

MAX_SCAN_CHARS = 600_000            # the part of a page that is read (store pages put prices near the top)
MAX_TAG_CHARS = 4_000               # a longer "tag" is not a tag (a stray "<" in text)

RAW_TEXT = ("script", "style", "noscript", "svg", "title", "textarea")
_NAME = re.compile(r"[/!?]?([A-Za-z][A-Za-z0-9:-]*)")


def tags(raw: str, raw_text=RAW_TEXT) -> Iterator[tuple[str, str, str | None, int, int]]:
    """``(name, tag_text, body, start, end)`` for every tag in order: ``name`` lowercased with a leading "/" for a
    closing tag, ``start``/``end`` where the tag (and its body, if any) is in ``raw``. The contents of ``raw_text``
    elements are not read as tags: they come whole as ``body``."""
    raw = (raw or "")[:MAX_SCAN_CHARS]
    lower = None
    n, i = len(raw), 0
    next_gt = -2                             # the first ">" after the last place looked (-2: not looked yet)
    while i < n:
        lt = raw.find("<", i)
        if lt < 0:
            return
        if raw.startswith("<!--", lt):      # a comment: up to "-->" (never closed: the rest is the comment)
            close = raw.find("-->", lt + 4)
            if close < 0:
                return
            yield "!--", raw[lt:close + 3], None, lt, close + 3
            i = close + 3
            continue
        m = _NAME.match(raw, lt + 1)         # "<" + a name (or "/" + a name, "!doctype", "?xml"), else it is text
        if not m:
            i = lt + 1
            continue
        gt = raw.find(">", m.end(), lt + 1 + MAX_TAG_CHARS)
        if gt < 0:
            if next_gt != -1 and next_gt <= lt:
                next_gt = raw.find(">", lt + 1)
            if next_gt == -1:
                return                       # no ">" anywhere after: the rest is text
            # every "<" from here to MAX_TAG_CHARS before that ">" is too far from it to be a tag
            i = max(lt + 1, next_gt - MAX_TAG_CHARS)
            continue
        name = m.group(1).lower()
        closing = raw[lt + 1] == "/"
        if raw[lt + 1] in "!?":
            name = raw[lt + 1] + name
        text = raw[lt:gt + 1]
        if not closing and name in raw_text and not text.endswith("/>"):
            if lower is None:
                lower = raw.lower()
            close = lower.find("</" + name, gt + 1)
            if close < 0:
                yield name, text, raw[gt + 1:], lt, n          # never closed: the rest of the page is its body
                return
            close_end = raw.find(">", close)
            close_end = n - 1 if close_end < 0 else close_end
            yield name, text, raw[gt + 1:close], lt, close_end + 1
            i = close_end + 1
            continue
        yield ("/" + name if closing else name), text, None, lt, gt + 1
        i = gt + 1


def strip(raw: str, drop=("script", "style", "noscript", "svg"), block=()) -> str:
    """The text of an HTML page: ``drop`` elements removed with their contents, ``block`` tags (by name, opening or
    closing) as line breaks, other tags as spaces, entities decoded. Whitespace is left for the caller to tidy."""
    raw = (raw or "")[:MAX_SCAN_CHARS]
    drop, block = set(drop), set(block)
    # what is dropped goes whole; script and style hold code, title and textarea text: never read as tags. The rest
    # (an svg that is kept, say) is markup like any other.
    skip = drop | {"script", "style", "title", "textarea"}
    parts: list[str] = []
    pos = 0
    for name, _text, body, start, end in tags(raw, skip):
        parts.append(raw[pos:start])
        bare = name.lstrip("/")
        if body is not None and bare not in drop:
            parts.append(" " + body + " ")
        else:
            parts.append("\n" if bare in block else " ")
        pos = end
    parts.append(raw[pos:])
    return htmllib.unescape("".join(parts))


def jsonld(raw: str) -> list[str]:
    """The contents of every ``<script type="application/ld+json">`` block."""
    out = []
    for name, text, body, _s, _e in tags(raw):
        if name == "script" and body is not None and "ld+json" in (attr(text, "type") or "").lower():
            out.append(body)
    return out


_ATTRS: dict[str, re.Pattern] = {}


def attr(tag_text: str, name: str) -> str | None:
    """The quoted value of attribute ``name`` in one tag (tags are at most MAX_TAG_CHARS long)."""
    rx = _ATTRS.get(name)
    if rx is None:
        rx = _ATTRS[name] = re.compile(r"(?<![\w-])" + re.escape(name) + r"\s*=\s*([\"'])(.*?)\1", re.I | re.S)
    m = rx.search(tag_text[:MAX_TAG_CHARS])
    return m.group(2) if m else None


def text_after(raw: str, end: int, limit: int = 200) -> str:
    """The text right after a tag, up to the next "<" (at most ``limit`` characters)."""
    raw = raw or ""
    stop = raw.find("<", end, end + limit + 1)
    return raw[end:stop if stop >= 0 else end + limit]
