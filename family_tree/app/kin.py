"""Indian relationship names (§13.1).

`relations.relationship()` gives the canonical walk from the viewer to a
person (`steps`). Here it becomes a **kin key**, one letter per step:

    F M B Z S D H W   father mother brother sister son daughter husband wife
    P X C E           parent sibling child spouse, when the gender isn't known

with `+`/`-` for elder/younger on a sibling step (compared with the person
before it), and — for someone of the viewer's own generation reached through
a sibling (a cousin) — on the last step, compared with the viewer. Examples:
father's elder brother `F.B+`, mother's younger sister `M.Z-`, father's
sister's husband `F.Z.H`, elder brother's wife `B+.W`, an elder parallel
cousin `F.B.S+` (the uncle's own mark is dropped when looking terms up).
The speaker's gender is `@m`/`@f`, used only when looking a term up.

Terms: seed rows (below) for Telugu and Hindi, overridden per family by
custom rows in `kin_terms` (Settings → Relationship names, every change in
History). Lookup takes the most specific match: the exact key with the
speaker's gender, the exact key, then the key with only its last elder/younger
mark, then with none. No match → the English label.
"""
import contextvars
import re
import threading

from . import db

LANGS = {"en": "English", "te": "Telugu", "hi": "Hindi"}
KEY_RE = re.compile(r"^[FMBZSDHWPXCE][+-]?(\.[FMBZSDHWPXCE][+-]?){0,7}(@[mf])?$")

_LETTER = {
    "parent": {"male": "F", "female": "M"}, "sibling": {"male": "B", "female": "Z"},
    "child": {"male": "S", "female": "D"}, "spouse": {"male": "H", "female": "W"},
}
_NEUTRAL = {"parent": "P", "sibling": "X", "child": "C", "spouse": "E"}
_WORD = {"F": "father", "M": "mother", "B": "brother", "Z": "sister", "S": "son", "D": "daughter",
         "H": "husband", "W": "wife", "P": "parent", "X": "sibling", "C": "child", "E": "spouse"}
_GEN = {"F": -1, "M": -1, "P": -1, "S": 1, "D": 1, "C": 1}


# ---------------------------------------------------------------------------
# Elder / younger
# ---------------------------------------------------------------------------

def compare_age(g, x: str, y: str) -> int | None:
    """+1 if y is older than x, -1 if younger, None if unknown. Birth dates
    when both are known (to the precision both have), otherwise birth order
    in a family they share."""
    px, py = g.people.get(x), g.people.get(y)
    if not px or not py:
        return None
    bx, by = px.birth or {}, py.birth or {}
    if bx.get("date_y") and by.get("date_y"):
        kx = (bx["date_y"], bx.get("date_m") or 0, bx.get("date_d") or 0)
        ky = (by["date_y"], by.get("date_m") or 0, by.get("date_d") or 0)
        # compare only as far as both are known
        n = 3 if bx.get("date_d") and by.get("date_d") else 2 if bx.get("date_m") and by.get("date_m") else 1
        if kx[:n] != ky[:n]:
            return 1 if ky[:n] < kx[:n] else -1
    shared = {f for f, _r in px.parent_fams} & {f for f, _r in py.parent_fams}
    for fid in shared:
        pos = {c: i for i, (c, _rel, _p) in enumerate(g.families[fid].children)}
        if x in pos and y in pos and pos[x] != pos[y]:
            return 1 if pos[y] < pos[x] else -1
    return None


# ---------------------------------------------------------------------------
# Kin keys
# ---------------------------------------------------------------------------

