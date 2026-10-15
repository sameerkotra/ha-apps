"""Reading a normalised question (HOUSEHOLD_ASSISTANT_SPEC.md §17.4–17.5): which concepts it mentions, which of the
household's own names, and what words are left over for a search, an item or a person.

    u = read("whats on the costco list tmrw", today, vocab)
    u.has("LIST") -> 3.0     u.names["list_name"] -> [(3, 4, "Costco")]     u.slots.when -> "tomorrow"
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from functools import lru_cache

from . import lexicon, normalise, slots as slots_mod
from .normalise import NUM, WORD, Token

CONCEPT_WEIGHT = 3.0
FIXED_FACTOR = 0.9          # a typo-corrected word counts a little less (2.7 instead of 3, §17.6)

def _norm_phrase(p: str) -> tuple[str, ...]:
    """A lexicon phrase in the questions' own normalised form ("cheaper elsewhere" -> "cheap elsewhere")."""
    return tuple(normalise.FORMS.get(w, w) for w in p.split())


_PHRASES = sorted({(c, _norm_phrase(p)) for c, ps in lexicon.CONCEPTS.items() for p in ps},
                  key=lambda x: (-len(x[1]), x[0], x[1]))
_BY_FIRST: dict[str, list] = {}
for _c, _w in _PHRASES:
    _BY_FIRST.setdefault(_w[0], []).append((_c, _w))
_TARGETS = lexicon.all_words() | lexicon.GROCERIES | set(normalise.FORMS.values()) | set(normalise.FORMS)
_KNOWN = (lexicon.EVERYDAY | lexicon.STOPWORDS | lexicon.GROCERIES | lexicon.STORES
          | set(slots_mod.MONTHS) | set(slots_mod.WEEKDAYS) | set(normalise.FORMS) | set(normalise.FORMS.values()))


@dataclass
class Vocab:
    """The household's own names, as this person sees them (vocab.py fetches them; the tests make their own)."""
    lists: list = field(default_factory=list)          # Todo list names
    items: list = field(default_factory=list)          # shopping list items
    stores: list = field(default_factory=list)         # stores in Receipts
    groups: list = field(default_factory=list)         # Splitpot groups
    people: list = field(default_factory=list)         # people (Home Assistant's, Splitpot's)
    categories: list = field(default_factory=list)     # Finance categories
    games: list = field(default_factory=list)          # Arcade games
    app_words: dict = field(default_factory=dict)      # tool -> [phrases] taught by the app (§17.4 "quick")

    def kinds(self):
        return (("list_name", self.lists), ("item", self.items), ("store", self.stores), ("group", self.groups),
                ("person", self.people), ("category", self.categories), ("game", self.games))


@dataclass
class Utterance:
    text: str                                           # as asked
    toks: list                                          # normalised tokens
    slots: slots_mod.Slots
    hits: dict = field(default_factory=dict)            # concept -> [(start, end, fixed)]
    names: dict = field(default_factory=dict)           # kind -> [(start, end, value)]
    used: set = field(default_factory=set)              # token positions taken by concepts, slots and names

    @property
    def words(self) -> list[str]:
        return [t.text for t in self.toks]

    def has(self, concept: str) -> float:
        """The weight of a concept the question mentions (0 when it doesn't)."""
        spans = self.hits.get(concept)
        if not spans:
            return 0.0
        return CONCEPT_WEIGHT * (FIXED_FACTOR if all(fixed for _s, _e, fixed in spans) else 1.0)

    def name(self, kind: str):
        v = self.names.get(kind)
        return v[0][2] if v else None

    def all_names(self, kind: str) -> list:
        return [x[2] for x in self.names.get(kind, [])]

    def leftover(self, *, keep_concepts=(), drop=(), stop=True) -> list[str]:
        """The words not taken by a concept, a slot or a name (except those of `keep_concepts`), without filler and
        stop words: what a search, an item or a task's text is made of."""
        keep = set()
        for c in keep_concepts:
            for s, e, _f in self.hits.get(c, []):
                keep.update(range(s, e))
        out = []
        for i, t in enumerate(self.toks):
            if (i in self.used and i not in keep) or t.text in drop:
                continue
            if stop and t.text in lexicon.STOPWORDS:
                continue
            out.append(t.text)
        return out

    def free(self, *, drop=(), keep_concepts=()) -> str:
        return " ".join(self.leftover(drop=drop, keep_concepts=keep_concepts))

    def name_words(self) -> list[str]:
        """Words that could be a person's name: the household's people, then left-over words that aren't everyday
        words or the lexicon's."""
        people = {a: v.casefold() for a, _b, v in self.names.get("person", [])}
        inside = {i for a, b, _v in self.names.get("person", []) for i in range(a, b)}
        out = []
        for i, t in enumerate(self.toks):
            if i in people:
                out.append(people[i])
            elif (i not in inside and i not in self.used and t.text not in lexicon.STOPWORDS
                  and t.text not in lexicon.EVERYDAY and t.text not in _TARGETS and not t.text.isdigit()
                  and len(t.text) > 1 and t.kind == WORD):
                out.append(t.text)
        return list(dict.fromkeys(out))                  # in the order they were said

    def own_text(self, *concepts, drop=(), names=()) -> str:
        """The words of a task's or a reminder's own text: everything but the slots and the spans of `concepts`
        (so "take the bins out" keeps "bins", which is also a schedule word)."""
        skip = set(self.slots.used)
        for c in concepts:
            for a, b, _f in self.hits.get(c, []):
                skip.update(range(a, b))
        for k in names:
            for a, b, _v in self.names.get(k, []):
                skip.update(range(a, b))
        words = [t.orig for i, t in enumerate(self.toks) if i not in skip and t.text not in drop]
        lead = ("to", "that", "me", "about", "i", "need", "please", "a", "the", "for", "on", "of", "is", "it", "as",
                "my", "our", "us", "off", "with", "have", "am", "has", "just", "been", "new", "an", "we", "are")
        while words and words[0] in lead:
            words.pop(0)
        while words and words[-1] in ("to", "on", "in", "my", "our", "list", "lists", "the", "please", "a", "for",
                                      "as", "is", "it", "of", "off", "done", "from"):
            words.pop()
        return " ".join(words)

    def only(self, *allowed_kinds) -> bool:
        """True when nothing is left but slots, stop words and the given kinds of names (and no concept at all)."""
        if any(c not in lexicon.HELPERS for c in self.hits):
            return False
        for i, t in enumerate(self.toks):
            if i in self.slots.used or t.text in lexicon.STOPWORDS:
                continue
            if any(s <= i < e for k in allowed_kinds for s, e, _v in self.names.get(k, [])):
                continue
            return False
        return True


