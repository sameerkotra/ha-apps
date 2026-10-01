"""Relationship search (§13.17): "first cousins of me", "all my
Babais", "descendants of Venkat", "everyone on Amma's side", "in-laws of Ravi".

A picker, not free text: `rel` (a category or a kin word), of `anchor`, plus
filters (side, living, generation, gender). Everyone within 6 steps of the
anchor is named once (relationship, kin label, generation) and cached per tree
version, anchor and language. `parse()` turns typed phrases into picker values.
"""
import re
import threading

from . import db, graph as graph_mod, kin, relations, sides

MAX_STEPS = 6

RELS = {
    "relatives": "Everyone related", "blood": "Blood relatives", "parents": "Parents",
    "grandparents": "Grandparents", "ancestors": "Ancestors", "children": "Children",
    "grandchildren": "Grandchildren", "descendants": "Descendants", "siblings": "Brothers and sisters",
    "uncles_aunts": "Uncles and aunts", "nieces_nephews": "Nieces and nephews", "first_cousins": "First cousins",
    "cousins": "All cousins", "partners": "Partners", "in_laws": "In-laws",
}


def _counts(steps):
    """(g1, g2) for a blood walk: generations up to the common ancestor, and down from it."""
    up = sum(1 for s in steps if s["t"] == "parent")
    down = sum(1 for s in steps if s["t"] == "child")
    if any(s["t"] == "sibling" for s in steps):
        up, down = up + 1, down + 1
    return up, down


def _within(g, anchor: str, limit: int = MAX_STEPS) -> set:
    return g.within(anchor, limit) - {anchor}


_lock = threading.Lock()
_cache: dict = {}


def named(conn, g, anchor: str, lang: str) -> dict:
    """{pid: {kind, g1, g2, gen, label, english, steps}} for everyone within 6 steps."""
    key = (db.get_setting(conn, "tree_version", "0"), anchor, lang)
    with _lock:
        if key in _cache:
            return _cache[key]
    out = {}
    for pid in _within(g, anchor):
        rel = relations.relationship(g, anchor, pid)
        if rel["kind"] in ("none", "self"):
            continue
        g1 = g2 = None
        if rel["kind"] == "blood":
            g1, g2 = _counts(rel["steps"])
        out[pid] = {"kind": rel["kind"], "g1": g1, "g2": g2, "gen": rel["gen"], "english": rel["label"],
                    "label": kin.label_for(g, anchor, pid, lang, rel), "steps": rel["steps"]}
    with _lock:
        if len(_cache) > 100:
            _cache.clear()
        _cache[key] = out
    return out


def matches(info: dict, rel: str) -> bool:
    kind, g1, g2 = info["kind"], info["g1"], info["g2"]
    if rel == "relatives":
        return True
    if rel == "blood":
        return kind == "blood"
    if rel == "partners":
        return kind == "inlaw" and info["steps"] is not None and len(info["steps"]) == 1
    if rel == "in_laws":
        return kind in ("inlaw", "marriage") and not (info["steps"] is not None and len(info["steps"]) == 1)
    if kind != "blood":
        return False
    return {
        "parents": (g1, g2) == (1, 0), "grandparents": (g1, g2) == (2, 0), "ancestors": g2 == 0 and g1 >= 1,
        "children": (g1, g2) == (0, 1), "grandchildren": (g1, g2) == (0, 2), "descendants": g1 == 0 and g2 >= 1,
        "siblings": (g1, g2) == (1, 1), "uncles_aunts": (g1, g2) == (2, 1), "nieces_nephews": (g1, g2) == (1, 2),
        "first_cousins": (g1, g2) == (2, 2), "cousins": g1 >= 2 and g2 >= 2,
    }.get(rel, False)