def build(g, viewer: str, steps: list | None) -> dict | None:
    """{key, meaning, ageUnknown} for a walk, or None (no walk, or yourself)."""
    if not steps:
        return None
    parts, words = [], []
    age_unknown = False
    gen = 0
    for st in steps:
        gender = g.people[st["id"]].gender
        letter = _LETTER[st["t"]].get(gender) or _NEUTRAL[st["t"]]
        mark = ""
        word = _WORD[letter]
        if st["t"] == "sibling":
            c = compare_age(g, st["from"], st["id"])
            if c is None:
                age_unknown = True
            else:
                mark = "+" if c > 0 else "-"
                word = ("elder " if c > 0 else "younger ") + word
        gen += _GEN.get(letter, 0)
        parts.append(letter + mark)
        words.append(word)
    # a cousin (own generation, reached across a sibling): elder or younger than the viewer
    last = steps[-1]
    if gen == 0 and last["t"] == "child" and any(s["t"] == "sibling" for s in steps):
        c = compare_age(g, viewer, last["id"])
        if c is None:
            age_unknown = True
        else:
            parts[-1] = parts[-1].rstrip("+-") + ("+" if c > 0 else "-")
            words[-1] += ", older than you" if c > 0 else ", younger than you"
    return {"key": ".".join(parts), "meaning": "'s ".join(words), "ageUnknown": age_unknown}


def candidates(key: str, speaker_gender: str | None) -> list:
    """Lookup order: exact, last mark only, no marks — each with @m/@f first."""
    segs = key.split(".")
    last_only = ".".join([s.rstrip("+-") for s in segs[:-1]] + [segs[-1]])
    none = ".".join(s.rstrip("+-") for s in segs)
    sfx = {"male": "@m", "female": "@f"}.get(speaker_gender)
    out = []
    for k in (key, last_only, none):
        for c in ((k + sfx) if sfx else None, k):
            if c and c not in out:
                out.append(c)
    return out


# ---------------------------------------------------------------------------
# Seed terms (editable per family; custom rows override these)
# ---------------------------------------------------------------------------

def _cousins(parallel_m, parallel_f, cross_m, cross_f):
    """Cousin keys from (elder, younger) term pairs: parallel = F.B.* and M.Z.*, cross = F.Z.* and M.B.*."""
    out = {}
    for via, (m, f) in (("F.B", (parallel_m, parallel_f)), ("M.Z", (parallel_m, parallel_f)),
                        ("F.Z", (cross_m, cross_f)), ("M.B", (cross_m, cross_f))):
        out[f"{via}.S+"], out[f"{via}.S-"] = m
        out[f"{via}.D+"], out[f"{via}.D-"] = f
    return out