def _seq_at(toks, i, words) -> bool:
    return i + len(words) <= len(toks) and all(toks[i + k].text == w for k, w in enumerate(words))


@lru_cache(maxsize=4096)
def _name_tokens(value: str) -> tuple[str, ...]:
    return tuple(t.text for t in normalise.tokens(value))


def read(text: str, today: date, vocab: Vocab | None = None, *, fix_typos: bool = True) -> Utterance:
    """`fix_typos` False: the words as typed (understand() tries that first, so a real word like "plants" is never
    turned into a lexicon word like "plans" when the question is understood without it)."""
    vocab = vocab or Vocab()
    toks = normalise.strip_filler(normalise.tokens(text))
    names_known = set()
    for _k, values in vocab.kinds():
        for v in values:
            names_known.update(_name_tokens(v))
    for phrases in vocab.app_words.values():
        for p in phrases:
            names_known.update(_name_tokens(p))
    if fix_typos:
        toks = normalise.correct(toks, _TARGETS | names_known, _KNOWN | names_known)
    s = slots_mod.read(toks, today)
    u = Utterance(text=text, toks=toks, slots=s)
    # concepts: every phrase occurrence, then a hit inside a longer hit of another concept is dropped
    raw = []
    for i, t in enumerate(toks):
        for concept, words in _BY_FIRST.get(t.text, ()):
            if _seq_at(toks, i, words):
                if any(j in s.used for j in range(i, i + len(words))) and concept not in ("TASK", "BILL", "CALORIE"):
                    continue
                raw.append((concept, i, i + len(words), any(toks[j].fixed for j in range(i, i + len(words)))))
    kept = []
    for h in raw:
        c, a, b, _f = h
        if any(c2 != c and a2 <= a and b <= b2 and (b2 - a2) > (b - a) for c2, a2, b2, _ in raw):
            continue
        if any(c2 == c and a2 <= a and b <= b2 and (b2 - a2) > (b - a) for c2, a2, b2, _ in kept):
            continue
        kept.append(h)
    for c, a, b, f in kept:
        u.hits.setdefault(c, []).append((a, b, f))
        if c not in lexicon.HELPERS:
            u.used.update(range(a, b))
    u.used.update(s.used)
    # the household's names: longest first
    present = {t.text for t in toks}
    for kind, values in vocab.kinds():
        for v in sorted(set(values), key=lambda x: -len(_name_tokens(x))):
            words = _name_tokens(v)
            if not words or words[0] not in present:
                continue
            for i in range(len(toks)):
                if _seq_at(toks, i, words):
                    u.names.setdefault(kind, []).append((i, i + len(words), v))
                    u.used.update(range(i, i + len(words)))
    for tool, phrases in vocab.app_words.items():
        for p in phrases:
            words = _name_tokens(p)
            for i in range(len(toks)):
                if words and _seq_at(toks, i, words):
                    u.names.setdefault("app_word", []).append((i, i + len(words), tool))
                    u.used.update(range(i, i + len(words)))
    # everyday items and stores the household hasn't named yet
    for i, t in enumerate(toks):
        if t.kind != WORD:
            continue
        if t.text in lexicon.GROCERIES and i not in u.used and not any(s <= i < e for s, e, _v in u.names.get("item", [])):
            u.names.setdefault("item", []).append((i, i + 1, t.text))
        if t.text in lexicon.STORES and not any(s <= i < e for s, e, _v in u.names.get("store", [])):
            u.names.setdefault("store", []).append((i, i + 1, t.text.title()))
            u.used.add(i)
    return u


def template(u: Utterance) -> str:
    """The question with its slots and names as placeholders, for the learned phrases (§17.10):
    "what is on the costco list tomorrow" -> "what is on the {name} list {when}"."""
    marks = {}
    for a, b, kind in u.slots.words:
        marks[a] = (b, "{" + ("when" if kind in ("when", "days", "date") else kind) + "}")
    for kind, spans in u.names.items():
        if kind == "app_word":
            continue
        for a, b, _v in spans:
            if a not in marks:
                marks[a] = (b, "{name}")
    out, i = [], 0
    while i < len(u.toks):
        if i in marks:
            b, ph = marks[i]
            if not out or out[-1] != ph:
                out.append(ph)
            i = max(b, i + 1)
            continue
        t = u.toks[i]
        if t.kind == NUM:
            out.append("{n}")
        elif t.text not in ("please", "the", "a", "an"):
            out.append(t.text)
        i += 1
    return " ".join(out)


def tokens_text(u: Utterance) -> str:
    return " ".join(t.text for t in u.toks)


__all__ = ["Vocab", "Utterance", "read", "template", "Token"]
