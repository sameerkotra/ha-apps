"""Patterns for the Search page (SPEC §10.2, §10.4).

- **Wildcards** (`*`, `?`, `[abc]`, `[0-9]`, `[!x]`) match a whole name, case-insensitively. In SQL they run as
  `GLOB` over the folded name (lower case, no accents), which is anchored by nature; `wildcard_to_regex` gives the
  same pattern as an anchored, case-insensitive regular expression (used to highlight the match).
- **Path** patterns (`path:taxes/2026`, `path:bills/*/2026`) match a run of folders anywhere in an item's path.
- **Regular expressions** are never run in the app's own process (only in search/regex_worker.py); here they
  are only checked (length, compiles) and searched for the literal text every match must contain, which narrows
  the files the worker reads.
"""
import re

from . import fts

WILDCARD_CHARS = set("*?[")
MAX_PATTERN = 200
MAX_REGEX = 200


def has_wildcards(s: str) -> bool:
    return any(c in WILDCARD_CHARS for c in s or "")


def is_prefix_word(s: str) -> bool:
    """"budg*" — a word with one star at the end (a prefix search), not a name pattern."""
    return bool(s) and s.endswith("*") and len(s) > 1 and not has_wildcards(s[:-1])


def wildcard_glob(pattern: str) -> str:
    """A wildcard pattern for SQLite GLOB over folded names: folded the same way; `[!x]` becomes `[^x]`."""
    p = fts.fold(pattern)[:MAX_PATTERN]
    return p.replace("[!", "[^")


def wildcard_to_regex(pattern: str) -> str:
    """`bill-2026-??.pdf` → `^bill\\-2026\\-..\\.pdf$` (use with re.IGNORECASE). Unclosed `[` is a plain `[`."""
    out = ["^"]
    i, n = 0, len(pattern)
    while i < n:
        c = pattern[i]
        if c == "*":
            out.append(".*")
        elif c == "?":
            out.append(".")
        elif c == "[":
            j = pattern.find("]", i + 2 if i + 1 < n and pattern[i + 1] in "!^" else i + 1)
            if j == -1:
                out.append(re.escape(c))
            else:
                body = pattern[i + 1:j]
                neg = body[:1] in ("!", "^")
                if neg:
                    body = body[1:]
                body = "".join("\\" + ch if ch in "\\]^" else ch for ch in body)
                out.append("[" + ("^" if neg else "") + body + "]")
                i = j
        else:
            out.append(re.escape(c))
        i += 1
    out.append("$")
    return "".join(out)


def like_escape(s: str) -> str:
    return fts.like_escape(s)


def path_like(pattern: str) -> str:
    """A path filter → a LIKE pattern over "/<root name>/<path inside>/" (case-insensitive for plain letters):
    the folders given must follow each other somewhere in the path; `*` and `?` work inside a part."""
    parts = [p for p in pattern.replace("\\", "/").split("/") if p]
    body = "/".join("".join("%" if c == "*" else "_" if c == "?" else like_escape(c) for c in p) for p in parts)
    return "%/" + body + "/%"


def check_regex(pattern: str) -> str | None:
    """Why a regular expression can't be used (shown under the box), or None."""
    if not pattern:
        return "Type a pattern."
    if len(pattern) > MAX_REGEX:
        return f"A pattern can be at most {MAX_REGEX} characters."
    try:
        re.compile(pattern)
    except re.error as e:
        return f"That pattern doesn't work: {e}."
    except (RecursionError, OverflowError, ValueError):
        return "That pattern is too complicated."
    return None


def required_literals(pattern: str, min_len: int = 3) -> list[str]:
    """Text every match of `pattern` must contain: the literal runs at the top level of the expression (outside
    alternatives, optional parts and character classes). Used to narrow the files read; [] when unsure."""
    try:
        import re._parser as sre_parse          # Python 3.11+
    except ImportError:                           # pragma: no cover
        import sre_parse                          # type: ignore
    try:
        parsed = sre_parse.parse(pattern)
    except Exception:
        return []
    out, run = [], []
    lit = sre_parse.LITERAL
    for op, av in parsed:
        if op == lit:
            run.append(chr(av))
            continue
        if run:
            out.append("".join(run))
            run = []
    if run:
        out.append("".join(run))
    return [s for s in out if len(s.strip()) >= min_len]
