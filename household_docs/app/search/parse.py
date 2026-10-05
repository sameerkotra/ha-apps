"""The search box's syntax (SPEC §10.5).

    words              every word, in the name or in the text (each matches the start of a word in the text)
    "exact phrase"     those words together           -word / -"phrase"   leave out what has it
    a OR b             either (binds the two words next to it: `car a OR b` = car and (a or b))
    word*              the start of a word (plain words already are)
    *.pdf  IMG_[0-9]*  a bare word with * ? or [ ] is a name pattern (wildcards)
    name:*.pdf  name:/^bill-\\d+/  name:budget          a name pattern, a regular expression, words in the name
    content:/\\b\\d{4}-\\d{4}\\b/  content:invoice      a regular expression in the text, words in the text
    ext:xlsx  type:sheet  modified:7d  modified:>2026-09-01  modified:2026-01-01..2026-03-31  created:<2025
    size:>5mb  by:alex  by:me  by:outside  by:notme  owner:priya  owner:me  in:"House papers"  in:mine  in:shared
    in:everyone  in:folders  in:trash  path:taxes/2026  tag:taxes  -tag:taxes  color:red  is:fav  is:shared
    is:open  is:done  is:empty  is:dup  shared:byme|withme|not|everyone  access:edit|read

`parse(q)` never fails: anything it doesn't understand is searched as words, and a filter with a bad value becomes
an error chip. Every recognised part comes back as a chip with the exact text (`token`) to take out of the box.
"""
import re

KEYS = {"name", "content", "ext", "type", "modified", "created", "size", "by", "owner", "in", "path", "tag", "color",
        "is", "shared", "access"}
NEGATABLE = {"ext", "type", "tag", "is"}
MAX_TERMS = 16


class Parsed:
    def __init__(self):
        self.clauses: list[list[dict]] = []      # AND of ORs; a term is {"t": "word"|"phrase", "v": text}
        self.excludes: list[dict] = []
        self.patterns: list[dict] = []           # {"side": "name"|"content", "mode": "wildcard"|"regex"|"words", "v"}
        self.filters: list[dict] = []            # {"key", "value", "neg", "token"}
        self.chips: list[dict] = []
        self.text = ""                           # the free text alone (for name modes that take the whole text)

    def terms(self) -> list[dict]:
        return [t for c in self.clauses for t in c]


_TOKEN = re.compile(r'''
    (?P<neg>-)?
    (?:
        (?P<key>[A-Za-z]+):(?:
            /(?P<rx>(?:\\.|[^\\/])*)/(?=\s|$)
          | "(?P<kq>[^"]*)"?
          | (?P<kv>\S*)
        )
      | "(?P<phrase>[^"]*)"?
      | (?P<word>\S+)
    )''', re.X)


def tokens(q: str):
    """(kind, value, neg, key, raw) for every part of the box, in order."""
    for m in _TOKEN.finditer(q or ""):
        raw = m.group(0)
        if not raw.strip():
            continue
        neg = bool(m.group("neg"))
        key = (m.group("key") or "").lower()
        if key and key in KEYS:
            if m.group("rx") is not None:
                yield "regex", m.group("rx"), neg, key, raw
            elif m.group("kq") is not None:
                yield "kv", m.group("kq"), neg, key, raw
            else:
                yield "kv", m.group("kv") or "", neg, key, raw
            continue
        if key:                                   # "time:10:30", "http://…": not syntax, just words
            yield "word", raw.lstrip("-") if neg else raw, neg, "", raw
            continue
        if m.group("phrase") is not None:
            yield "phrase", m.group("phrase"), neg, "", raw
        else:
            yield "word", m.group("word"), neg, "", raw


def parse(q: str, raw_text: bool = False) -> Parsed:
    """raw_text: the free text is one pattern (wildcard, regex, starts / exact / ends with) — only `key:value`
    filters are taken out; quotes, dashes and OR are part of the text."""
    from . import pattern
    p = Parsed()
    if raw_text:
        free = []
        for kind, value, neg, key, raw in tokens(q):
            if key and kind == "kv" and key not in ("name", "content") and not (neg and key not in NEGATABLE):
                p.filters.append({"key": key, "value": value, "neg": neg, "token": raw})
            elif key and key in ("name", "content") and kind == "regex" and not neg:
                p.patterns.append({"side": key, "mode": "regex", "v": value, "token": raw})
            else:
                free.append(raw)
        p.text = " ".join(free)
        if p.text.strip():
            p.clauses = [[{"t": "phrase", "v": p.text}]]
        return p
    free = []
    pending_or = False
    count = 0
    for kind, value, neg, key, raw in tokens(q):
        if kind == "word" and value == "OR" and not neg:
            pending_or = bool(p.clauses)
            continue
        if key:
            if kind == "regex":
                if neg:
                    p.chips.append({"key": key, "label": "can't leave out a pattern", "token": raw, "error": True})
                    continue
                if key not in ("name", "content"):
                    p.chips.append({"key": key, "label": f"{key}: doesn't take a /pattern/", "token": raw, "error": True})
                    continue
                p.patterns.append({"side": key, "mode": "regex", "v": value, "token": raw})
                continue
            if key in ("name", "content"):
                if not value:
                    continue
                if neg:
                    for w in value.split():
                        p.excludes.append({"t": "word", "v": w, "side": key})
                    p.chips.append({"key": key, "label": f"not {value} in the {key}", "token": raw})
                    continue
                if key == "name" and pattern.has_wildcards(value) and not pattern.is_prefix_word(value):
                    p.patterns.append({"side": "name", "mode": "wildcard", "v": value, "token": raw})
                else:
                    p.patterns.append({"side": key, "mode": "words", "v": value, "token": raw})
                continue
            if neg and key not in NEGATABLE:
                p.chips.append({"key": key, "label": f"can't leave out {key}:", "token": raw, "error": True})
                continue
            p.filters.append({"key": key, "value": value, "neg": neg, "token": raw})
            continue
        if not value:
            continue
        count += 1
        if count > MAX_TERMS:
            continue
        if neg:
            p.excludes.append({"t": kind, "v": value, "side": "any"})
            p.chips.append({"key": "not", "label": f"without “{value}”", "token": raw})
            continue
        if kind == "word" and pattern.has_wildcards(value) and not pattern.is_prefix_word(value):
            p.patterns.append({"side": "name", "mode": "wildcard", "v": value, "token": raw})
            continue
        term = {"t": kind, "v": value[:-1] if kind == "word" and pattern.is_prefix_word(value) else value}
        if not term["v"].strip():
            continue
        free.append(term["v"])
        if pending_or and p.clauses:
            p.clauses[-1].append(term)
        else:
            p.clauses.append([term])
        pending_or = False
    p.text = " ".join(free)
    return p
