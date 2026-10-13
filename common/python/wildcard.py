"""Shell-style wildcard matching without regular expressions (shared by the household apps:
common/python/wildcard.py, copied into each app's app/common/ by tools/sync_common.py). Standard library only.

`fnmatch` turns a pattern into a regular expression and `re` backtracks: a pattern someone typed such as
"*a*a*a*a*a*a*a*a*a*a*a*b" against a long name of a's takes exponential time there. This matcher walks
the name once with a single backtracking point (the last `*`), so the worst case is name × pattern
steps — milliseconds for anything a person can type — and a pattern can't hang a request thread.

    wildcard.match("*.pdf", "Invoice.PDF")                # True (case-insensitive by default)
    wildcard.match("report-202?-[0-9][0-9].csv", name)    # ? one character, [..] a set, [!..] not in the set
    wildcard.match("[a-c]*", "Beta", ignore_case=False)   # ranges; False here (B isn't in a-c)
    wildcard.filter(["a.txt", "b.md"], "*.txt")           # the names that match
    wildcard.is_pattern("a*")                             # has a wildcard at all?

Only `*`, `?`, `[...]` and `[!...]` are special; everything else matches itself. An unclosed `[` is a
literal `[`; `]` first in a set is a literal `]`; `-` first or last in a set is a literal `-`. A pattern longer
than MAX_PATTERN (1000) or a name longer than MAX_NAME (10 000) is refused with ValueError (nothing that long
is a file name). `/` isn't special (a name is one component; the caller splits paths).
"""
from __future__ import annotations

MAX_PATTERN = 1000
MAX_NAME = 10_000

_STAR, _ANY, _SET, _CHAR = 0, 1, 2, 3


def is_pattern(text: str) -> bool:
    return any(c in text for c in "*?[")


def _parse_set(pattern: str, i: int):
    """The set starting at pattern[i] == "[": ((negate, chars, ranges), index after it), or None if unclosed."""
    j = i + 1
    negate = False
    if j < len(pattern) and pattern[j] == "!":
        negate = True
        j += 1
    chars, ranges = set(), []
    first = True
    while j < len(pattern):
        c = pattern[j]
        if c == "]" and not first:
            return (negate, frozenset(chars), tuple(ranges)), j + 1
        first = False
        if j + 2 < len(pattern) and pattern[j + 1] == "-" and pattern[j + 2] != "]":
            lo, hi = c, pattern[j + 2]
            if lo <= hi:
                ranges.append((lo, hi))
            j += 3
            continue
        chars.add(c)
        j += 1
    return None


def compile(pattern: str, ignore_case: bool = True) -> tuple:  # noqa: A001 — the natural name
    """The pattern as a tuple of tokens (match() does this itself; keep the result when matching many names)."""
    if len(pattern) > MAX_PATTERN:
        raise ValueError(f"pattern longer than {MAX_PATTERN} characters")
    if ignore_case:
        pattern = pattern.casefold()
    out = []
    i = 0
    while i < len(pattern):
        c = pattern[i]
        if c == "*":
            if not out or out[-1][0] != _STAR:              # "**" is "*"
                out.append((_STAR, None))
            i += 1
        elif c == "?":
            out.append((_ANY, None))
            i += 1
        elif c == "[":
            parsed = _parse_set(pattern, i)
            if parsed is None:
                out.append((_CHAR, "["))
                i += 1
            else:
                out.append((_SET, parsed[0]))
                i = parsed[1]
        else:
            out.append((_CHAR, c))
            i += 1
    return tuple(out)


def _in_set(spec, c: str) -> bool:
    negate, chars, ranges = spec
    hit = c in chars or any(lo <= c <= hi for lo, hi in ranges)
    return hit != negate


def _token_matches(token, c: str) -> bool:
    kind, arg = token
    if kind == _ANY:
        return True
    if kind == _CHAR:
        return c == arg
    return _in_set(arg, c)


def match_compiled(tokens: tuple, name: str, ignore_case: bool = True) -> bool:
    if len(name) > MAX_NAME:
        raise ValueError(f"name longer than {MAX_NAME} characters")
    if ignore_case:
        name = name.casefold()
    n, m = len(name), len(tokens)
    i = p = 0
    star_p = star_i = -1                                  # the last "*" and where the name was then
    while i < n:
        if p < m and tokens[p][0] == _STAR:
            star_p, star_i = p, i
            p += 1
        elif p < m and _token_matches(tokens[p], name[i]):
            i += 1
            p += 1
        elif star_p >= 0:                                 # back to the last "*": let it eat one more char
            star_i += 1
            i = star_i
            p = star_p + 1
        else:
            return False
    while p < m and tokens[p][0] == _STAR:
        p += 1
    return p == m


def match(pattern: str, name: str, ignore_case: bool = True) -> bool:
    """Does `name` match the wildcard `pattern` (whole name)?"""
    return match_compiled(compile(pattern, ignore_case), name, ignore_case)


def filter(names, pattern: str, ignore_case: bool = True) -> list:  # noqa: A001 — like fnmatch.filter
    tokens = compile(pattern, ignore_case)
    return [n for n in names if match_compiled(tokens, n, ignore_case)]
