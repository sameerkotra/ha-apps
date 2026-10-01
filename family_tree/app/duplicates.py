"""Duplicate finder (§13.4): people who may have been entered twice.

Names are compared after a small normalisation for spellings common in
Indian names (Lakshmi/Laxmi, Venkat/Venkata, Srinivas/Shrinivas, doubled
letters, th/t, v/w, sh/s …) with Jaro-Winkler similarity. Only pairs that
share a surname initial or a phonetic key of the first name are scored, so it
stays fast. Birth and death years, birth place, gender, parents' and
partners' names add or take away points. Pairs above THRESHOLD are listed with
their reasons; "not the same person" dismissals are remembered.
"""
import re
import threading

from . import db

THRESHOLD = 0.72
MAX_BUCKET = 400

_RULES = [("ksh", "ks"), ("x", "ks"), ("aa", "a"), ("ee", "i"), ("ii", "i"), ("oo", "u"), ("uu", "u"), ("ou", "u"),
          ("th", "t"), ("dh", "d"), ("bh", "b"), ("gh", "g"), ("kh", "k"), ("ch", "c"), ("jh", "j"), ("ph", "f"),
          ("sh", "s"), ("w", "v"), ("y", "i"), ("z", "j"), ("q", "k")]


def norm(text: str | None) -> str:
    """"Shrinivasa" → "srinivas", "Laxmi" → "laksmi", "Lakshmi" → "laksmi"."""
    t = re.sub(r"[^a-z ]", "", (text or "").lower())
    words = []
    for w in t.split():
        for a, b in _RULES:
            w = w.replace(a, b)
        w = re.sub(r"(.)\1+", r"\1", w)              # doubled letters
        if len(w) > 4 and w.endswith("a"):          # Venkata → Venkat, Srinivasa → Srinivas
            w = w[:-1]
        words.append(w)
    return " ".join(words)