def search(conn, g, anchor: str, lang: str, rel: str = "relatives", term: str | None = None, side: str | None = None,
           living: str | None = None, generation: int | None = None, gender: str | None = None) -> list:
    info = named(conn, g, anchor, lang)
    side_map = sides.get(conn, g, anchor) if side else {}
    out = []
    for pid, it in info.items():
        if not matches(it, rel):
            continue
        if term and not (kin.word_matches(term, it["label"]) or kin.word_matches(term, it["english"])):
            continue
        p = g.people[pid]
        if gender and p.gender != gender:
            continue
        if side and side_map.get(pid) not in (side, "both"):
            continue
        alive = graph_mod.living(p)
        if living == "living" and not alive or living == "deceased" and alive:
            continue
        if generation is not None and it["gen"] != generation:
            continue
        out.append((pid, it))
    order = {"blood": 0, "inlaw": 1, "marriage": 2}
    out.sort(key=lambda x: (order[x[1]["kind"]], (x[1]["g1"] or 0) + (x[1]["g2"] or 0), x[1]["gen"] if x[1]["gen"] is not None else 99,
                            g.people[x[0]].name.lower()))
    return out


# ---------------------------------------------------------------------------
# Typed phrases → picker values
# ---------------------------------------------------------------------------

_PHRASES = [
    # (words, rel, gender) — longest first
    ("aunts and uncles", "uncles_aunts", None), ("uncles and aunts", "uncles_aunts", None),
    ("nieces and nephews", "nieces_nephews", None), ("nephews and nieces", "nieces_nephews", None),
    ("brothers and sisters", "siblings", None), ("blood relatives", "blood", None),
    ("first cousins", "first_cousins", None), ("great grandparents", "ancestors", None),
    ("grandparents", "grandparents", None), ("grandfathers", "grandparents", "male"),
    ("grandmothers", "grandparents", "female"), ("grandchildren", "grandchildren", None),
    ("grandsons", "grandchildren", "male"), ("granddaughters", "grandchildren", "female"),
    ("ancestors", "ancestors", None), ("descendants", "descendants", None),
    ("parents", "parents", None), ("children", "children", None), ("kids", "children", None),
    ("sons", "children", "male"), ("daughters", "children", "female"),
    ("siblings", "siblings", None), ("brothers", "siblings", "male"), ("sisters", "siblings", "female"),
    ("uncles", "uncles_aunts", "male"), ("aunts", "uncles_aunts", "female"),
    ("nephews", "nieces_nephews", "male"), ("nieces", "nieces_nephews", "female"),
    ("cousins", "cousins", None), ("in-laws", "in_laws", None), ("in laws", "in_laws", None),
    ("partners", "partners", None), ("spouses", "partners", None), ("husbands", "partners", "male"),
    ("wives", "partners", "female"),
    ("relatives", "relatives", None), ("relations", "relatives", None), ("family", "relatives", None),
    ("everyone", "relatives", None), ("everybody", "relatives", None), ("people", "relatives", None),
]
_SINGULAR = {"parent": "parents", "child": "children", "son": "sons", "daughter": "daughters", "sibling": "siblings",
             "brother": "brothers", "sister": "sisters", "uncle": "uncles", "aunt": "aunts", "nephew": "nephews",
             "niece": "nieces", "cousin": "cousins", "first cousin": "first cousins", "grandparent": "grandparents",
             "grandchild": "grandchildren", "in-law": "in-laws", "partner": "partners", "spouse": "spouses",
             "husband": "husbands", "wife": "wives", "grandfather": "grandfathers", "grandmother": "grandmothers",
             "grandson": "grandsons", "granddaughter": "granddaughters", "ancestor": "ancestors",
             "descendant": "descendants"}
_ME = {"me", "my", "mine", "myself", "i", "you", "your"}
_SIDE_WORDS = {"paternal": "paternal", "father": "paternal", "fathers": "paternal", "dad": "paternal",
               "dads": "paternal", "papa": "paternal", "daddy": "paternal", "maternal": "maternal",
               "mother": "maternal", "mothers": "maternal", "mom": "maternal", "moms": "maternal", "mum": "maternal",
               "mums": "maternal", "mummy": "maternal", "mommy": "maternal", "ma": "maternal"}


class ParseError(ValueError):
    pass