def _wider(lang: str) -> dict:
    """Keys beyond the close family, built from rules: every
    great-grandparent and great-grandchild, grandparents' brothers and sisters
    (and their partners), parents' cousins, second cousins, cousins' partners
    and children. The explicit SEED entries below win over these."""
    out = {}
    te = lang == "te"
    gp_sides = (("F.F", "F"), ("F.M", "F"), ("M.F", "M"), ("M.M", "M"))
    ggp = ("Mutthatha", "Mutthavva") if te else None
    for g1 in "FM":
        for g2 in "FM":
            for g3, word in (("F", 0), ("M", 1)):
                key = f"{g1}.{g2}.{g3}"
                if te:
                    out[key] = ggp[word]
                else:
                    out[key] = ("Pardada", "Pardadi")[word] if g1 == "F" else ("Parnana", "Parnani")[word]
    for a in "SD":
        for b in "SD":
            for c, word in (("S", 0), ("D", 1)):
                out[f"{a}.{b}.{c}"] = (("Munimanavadu", "Munimanavaralu") if te else ("Parpota", "Parpoti"))[word]
    for gp, parent in gp_sides:
        grandpa = gp.endswith("F")
        paternal = parent == "F"
        if te:
            tata, amma = ("Thatha", "Nanamma" if paternal else "Ammamma")
            # a grandfather's brother / grandmother's sister is a big or little grandfather / grandmother
            if grandpa:
                out[f"{gp}.B+"], out[f"{gp}.B-"], out[f"{gp}.B"] = f"Pedda {tata}", f"Chinna {tata}", f"Pedda {tata} / Chinna {tata}"
                out[f"{gp}.B+.W"], out[f"{gp}.B-.W"], out[f"{gp}.B.W"] = f"Pedda {amma}", f"Chinna {amma}", f"Pedda {amma} / Chinna {amma}"
            else:
                out[f"{gp}.Z+"], out[f"{gp}.Z-"], out[f"{gp}.Z"] = f"Pedda {amma}", f"Chinna {amma}", f"Pedda {amma} / Chinna {amma}"
                out[f"{gp}.Z+.H"], out[f"{gp}.Z-.H"], out[f"{gp}.Z.H"] = f"Pedda {tata}", f"Chinna {tata}", f"Pedda {tata} / Chinna {tata}"
        else:
            base_m, base_f = (("Dada", "Dadi") if paternal else ("Nana", "Nani"))
            if grandpa:
                out[f"{gp}.B"], out[f"{gp}.B.W"] = f"{base_m} (bhai)", f"{base_f} (bhabhi)"
            else:
                out[f"{gp}.Z"], out[f"{gp}.Z.H"] = f"{base_f} (behen)", f"{base_m} (jija)"
        # the parent's cousins count as the parent's brothers and sisters
        for sib in "BZ":
            via = f"{gp}.{sib}"
            if te:
                out[f"{via}.S"] = "Peddananna / Babai" if paternal else "Mamayya"
                out[f"{via}.D"] = "Atta" if paternal else "Peddamma / Pinni"
            else:
                out[f"{via}.S"] = "Chacha / Tauji" if paternal else "Mama"
                out[f"{via}.D"] = "Bua" if paternal else "Mausi"
            # second cousins: brothers and sisters, elder or younger
            for c1 in "SD":
                for c2, pair in (("S", ("Anna", "Tammudu") if te else ("Bhaiya", "Chhota bhai")),
                                 ("D", ("Akka", "Chelli") if te else ("Didi", "Chhoti behen"))):
                    k = f"{via}.{c1}.{c2}"
                    out[k + "+"], out[k + "-"], out[k] = pair[0], pair[1], " / ".join(pair)
    # first cousins' partners and children, like a brother's or sister's
    for via in ("F.B", "F.Z", "M.B", "M.Z"):
        if te:
            out[f"{via}.S.W"], out[f"{via}.D.H"] = "Vadina / Maradalu", "Bava / Maridi"
            out[f"{via}.S.S@m"], out[f"{via}.S.D@m"] = "Koduku", "Kuthuru"
            out[f"{via}.S.S@f"], out[f"{via}.S.D@f"] = "Menalludu", "Menakodalu"
            out[f"{via}.D.S@f"], out[f"{via}.D.D@f"] = "Koduku", "Kuthuru"
            out[f"{via}.D.S@m"], out[f"{via}.D.D@m"] = "Menalludu", "Menakodalu"
            out[f"{via}.S.S"], out[f"{via}.S.D"] = "Anna/Tammudi koduku", "Anna/Tammudi kuthuru"
            out[f"{via}.D.S"], out[f"{via}.D.D"] = "Akka/Chelli koduku", "Akka/Chelli kuthuru"
        else:
            out[f"{via}.S.W"], out[f"{via}.D.H"] = "Bhabhi", "Jijaji"
            out[f"{via}.S.S"], out[f"{via}.S.D"] = "Bhatija", "Bhatiji"
            out[f"{via}.D.S"], out[f"{via}.D.D"] = "Bhanja", "Bhanji"
    # a husband's or wife's sister's or brother's partner
    if te:
        out.update({"W.Z.H": "Todalludu", "H.B.W": "Todikodalu", "W.B.W": "Vadina / Maradalu", "H.Z.H": "Bava / Maridi",
                    "W.B+.W": "Vadina", "W.B-.W": "Maradalu", "H.Z+.H": "Bava", "H.Z-.H": "Maridi"})
    else:
        out.update({"W.Z.H": "Saadhu", "H.B.W": "Jethani / Devrani", "W.B.W": "Salhaj", "H.Z.H": "Nandoi"})
    # step-parents
    out.update({"F.W": "Savati Amma", "M.H": "Savati Nanna"} if te else {"F.W": "Sauteli Maa", "M.H": "Sautele Pita"})
    # a brother's, sister's or cousin's grandchildren are grandchildren too
    gc = {"S.S": "Manavadu", "S.D": "Manavaralu", "D.S": "Manavadu", "D.D": "Manavaralu"} if te else \
         {"S.S": "Pota", "S.D": "Poti", "D.S": "Navasa (Nati)", "D.D": "Navasi (Natin)"}
    for sib in ("B", "Z", "F.B.S", "F.B.D", "F.Z.S", "F.Z.D", "M.B.S", "M.B.D", "M.Z.S", "M.Z.D"):
        for k, t in gc.items():
            out[f"{sib}.{k}"] = t
    # a brother's or sister's son's wife and daughter's husband
    for sib in "BZ":
        out[f"{sib}.S.W"], out[f"{sib}.D.H"] = ("Kodalu", "Alludu") if te else ("Bahu", "Damaad")
    # your children's parents-in-law
    for kid in ("S.W", "D.H"):
        out[f"{kid}.F"], out[f"{kid}.M"] = ("Viyyankudu", "Viyyapuralu") if te else ("Samdhi", "Samdhan")
    for sp in "WH":
        # a husband's or wife's grandparents, uncles and aunts
        if te:
            out.update({f"{sp}.F.F": "Thatha", f"{sp}.F.M": "Nanamma", f"{sp}.M.F": "Thatha", f"{sp}.M.M": "Ammamma",
                        f"{sp}.F.B+": "Pedda Mamagaru", f"{sp}.F.B-": "Chinna Mamagaru", f"{sp}.F.B": "Mamagaru",
                        f"{sp}.M.Z+": "Pedda Attagaru", f"{sp}.M.Z-": "Chinna Attagaru", f"{sp}.M.Z": "Attagaru",
                        f"{sp}.F.Z": "Atta", f"{sp}.M.B": "Mamayya"})
        else:
            out.update({f"{sp}.F.F": "Dada sasur", f"{sp}.F.M": "Dadi saas", f"{sp}.M.F": "Nana sasur",
                        f"{sp}.M.M": "Nani saas", f"{sp}.F.B": "Chachiya sasur", f"{sp}.F.Z": "Phuphiya saas",
                        f"{sp}.M.B": "Mamiya sasur", f"{sp}.M.Z": "Mausiya saas"})
        # a husband's or wife's nephews and nieces: a same-sex sibling's are your own
        same, other = ("Z", "B") if sp == "W" else ("B", "Z")
        if te:
            out.update({f"{sp}.{same}.S": "Koduku", f"{sp}.{same}.D": "Kuthuru",
                        f"{sp}.{other}.S": "Menalludu", f"{sp}.{other}.D": "Menakodalu"})
        else:
            out.update({f"{sp}.B.S": "Bhatija", f"{sp}.B.D": "Bhatiji", f"{sp}.Z.S": "Bhanja", f"{sp}.Z.D": "Bhanji"})
    return out