def jaro_winkler(a: str, b: str) -> float:
    if a == b:
        return 1.0 if a else 0.0
    la, lb = len(a), len(b)
    if not la or not lb:
        return 0.0
    rng = max(max(la, lb) // 2 - 1, 0)
    am, bm = [False] * la, [False] * lb
    m = 0
    for i, ca in enumerate(a):
        for j in range(max(0, i - rng), min(lb, i + rng + 1)):
            if not bm[j] and b[j] == ca:
                am[i] = bm[j] = True
                m += 1
                break
    if not m:
        return 0.0
    t, k = 0, 0
    for i in range(la):
        if am[i]:
            while not bm[k]:
                k += 1
            if a[i] != b[k]:
                t += 1
            k += 1
    jaro = (m / la + m / lb + (m - t / 2) / m) / 3
    p = 0
    for x, y in zip(a[:4], b[:4]):
        if x != y:
            break
        p += 1
    return jaro + p * 0.1 * (1 - jaro)


def _name_sim(x: str | None, y: str | None) -> float:
    nx, ny = norm(x), norm(y)
    if not nx or not ny:
        return 0.0
    best = jaro_winkler(nx, ny)
    # "Venkata Rao" vs "Venkat": compare first words too
    best = max(best, jaro_winkler(nx.split()[0], ny.split()[0]) - 0.03)
    return best


def _year(ev):
    return (ev or {}).get("date_y")


def _place(ev):
    p = (ev or {}).get("place")
    return norm(p.split(",")[0]) if p else None


def _parent_names(g, pid):
    return {norm(g.people[x].given) for x, _f, _r in g.parents(pid) if g.people[x].given}


def _partner_names(g, pid):
    return {norm(g.people[x].given) for x, _f in g.partners(pid) if x and g.people[x].given}


def _related_directly(g, a, b) -> bool:
    if any(x == b for x, _f in g.partners(a)):
        return True
    if g.is_descendant(a, b, limit=50) or g.is_descendant(b, a, limit=50):
        return True
    return any(s == b for s, _full in g.siblings(a))


def score(g, a: str, b: str):
    """(score 0–1, [reasons]) or None when they can't be the same person."""
    pa, pb = g.people[a], g.people[b]
    if pa.gender in ("male", "female") and pb.gender in ("male", "female") and pa.gender != pb.gender:
        return None
    if _related_directly(g, a, b):
        return None
    reasons = []
    given = _name_sim(pa.given or pa.nickname, pb.given or pb.nickname)
    if given < 0.8:
        return None
    sa = {norm(x) for x in (pa.surname, pa.birth_surname) if x}
    sb = {norm(x) for x in (pb.surname, pb.birth_surname) if x}
    if sa and sb:
        sur = max(jaro_winkler(x, y) for x in sa for y in sb)
    else:
        sur = 0.85                                         # a missing surname neither helps nor rules out
    if sur < 0.8:
        return None
    s = 0.45 * given + 0.25 * sur
    reasons.append("same name" if pa.name.lower() == pb.name.lower() else
                   f"similar names ({' / '.join(sorted((pa.name, pb.name)))})")
    ya, yb = _year(pa.birth), _year(pb.birth)
    if ya and yb:
        d = abs(ya - yb)
        if d > 5:
            return None
        if d <= 2:
            s += 0.15 if d == 0 else 0.1
            reasons.append(f"born {ya}" if d == 0 else f"birth year {min(ya, yb)} vs {max(ya, yb)}")
        else:
            s -= 0.1
    pla, plb = _place(pa.birth), _place(pb.birth)
    if pla and plb:
        if jaro_winkler(pla, plb) > 0.9:
            s += 0.07
            reasons.append(f"born in {pa.birth['place'].split(',')[0]}")
        else:
            s -= 0.05
    da, dbb = _year(pa.death), _year(pb.death)
    if da and dbb:
        if abs(da - dbb) <= 1:
            s += 0.08
            reasons.append(f"died {da}")
        else:
            return None
    par_a, par_b = _parent_names(g, a), _parent_names(g, b)
    if par_a and par_b:
        common = {x for x in par_a if any(jaro_winkler(x, y) > 0.9 for y in par_b)}
        if common:
            s += 0.12 if len(common) >= 2 else 0.07
            reasons.append("same parents" if len(common) >= 2 else "a parent with the same name")
        else:
            s -= 0.1
    pt_a, pt_b = _partner_names(g, a), _partner_names(g, b)
    if pt_a and pt_b and any(jaro_winkler(x, y) > 0.9 for x in pt_a for y in pt_b):
        s += 0.08
        reasons.append("partner with the same name")
    return min(s, 1.0), reasons


def _buckets(g):
    buckets = {}
    for pid, p in g.people.items():
        keys = set()
        for sn in (p.surname, p.birth_surname):
            n = norm(sn)
            if n:
                keys.add("s:" + n[0])
        n = norm(p.given or p.nickname)
        if n:
            keys.add("g:" + n.split()[0][:4])
        for k in keys:
            buckets.setdefault(k, []).append(pid)
    return buckets


_lock = threading.Lock()
_cache: dict = {}


def find(conn, g) -> list:
    """[{a, b, score, reasons}] best first; cached per tree version and dismissals."""
    version = db.get_setting(conn, "tree_version", "0")
    dismissed = {(r["a"], r["b"]) for r in conn.execute("SELECT a, b FROM not_duplicates")}
    key = (version, len(dismissed))
    with _lock:
        if _cache.get("key") == key:
            return _cache["pairs"]
    seen, pairs = set(), []
    for ids in _buckets(g).values():
        if len(ids) > MAX_BUCKET:
            ids = ids[:MAX_BUCKET]
        for i in range(len(ids)):
            for j in range(i + 1, len(ids)):
                a, b = sorted((ids[i], ids[j]))
                if (a, b) in seen or (a, b) in dismissed:
                    continue
                seen.add((a, b))
                r = score(g, a, b)
                if r and r[0] >= THRESHOLD:
                    pairs.append({"a": a, "b": b, "score": round(r[0], 3), "reasons": r[1]})
    pairs.sort(key=lambda x: -x["score"])
    with _lock:
        _cache.update(key=key, pairs=pairs)
    return pairs