def _side_word(word: str) -> str | None:
    w = word.strip().lower().rstrip("'").removesuffix("'s").removesuffix("’s")
    if w in _SIDE_WORDS:
        return _SIDE_WORDS[w]
    for lang in kin.SEED:
        table = kin.terms()[lang]
        for key, side in (("F", "paternal"), ("M", "maternal")):
            t = table.get(key)
            if t and w in kin._words(t["term"]) + [x.split()[0] for x in kin._words(t["term"])]:
                return side
    return None


def _find_person(g, name: str):
    n = " ".join(name.lower().split())
    exact = [p for p in g.people.values() if p.name.lower() == n]
    if exact:
        return exact[0]
    given = [p for p in g.people.values() if (p.given or "").lower() == n or (p.nickname or "").lower() == n]
    if given:
        return sorted(given, key=lambda p: p.name)[0]
    parts = n.split()
    loose = [p for p in g.people.values()
             if all(x in " ".join(filter(None, (p.given, p.surname, p.nickname, p.birth_surname))).lower() for x in parts)]
    return sorted(loose, key=lambda p: p.name)[0] if loose else None


def parse(g, text: str, me: str | None, lang: str) -> dict:
    """{anchor, rel, gender, side, term, understood}. ParseError with a hint."""
    t = " ".join((text or "").lower().replace("’", "'").split())
    t = re.sub(r"^(show|find|list|who are|who is)\s+", "", t)
    t = re.sub(r"^(all of|all|every|everyone who is|the)\s+", "", t)
    side = None
    m = re.search(r"(?:\bon\s+)?(?:the\s+)?(\S+?)(?:'s)?\s+side$", t)
    if m:
        side = _side_word(m.group(1))
        if side is None:
            raise ParseError(f"I don't know whose side “{m.group(1)}” is — try father's side or mother's side.")
        t = t[:m.start()].strip()
        t = re.sub(r"\s+(on|from)$", "", t)
    anchor_name = None
    m = re.match(r"^(.*?)\s+of\s+(.+)$", t)
    if m:
        t, anchor_name = m.group(1).strip(), m.group(2).strip()
    else:
        m = re.match(r"^(.+?)'s\s+(.+)$", t)
        if m and m.group(1) not in ("father", "mother"):
            anchor_name, t = m.group(1).strip(), m.group(2).strip()
    if t in _ME:
        t = ""
    if t.startswith("my "):
        anchor_name = anchor_name or "me"
        t = t[3:]
    t = re.sub(r"^(all|every)\s+", "", t).strip()
    if anchor_name is None or anchor_name in _ME:
        if not me:
            raise ParseError("Set “This is me” first, or say whose: “cousins of Ravi”.")
        anchor = me
    else:
        p = _find_person(g, anchor_name)
        if not p:
            raise ParseError(f"Nobody called “{anchor_name}” is in the tree.")
        anchor = p.id
    rel, gender, term = "relatives", None, None
    word = _SINGULAR.get(t, t)
    if word:
        for phrase, r, gdr in _PHRASES:
            if word == phrase:
                rel, gender = r, gdr
                break
        else:
            bare = word[:-1] if word.endswith("s") else word
            langs = [lang] + [x for x in kin.SEED if x != lang]
            hit = None
            for lg in langs:
                if lg in kin.SEED and (kin.is_kin_word(word, lg) or kin.is_kin_word(bare, lg)):
                    hit = word if kin.is_kin_word(word, lg) else bare
                    break
            if not hit:
                raise ParseError(f"I don't know “{word}” — try cousins, uncles, descendants, in-laws, or a word like Babai.")
            term = hit
    understood = " ".join(filter(None, [
        f"“{term}”" if term else RELS[rel].lower() if not gender else RELS[rel].lower() + f" ({gender})",
        "of " + ("you" if anchor == me else g.people[anchor].name),
        {"paternal": "on the father's side", "maternal": "on the mother's side"}.get(side)]))
    return {"anchor": anchor, "anchorName": g.people[anchor].name, "rel": rel, "gender": gender, "side": side,
            "term": term, "understood": understood}
