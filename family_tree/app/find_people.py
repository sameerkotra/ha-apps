"""Finding a person from a name someone said or typed, when it may not be spelled the way the tree has it
(the Household Assistant's `tree.relation`; spec §13.20).

`find(conn, g, text, me, lang)` answers one of:

- `{"id": pid, "how": "me" | "exact" | "close" | "relation"}` — one person;
- `{"choices": [pid, …]}` — several people fit about equally well (two Ravis), best first;
- `{"none": [pid, …]}` — nobody fits; the nearest few names, if any are near at all.

What counts as a name: given names, surname, birth surname, nickname, other names, and the names in Telugu/Hindi
script while that feature is on. A word is compared as written, then as it sounds once common spelling differences
in Indian names are folded together (Lakshmi/Laxmi, Sreenivas/Srinivas, Vijay/Vijai, Kotha/Kota …), then as the
start of a longer name ("Venkat" → Venkateswara, an initial "P" → Pallem), and last by how alike the letters are.
"me", "myself", "my mother", "Ravi's wife" or "father of Sita" are worked out from the tree instead.
"""
import difflib
import json
import re
import unicodedata

from . import features, related

ACCEPT = 0.75          # a person's score from here up can be the answer
TIE = 0.05             # people within this much of the best are all offered
NEAR = 0.5             # "did you mean" suggestions when nobody is accepted
MAX_CHOICES = 6
ME_WORDS = {"me", "myself", "i", "my self", "mine"}
_SOUNDS = (("ksh", "x"), ("ks", "x"), ("sh", "s"), ("th", "t"), ("dh", "d"), ("bh", "b"), ("kh", "k"),
           ("gh", "g"), ("ph", "f"), ("ch", "c"), ("jh", "j"), ("ck", "k"), ("ee", "i"), ("oo", "u"), ("w", "v"),
           ("z", "j"), ("q", "k"))


def _fold(text: str) -> str:
    """Lower case; accents dropped from Latin letters (José → jose); other scripts kept as they are."""
    t = unicodedata.normalize("NFKD", (text or "").lower())
    if all(ord(c) < 0x250 or unicodedata.combining(c) for c in t):
        t = "".join(c for c in t if not unicodedata.combining(c))
    return unicodedata.normalize("NFC", t)


def words(text: str) -> list:
    return [w for w in re.split(r"[^\wऀ-ൿ]+", _fold(text)) if w]


def sound(word: str) -> str:
    """The word with common transliteration differences folded together (Latin letters only)."""
    if not word.isascii():
        return word
    w = word
    for a, b in _SOUNDS:
        w = w.replace(a, b)
    if w.endswith("y"):
        w = w[:-1] + "i"
    w = re.sub(r"(.)\1+", r"\1", w)                    # doubled letters: Lalitha / Lallitha
    w = re.sub(r"(?<=[^aeiou])h", "", w)               # a breathy h after a consonant: Bhaskar / Baskar
    return w


def skeleton(word: str) -> str:
    """The first letter and the consonants after it."""
    return word[:1] + re.sub(r"[aeiou]", "", word[1:])


def word_score(q: str, n: str) -> float:
    if q == n:
        return 1.0
    sq, sn = sound(q), sound(n)
    if sq == sn:
        return 0.95
    if len(q) == 1 and n.startswith(q):                # an initial
        return 0.9
    if len(q) >= 3 and (n.startswith(q) or sn.startswith(sq)):
        return 0.85
    if len(sq) >= 3 and skeleton(sq) == skeleton(sn):     # only the vowels differ: Kiren / Kiran, Sunita / Suneta
        return 0.88
    return 0.9 * difflib.SequenceMatcher(None, sq, sn).ratio()


def _name_words(conn, g) -> dict:
    """{pid: [words of every name]}."""
    script = features.on("script_names")
    extra = {r["id"]: r for r in conn.execute(
        "SELECT id, other_names FROM people WHERE deleted_at IS NULL")}
    out = {}
    for pid, p in g.people.items():
        parts = [p.given, p.surname, p.birth_surname, p.nickname]
        if script:
            parts += [p.given_local, p.surname_local]
        r = extra.get(pid)
        if r is not None and r["other_names"]:
            try:
                parts += [o.get("name") for o in json.loads(r["other_names"]) if isinstance(o, dict)]
            except ValueError:
                pass
        out[pid] = sorted({w for part in parts if part for w in words(part)})
    return out


def score(query: list, names: list) -> tuple[float, bool]:
    """(how well the query's words fit one person's names, 0–1; whether every word was exact)."""
    if not query or not names:
        return 0.0, False
    best = [max(word_score(q, n) for n in names) for q in query]
    s = sum(best) / len(best)
    if min(best) < 0.6:                                 # a word that fits none of the names
        s *= 0.5
    return s, all(b == 1.0 for b in best)


def rank(conn, g, text: str) -> list:
    """[(score, exact, pid)], best first."""
    q = words(text)
    out = []
    for pid, names in _name_words(conn, g).items():
        s, exact = score(q, names)
        if s >= NEAR:
            out.append((s, exact, pid))
    out.sort(key=lambda x: (-x[0], not x[1], g.people[x[2]].name.lower()))
    return out


def _by_relation(conn, g, text: str, me: str | None, lang: str) -> list | None:
    """People a phrase like "my mother", "Ravi's wife" or "father of Sita" names, or None when it isn't one."""
    t = " ".join(_fold(text).replace("’", "'").split())
    if not (t.startswith("my ") or "'s " in t or " of " in t):
        return None
    try:
        p = related.parse(g, t, me, lang)
    except related.ParseError:
        return None
    if p["rel"] == "relatives" and not p["term"]:
        return None
    found = related.search(conn, g, p["anchor"], lang, p["rel"], p["term"], p["side"], gender=p["gender"])
    return [pid for pid, _ in found]


def find(conn, g, text: str, me: str | None, lang: str) -> dict:
    t = " ".join(_fold(text).split())
    if t in ME_WORDS:
        return {"id": me, "how": "me"} if me else {"none": [], "me": True}
    by_rel = _by_relation(conn, g, t, me, lang)
    if by_rel:
        return {"id": by_rel[0], "how": "relation"} if len(by_rel) == 1 else {"choices": by_rel[:MAX_CHOICES]}
    ranked = rank(conn, g, t)
    if not ranked or ranked[0][0] < ACCEPT:
        return {"none": [pid for _, _, pid in ranked[:3]]}
    top, top_exact = ranked[0][0], ranked[0][1]
    if top_exact:
        tied = [pid for s, exact, pid in ranked if exact and s >= top - TIE]
    else:
        tied = [pid for s, _, pid in ranked if s >= max(ACCEPT, top - TIE)]
    if len(tied) == 1:
        return {"id": tied[0], "how": "exact" if top_exact else "close"}
    # several people share a name; one the asker said "This is me" for isn't meant by a name
    tied = [pid for pid in tied if pid != me] or tied
    return {"id": tied[0], "how": "exact" if top_exact else "close"} if len(tied) == 1 else \
        {"choices": tied[:MAX_CHOICES]}