def _derived(table: dict) -> dict:
    """A husband's or wife's cousins go by the words for their brothers and sisters."""
    out = {}
    for sp in "WH":
        for via in ("F.B", "F.Z", "M.B", "M.Z"):
            for kid, sib in (("S", "B"), ("D", "Z")):
                elder, younger = table.get(f"{sp}.{sib}+"), table.get(f"{sp}.{sib}-")
                if elder:
                    out[f"{sp}.{via}.{kid}+"] = elder
                if younger:
                    out[f"{sp}.{via}.{kid}-"] = younger
                both = table.get(f"{sp}.{sib}") or (f"{elder} / {younger}" if elder and younger and elder != younger
                                                     else elder or younger)
                if both:
                    out[f"{sp}.{via}.{kid}"] = both
    return {**out, **table}


SEED = {
    "te": {
        "F": "Nanna", "M": "Amma",
        "F.F": "Thatha", "F.M": "Nanamma", "M.F": "Thatha", "M.M": "Ammamma",
        "F.F.F": "Mutthatha", "F.F.M": "Mutthavva", "M.M.M": "Mutthavva", "M.M.F": "Mutthatha",
        "F.B+": "Peddananna", "F.B-": "Babai", "F.B": "Peddananna / Babai",
        "F.B+.W": "Peddamma", "F.B-.W": "Pinni", "F.B.W": "Peddamma / Pinni",
        "F.Z": "Atta", "F.Z.H": "Mamayya",
        "M.Z+": "Peddamma", "M.Z-": "Pinni", "M.Z": "Peddamma / Pinni",
        "M.Z+.H": "Peddananna", "M.Z-.H": "Babai", "M.Z.H": "Peddananna / Babai",
        "M.B": "Mamayya", "M.B.W": "Atta",
        "B+": "Anna", "B-": "Tammudu", "B": "Anna / Tammudu",
        "Z+": "Akka", "Z-": "Chelli", "Z": "Akka / Chelli",
        "B+.W": "Vadina", "B-.W": "Maradalu", "B.W": "Vadina / Maradalu",
        "Z+.H": "Bava", "Z-.H": "Maridi", "Z.H": "Bava / Maridi",
        "H": "Bharta (Aayana)", "W": "Bharya (Aavida)",
        "H.F": "Mamagaru", "H.M": "Attagaru", "W.F": "Mamagaru", "W.M": "Attagaru",
        "W.B+": "Bava", "W.B-": "Bavamaridi", "W.B": "Bava / Bavamaridi",
        "W.Z+": "Vadina", "W.Z-": "Maradalu", "W.Z": "Vadina / Maradalu",
        "H.B+": "Bava", "H.B-": "Maridi", "H.B": "Bava / Maridi", "H.Z": "Adapaduchu",
        "S": "Koduku", "D": "Kuthuru", "S.W": "Kodalu", "D.H": "Alludu",
        "S.S": "Manavadu", "S.D": "Manavaralu", "D.S": "Manavadu", "D.D": "Manavaralu",
        "B.S@m": "Koduku", "B.D@m": "Kuthuru", "Z.S@f": "Koduku", "Z.D@f": "Kuthuru",
        "B.S@f": "Menalludu", "B.D@f": "Menakodalu", "Z.S@m": "Menalludu", "Z.D@m": "Menakodalu",
        "B.S": "Anna/Tammudi koduku", "B.D": "Anna/Tammudi kuthuru",
        "Z.S": "Akka/Chelli koduku", "Z.D": "Akka/Chelli kuthuru",
        **_cousins(("Anna", "Tammudu"), ("Akka", "Chelli"), ("Bava", "Bavamaridi"), ("Vadina", "Maradalu")),
        "F.B.S": "Anna / Tammudu", "F.B.D": "Akka / Chelli", "M.Z.S": "Anna / Tammudu", "M.Z.D": "Akka / Chelli",
        "F.Z.S": "Bava / Bavamaridi", "F.Z.D": "Vadina / Maradalu",
        "M.B.S": "Bava / Bavamaridi", "M.B.D": "Vadina / Maradalu",
    },
    "hi": {
        "F": "Pitaji (Papa)", "M": "Maa",
        "F.F": "Dada", "F.M": "Dadi", "M.F": "Nana", "M.M": "Nani",
        "F.F.F": "Pardada", "F.F.M": "Pardadi", "M.M.F": "Parnana", "M.M.M": "Parnani",
        "F.B+": "Tauji", "F.B-": "Chacha", "F.B": "Tauji / Chacha",
        "F.B+.W": "Taiji", "F.B-.W": "Chachi", "F.B.W": "Taiji / Chachi",
        "F.Z": "Bua", "F.Z.H": "Phupha",
        "M.B": "Mama", "M.B.W": "Mami", "M.Z": "Mausi", "M.Z.H": "Mausa",
        "B+": "Bhaiya", "B-": "Chhota bhai", "B": "Bhai",
        "Z+": "Didi", "Z-": "Chhoti behen", "Z": "Behen",
        "B+.W": "Bhabhi", "B-.W": "Bhabhi (chhoti)", "B.W": "Bhabhi",
        "Z.H": "Jijaji",
        "H": "Pati", "W": "Patni",
        "H.F": "Sasurji", "H.M": "Saasuma", "W.F": "Sasurji", "W.M": "Saasuma",
        "W.B": "Saala", "W.Z": "Saali", "W.Z.H": "Saadhu",
        "H.B+": "Jeth", "H.B-": "Devar", "H.B": "Jeth / Devar", "H.Z": "Nanad",
        "H.B+.W": "Jethani", "H.B-.W": "Devrani", "W.B.W": "Salhaj",
        "S": "Beta", "D": "Beti", "S.W": "Bahu", "D.H": "Damaad",
        "S.S": "Pota", "S.D": "Poti", "D.S": "Navasa (Nati)", "D.D": "Navasi (Natin)",
        "B.S": "Bhatija", "B.D": "Bhatiji", "Z.S": "Bhanja", "Z.D": "Bhanji",
        "F.B.S": "Chachera bhai", "F.B.D": "Chacheri behen", "F.Z.S": "Phuphera bhai", "F.Z.D": "Phupheri behen",
        "M.B.S": "Mamera bhai", "M.B.D": "Mameri behen", "M.Z.S": "Mausera bhai", "M.Z.D": "Mauseri behen",
    },
}
SEED = {lang: _derived({**_wider(lang), **seed}) for lang, seed in SEED.items()}
NOTES = {("te", "F.B-"): "also: Chinnanna", ("te", "F.B-.W"): "also: Chinnamma",
         ("te", "M.B"): "also: Menamama", ("te", "F.Z"): "also: Menatta",
         ("hi", "F.Z.H"): "also: Fufa", ("hi", "B+"): "also: Bhai sahab"}


def meaning_of_key(key: str) -> str:
    """"F.B-" → "father's younger brother" (for the terms editor)."""
    base, _, spk = key.partition("@")
    words = []
    segs = base.split(".")
    for i, seg in enumerate(segs):
        letter, mark = seg[0], seg[1:]
        w = _WORD[letter]
        if mark and i == len(segs) - 1 and sum(_GEN.get(s[0], 0) for s in segs) == 0 and len(segs) > 1 \
                and any(s[0] in "BZX" for s in segs[:-1]):
            w += ", older than you" if mark == "+" else ", younger than you"
        elif mark:
            w = ("elder " if mark == "+" else "younger ") + w
        words.append(w)
    out = "'s ".join(words)
    if spk:
        out += " (said by a man)" if spk == "m" else " (said by a woman)"
    return out


# ---------------------------------------------------------------------------
# Terms in use: seed + custom rows, cached until the next change
# ---------------------------------------------------------------------------

_lock = threading.Lock()
_cache: dict | None = None


def invalidate() -> None:
    global _cache
    with _lock:
        _cache = None


def terms(conn=None) -> dict:
    """{lang: {key: {"term", "note", "source"}}} for te and hi. Cached until
    the tree_version changes (every history batch — term edits and their undo
    included — writes a new one)."""
    global _cache

    def _get(c):
        global _cache
        version = db.get_setting(c, "tree_version", "0")
        with _lock:
            if _cache is not None and _cache[0] == version:
                return _cache[1]
        out = {lang: {k: {"term": t, "note": NOTES.get((lang, k)), "source": "seed"} for k, t in seed.items()}
               for lang, seed in SEED.items()}
        for r in c.execute("SELECT lang, kin_key, term, note FROM kin_terms"):
            if r["lang"] in out:
                out[r["lang"]][r["kin_key"]] = {"term": r["term"], "note": r["note"], "source": "custom"}
        with _lock:
            _cache = (version, out)
        return out
    if conn is not None:
        return _get(conn)
    with db.get_conn() as c:
        return _get(c)


def lookup(table: dict, key: str, speaker_gender: str | None) -> tuple[str, dict] | None:
    for k in candidates(key, speaker_gender):
        if k in table:
            return k, table[k]
    return None


_SEG_GENDER = {"F": "male", "B": "male", "S": "male", "H": "male",
               "M": "female", "Z": "female", "D": "female", "W": "female"}


def _first(term: str) -> str:
    """"Vadina / Maradalu" → "Vadina", "Pitaji (Papa)" → "Pitaji": one word per part when composing."""
    return re.sub(r"\s*\(.*?\)", "", term.split("/")[0]).strip()


def compose(table: dict, key: str, speaker_gender: str | None, lang: str) -> str | None:
    """A term built from parts when the whole key has none: the longest leading
    part that has a term, then the rest seen from that relative ("Mamayya
    Koduku" — uncle's son; hi "Mama ka Beta"). None when a part has no term."""
    segs = key.split(".")
    for cut in range(len(segs) - 1, 0, -1):
        head = lookup(table, ".".join(segs[:cut]), speaker_gender)
        if not head:
            continue
        rest_key = ".".join(segs[cut:])
        rest_gender = _SEG_GENDER.get(segs[cut - 1][0])
        rest = lookup(table, rest_key, rest_gender)
        tail = _first(rest[1]["term"]) if rest else compose(table, rest_key, rest_gender, lang)
        if not tail:
            continue
        a = _first(head[1]["term"])
        if lang == "hi":
            joint = {"male": "ka", "female": "ki"}.get(_SEG_GENDER.get(segs[-1][0]), "ke")
            return f"{a} {joint} {tail}"
        return f"{a} {tail}"
    return None


def term_for(table: dict, key: str, speaker_gender: str | None, lang: str) -> tuple[str | None, dict]:
    """(termKey, {"term", "note"}) — termKey None for a composed term — or (None, {}) without one."""
    hit = lookup(table, key, speaker_gender)
    if hit:
        return hit[0], hit[1]
    made = compose(table, key, speaker_gender, lang) or _either_gender(table, key, speaker_gender, lang)
    return (None, {"term": made, "note": None, "composed": True}) if made else (None, {})


_GENDERED = {"P": "FM", "X": "BZ", "C": "SD", "E": "HW"}


def _either_gender(table: dict, key: str, speaker_gender: str | None, lang: str) -> str | None:
    """Someone whose gender isn't set (key letters P X C E): both words, "Koduku / Kuthuru"."""
    segs = key.split(".")
    at = [i for i, sg in enumerate(segs) if sg[0] in _GENDERED]
    if len(at) != 1:
        return None
    i = at[0]
    words = []
    for letter in _GENDERED[segs[i][0]]:
        k = ".".join(segs[:i] + [letter + segs[i][1:]] + segs[i + 1:])
        hit = lookup(table, k, speaker_gender)
        t = _first(hit[1]["term"]) if hit else compose(table, k, speaker_gender, lang)
        if not t:
            return None
        if t not in words:
            words.append(t)
    return " / ".join(words)


def describe(g, viewer: str, rel: dict, lang: str, conn=None) -> dict:
    """The viewer-facing relationship: rel plus {english, term, termKey, kinKey,
    meaning, ageUnknown, note}; `label` becomes the term when there is one."""
    out = dict(rel)
    out["english"] = rel.get("label")
    out.update(term=None, kinKey=None, termKey=None, meaning=None, ageUnknown=False, note=None)
    if lang not in SEED or rel.get("kind") in ("self", "none") or not rel.get("steps"):
        return out
    k = build(g, viewer, rel.get("steps"))
    if not k:
        return out
    out.update(kinKey=k["key"], meaning=k["meaning"], ageUnknown=k["ageUnknown"])
    tkey, t = term_for(terms(conn)[lang], k["key"], g.people[viewer].gender, lang)
    if t:
        out.update(term=t["term"], termKey=tkey, note=t["note"], label=t["term"], composed=bool(t.get("composed")))
    return out


ENGLISH_WORDS = {"father", "mother", "parent", "brother", "sister", "sibling", "son", "daughter", "child", "husband",
                 "wife", "spouse", "partner", "uncle", "aunt", "nephew", "niece", "cousin", "grandfather",
                 "grandmother", "grandparent", "grandson", "granddaughter", "grandchild", "in-law",
                 "father-in-law", "mother-in-law", "brother-in-law", "sister-in-law", "son-in-law",
                 "daughter-in-law", "stepfather", "stepmother", "stepson", "stepdaughter"}


def _words(text: str) -> list:
    return [w.strip().lower() for w in re.split(r"\s*/\s*|[()]", text or "") if w.strip()]


def is_kin_word(needle: str, lang: str) -> bool:
    """Is this search text a relationship word (worth naming everyone to match it)?"""
    n = needle.strip().lower()
    if not n:
        return False
    base = n[:-1] if n.endswith("s") and n[:-1] in ENGLISH_WORDS else n
    if base in ENGLISH_WORDS or any(base in w.split() for w in ENGLISH_WORDS if " " in w):
        return True
    if lang in SEED:
        return any(n == w or n == w.split()[0] for t in terms()[lang].values() for w in _words(t["term"]))
    return False


def word_matches(needle: str, label: str | None) -> bool:
    if not label:
        return False
    n = needle.strip().lower()
    n = n[:-1] if n.endswith("s") and n[:-1] in ENGLISH_WORDS else n
    lab = label.lower()
    return n in _words(label) or n in lab.replace("-", " ").split() or n == lab or n in lab.split()


def label_for(g, viewer: str | None, pid: str, lang: str | None = None, rel: dict | None = None) -> str | None:
    """The label `viewer` should see for `pid`: the term in their language when
    there is one, else the English label."""
    if not viewer or viewer not in g.people or pid not in g.people:
        return None
    from . import relations
    rel = rel or relations.relationship(g, viewer, pid)
    lang = lang or current()
    if lang not in SEED or rel["kind"] not in ("blood", "inlaw"):
        return rel["label"]
    k = build(g, viewer, rel.get("steps"))
    t = term_for(terms()[lang], k["key"], g.people[viewer].gender, lang)[1] if k else {}
    return t.get("term") or rel["label"]


def labeler(g, viewer: str | None, lang: str | None = None):
    """A cached pid → label function for one viewer, or None without a viewer."""
    if not viewer or viewer not in g.people:
        return None
    lang = lang or current()
    cache: dict = {}

    def label(pid):
        if pid not in cache:
            cache[pid] = label_for(g, viewer, pid, lang)
        return cache[pid]
    return label


# The language of the request being served: set by auth.require_user from the
# user's own choice or the App setting. Background work (reminders) passes the
# language explicitly instead.
_current: contextvars.ContextVar = contextvars.ContextVar("kin_lang", default=None)


def set_current(lang: str | None) -> None:
    _current.set(lang)


def current() -> str:
    from . import features, settings
    if not features.on("kin_names"):
        return "en"
    lang = _current.get()
    if lang in LANGS:
        return lang
    return settings.get("relationship_language")


def user_lang(user: dict) -> str:
    """The user's own choice, else the household's App setting — English while
    Indian relationship names are switched off (Features)."""
    from . import features, settings
    if not features.on("kin_names"):
        return "en"
    lang = user.get("kin_lang")
    return lang if lang in LANGS else settings.get("relationship_language")
