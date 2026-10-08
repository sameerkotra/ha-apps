"""Relationship names (§6): "how is B related to A?".

1. Blood: lowest common ancestor by BFS (12 generations), then the name from
   the generation counts (g1 = A→ancestor, g2 = B→ancestor).
2. Otherwise one partner hop on either side gives the in-law forms.
3. Otherwise the shortest chain of parent/child/partner links, named step by
   step ("husband's brother's wife's father", kind "marriage") when it has at
   most eight steps — so it gets a Telugu or Hindi term too — or just "related
   by marriage" when it's longer; else "not related".

Labels are relative phrases without "your": "first cousin once removed",
"brother-in-law", "first cousin's wife". Gendered when the gender is known.
"""
from collections import deque

from .graph import Graph

ORD = ["", "first", "second", "third", "fourth", "fifth", "sixth", "seventh", "eighth", "ninth", "tenth"]
TIMES = {1: "once", 2: "twice", 3: "three times", 4: "four times", 5: "five times"}


NEAR = 4                      # parent/child links: first cousins, grand-uncles, grand-nephews


def _g(gender, male, female, neutral):
    return male if gender == "male" else female if gender == "female" else neutral


def _greats(n: int) -> str:
    """0 → '', 1 → 'great-', 2 → 'great-great-', 3+ → '3× great-'."""
    if n <= 0:
        return ""
    if n <= 2:
        return "great-" * n
    return f"{n}× great-"


def _ordinal(n: int) -> str:
    return ORD[n] if n < len(ORD) else f"{n}th"


def ancestor_word(n: int, gender: str) -> str:
    """B is A's ancestor n generations up."""
    if n == 1:
        return _g(gender, "father", "mother", "parent")
    base = _g(gender, "grandfather", "grandmother", "grandparent")
    return _greats(n - 2) + base


def descendant_word(n: int, gender: str) -> str:
    if n == 1:
        return _g(gender, "son", "daughter", "child")
    base = _g(gender, "grandson", "granddaughter", "grandchild")
    return _greats(n - 2) + base


def blood_label(g1: int, g2: int, gender: str, half: bool) -> str:
    """Name of B for A, where A is g1 and B is g2 generations below the common ancestor."""
    h = "half-" if half else ""
    if g1 == 0 and g2 == 0:
        return "you"
    if g1 == 0:
        return descendant_word(g2, gender)
    if g2 == 0:
        return ancestor_word(g1, gender)
    if g1 == 1 and g2 == 1:
        return h + _g(gender, "brother", "sister", "sibling")
    if g2 == 1:                                     # B is a sibling of A's ancestor
        n = g1 - 1
        if gender in ("male", "female"):
            word = _g(gender, "uncle", "aunt", "")
            if n == 1:
                return h + word
            return h + ("great-" if n == 2 else _greats(n - 1)) + word
        if n == 1:
            return h + "parent's sibling"
        return h + ancestor_word(n, "unknown") + "'s sibling"
    if g1 == 1:                                     # B descends from A's sibling
        n = g2 - 1
        if gender in ("male", "female"):
            word = _g(gender, "nephew", "niece", "")
            if n == 1:
                return h + word
            if n == 2:
                return h + "grand" + word
            return h + _greats(n - 2) + "grand" + word
        if n == 1:
            return h + "sibling's child"
        return h + "sibling's " + descendant_word(n, "unknown")
    degree = min(g1, g2) - 1
    removed = abs(g1 - g2)
    label = ("half " if half else "") + f"{_ordinal(degree)} cousin"
    if removed:
        label += f" {TIMES.get(removed, f'{removed} times')} removed"
    return label


def _spouse_word(g: Graph, pid: str, fid: str) -> str:
    p = g.people[pid]
    fam = g.families[fid]
    if fam.kind == "partners":
        word = "partner"
    else:
        word = _g(p.gender, "husband", "wife", "spouse")
    if fam.ended in ("divorced", "separated"):
        word = "ex-" + word if word != "partner" else "former partner"
    return word


def _family_between(g: Graph, parent: str, child: str):
    for fid, rel in sorted(g.people[child].parent_fams, key=lambda x: x[1] != "birth"):
        if parent in g.families[fid].partners():
            return fid, rel
    return None, None


def blood_steps(pa: list, pb: list) -> list:
    """The canonical walk from A to B for the kin key (§13.1): up to the
    generation below the common ancestor, across as siblings, then down —
    so a father's brother is F.B, never F.F.S. Each step is
    {"t": parent|sibling|child, "id": person reached, "from": previous person}."""
    g1, g2 = len(pa) - 1, len(pb) - 1
    steps = []
    if g2 == 0:                                   # B is A's ancestor (or A)
        for i in range(1, g1 + 1):
            steps.append({"t": "parent", "id": pa[i], "from": pa[i - 1]})
        return steps
    if g1 == 0:                                   # B is A's descendant
        chain = list(reversed(pb))                # A … B
        for i in range(1, len(chain)):
            steps.append({"t": "child", "id": chain[i], "from": chain[i - 1]})
        return steps
    for i in range(1, g1):
        steps.append({"t": "parent", "id": pa[i], "from": pa[i - 1]})
    steps.append({"t": "sibling", "id": pb[g2 - 1], "from": pa[g1 - 1]})
    for i in range(g2 - 1, 0, -1):
        steps.append({"t": "child", "id": pb[i - 1], "from": pb[i]})
    return steps


def _blood(g: Graph, a: str, b: str):
    """(label, path, g1, g2, steps) for blood relatives, else None."""
    up_a = g.ancestors(a)
    up_b = g.ancestors(b)
    common = set(up_a) & set(up_b)
    if not common:
        return None
    ca = min(common, key=lambda x: (len(up_a[x]) + len(up_b[x]), max(len(up_a[x]), len(up_b[x])), x))
    pa, pb = up_a[ca], up_b[ca]
    g1, g2 = len(pa) - 1, len(pb) - 1
    half = False
    if g1 >= 1 and g2 >= 1:
        fa, ra = _family_between(g, ca, pa[-2])
        fb, rb = _family_between(g, ca, pb[-2])
        if fa == fb and ra != rb and "step" in (ra, rb):
            half = True                  # one of them is only a stepchild of that couple
        elif fa != fb:
            fam_a, fam_b = g.families.get(fa), g.families.get(fb)
            same_couple = fam_a and fam_b and set(fam_a.partners()) == set(fam_b.partners())
            half = not same_couple
    person_b = g.people[b]
    label = blood_label(g1, g2, person_b.gender, half)
    # direct parent/child through adoption, step or fostering says so
    if g1 == 1 and g2 == 0:
        _f, rel = _family_between(g, b, a)
        label = {"adopted": "adoptive ", "step": "step", "foster": "foster "}.get(rel, "") + label
    elif g1 == 0 and g2 == 1:
        _f, rel = _family_between(g, a, b)
        label = {"adopted": "adopted ", "step": "step", "foster": "foster "}.get(rel, "") + label
    # the chain: a → … → ca → … → b, each step named relative to the previous person
    path = [{"id": a, "rel": None}]
    for i in range(1, len(pa)):
        path.append({"id": pa[i], "rel": ancestor_word(1, g.people[pa[i]].gender)})
    for pid in reversed(pb[:-1]):
        path.append({"id": pid, "rel": descendant_word(1, g.people[pid].gender)})
    return label, path, g1, g2, blood_steps(pa, pb)


def _with_names(g: Graph, path: list) -> list:
    return [dict(step, name=g.people[step["id"]].name) for step in path]


def relationship(g: Graph, a: str, b: str) -> dict:
    """{label, kind: self|blood|inlaw|marriage|none, path:[{id, name, rel}],
    steps, gen}. `steps` is the canonical walk for the kin key (kin.py); None
    when there isn't one (related only by a longer chain of marriages). `gen`
    is B's generation relative to A (+1 = a child's generation), when known."""
    if a not in g.people or b not in g.people:
        return {"label": None, "kind": "none", "path": [], "steps": None, "gen": None}
    if a == b:
        return {"label": "you", "kind": "self", "path": _with_names(g, [{"id": a, "rel": None}]), "steps": [], "gen": 0}
    blood = _blood(g, a, b)
    if blood:
        label, path, g1, g2, steps = blood
        return {"label": label, "kind": "blood", "path": _with_names(g, path), "steps": steps, "gen": g2 - g1}

    # B is A's partner
    for partner, fid in g.partners(a):
        if partner == b:
            return {"label": _spouse_word(g, b, fid), "kind": "inlaw",
                    "path": _with_names(g, [{"id": a, "rel": None}, {"id": b, "rel": _spouse_word(g, b, fid)}]),
                    "steps": [{"t": "spouse", "id": b, "from": a, "fam": fid}], "gen": 0}
    pb = g.people[b]
    # B is the partner of A's blood relative X
    best = None
    for x, fid in g.partners(b):
        if not x:
            continue
        rel = _blood(g, a, x)
        if rel:
            label, path, g1, g2, bsteps = rel
            if (g1, g2) == (1, 1):
                word = _g(pb.gender, "brother-in-law", "sister-in-law", "sibling-in-law")
            elif (g1, g2) == (0, 1):
                word = _g(pb.gender, "son-in-law", "daughter-in-law", "child-in-law")
            elif (g1, g2) == (1, 0):
                word = _g(pb.gender, "stepfather", "stepmother", "step-parent")
            elif (g1, g2) == (2, 0):
                word = _g(pb.gender, "step-grandfather", "step-grandmother", "step-grandparent")
            elif (g1, g2) == (2, 1) and pb.gender in ("male", "female"):
                word = _g(pb.gender, "uncle by marriage", "aunt by marriage", "")
            else:
                word = f"{label}'s {_spouse_word(g, b, fid)}"
            cand = (g1 + g2, word, path + [{"id": b, "rel": _spouse_word(g, b, fid)}],
                    bsteps + [{"t": "spouse", "id": b, "from": x, "fam": fid}], g2 - g1)
            if best is None or cand[0] < best[0]:
                best = cand
    # B is a blood relative of A's partner Y
    for y, fid in g.partners(a):
        if not y:
            continue
        rel = _blood(g, y, b)
        if rel:
            label, path, g1, g2, bsteps = rel
            spouse = _spouse_word(g, y, fid)
            if (g1, g2) == (1, 0):
                word = _g(pb.gender, "father-in-law", "mother-in-law", "parent-in-law")
            elif (g1, g2) == (1, 1):
                word = _g(pb.gender, "brother-in-law", "sister-in-law", "sibling-in-law")
            elif (g1, g2) == (0, 1):
                word = _g(pb.gender, "stepson", "stepdaughter", "stepchild")
            else:
                word = f"{spouse}'s {label}"
            cand = (g1 + g2, word, [{"id": a, "rel": None}, {"id": y, "rel": spouse}] + path[1:],
                    [{"t": "spouse", "id": y, "from": a, "fam": fid}] + bsteps, g2 - g1)
            if best is None or cand[0] < best[0]:
                best = cand
    if best:
        return {"label": best[1], "kind": "inlaw", "path": _with_names(g, best[2]), "steps": best[3], "gen": best[4]}
    best = _two_marriages(g, a, b)
    if best:
        return {"label": best[1], "kind": "inlaw", "path": _with_names(g, best[2]), "steps": best[3], "gen": best[4]}

    path = _any_path(g, a, b)
    if path:
        steps = _chain_steps(g, [x["id"] for x in path])
        if steps:                       # three or more marriages, still near enough to name step by step
            gen = sum({"parent": -1, "child": 1}.get(st["t"], 0) for st in steps)
            return {"label": "'s ".join(_step_word(g, st) for st in steps), "kind": "marriage",
                    "path": _with_names(g, path), "steps": steps, "gen": gen}
        return {"label": "related by marriage", "kind": "marriage", "path": _with_names(g, path), "steps": None, "gen": None}
    return {"label": "not related", "kind": "none", "path": [], "steps": None, "gen": None}


def _near(g: Graph, pid: str, radius: int = NEAR) -> set:
    """People within `radius` parent/child links of pid (pid included) — the
    only ones close enough to be worth naming through a second marriage."""
    seen = {pid}
    frontier = [pid]
    for _ in range(radius):
        nxt = []
        for cur in frontier:
            for n in [p for p, _f, _r in g.parents(cur)] + [c for c, _f, _r in g.children(cur)]:
                if n not in seen:
                    seen.add(n)
                    nxt.append(n)
        frontier = nxt
    return seen


def _two_marriages(g: Graph, a: str, b: str):
    """Close relatives across two marriages, so they get a kin term instead of
    "related by marriage": B is the partner of a blood relative of A's partner
    (wife's sister's husband), or a blood relative of the partner of A's blood
    relative (brother's wife's brother). (distance, label, path, steps, gen) or None."""
    best = None
    # A —partner→ Y —blood→ X —partner→ B
    for y, fy in g.partners(a):
        if not y:
            continue
        near_y = _near(g, y)
        for x, fx in g.partners(b):
            if not x or x == a or x not in near_y:
                continue
            rel = _blood(g, y, x)
            if not rel:
                continue
            label, path, g1, g2, bsteps = rel
            if g1 + g2 > NEAR:
                continue
            spouse_y, spouse_b = _spouse_word(g, y, fy), _spouse_word(g, b, fx)
            cand = (g1 + g2 + 2, f"{spouse_y}'s {label}'s {spouse_b}",
                    [{"id": a, "rel": None}, {"id": y, "rel": spouse_y}] + path[1:] + [{"id": b, "rel": spouse_b}],
                    [{"t": "spouse", "id": y, "from": a, "fam": fy}] + bsteps
                    + [{"t": "spouse", "id": b, "from": x, "fam": fx}], g2 - g1)
            if best is None or cand[0] < best[0]:
                best = cand
    # A —blood→ X —partner→ W —blood→ B
    near_a, near_b = _near(g, a), _near(g, b)
    for x in sorted(near_a - {a}):
        for w, fw in g.partners(x):
            if not w or w == a or w not in near_b:
                continue
            r1, r2 = _blood(g, a, x), _blood(g, w, b)
            if not r1 or not r2:
                continue
            l1, p1, a1, a2, s1 = r1
            l2, p2, b1, b2, s2 = r2
            if a1 + a2 > NEAR or b1 + b2 > NEAR:
                continue
            spouse = _spouse_word(g, w, fw)
            cand = (a1 + a2 + b1 + b2 + 1, f"{l1}'s {spouse}'s {l2}",
                    p1 + [{"id": w, "rel": spouse}] + p2[1:],
                    s1 + [{"t": "spouse", "id": w, "from": x, "fam": fw}] + s2, (a2 - a1) + (b2 - b1))
            if best is None or cand[0] < best[0]:
                best = cand
    return best


MAX_CHAIN = 8                 # steps a kin key can hold (kin.KEY_RE)


def _chain_steps(g: Graph, ids: list) -> list | None:
    """The canonical walk along a chain of people (parent / child / partner links): a parent then one of their other
    children becomes a sibling step. None when it has more than MAX_CHAIN steps."""
    steps, i = [], 0
    while i < len(ids) - 1:
        u, v = ids[i], ids[i + 1]
        parents = {p: f for p, f, _r in g.parents(u)}
        if v in parents:
            if i + 2 < len(ids) and ids[i + 2] != u and any(c == ids[i + 2] for c, _f, _r in g.children(v)):
                steps.append({"t": "sibling", "id": ids[i + 2], "from": u})
                i += 2
                continue
            steps.append({"t": "parent", "id": v, "from": u})
        elif any(c == v for c, _f, _r in g.children(u)):
            steps.append({"t": "child", "id": v, "from": u})
        else:
            fam = next((f for p, f in g.partners(u) if p == v), None)
            if fam is None:
                return None
            steps.append({"t": "spouse", "id": v, "from": u, "fam": fam})
        i += 1
    return steps if 0 < len(steps) <= MAX_CHAIN else None


def _step_word(g: Graph, st: dict) -> str:
    gender = g.people[st["id"]].gender
    if st["t"] == "spouse":
        return _spouse_word(g, st["id"], st["fam"])
    return {"parent": _g(gender, "father", "mother", "parent"), "child": _g(gender, "son", "daughter", "child"),
            "sibling": _g(gender, "brother", "sister", "sibling")}[st["t"]]


def _any_path(g: Graph, a: str, b: str, limit: int = 40) -> list | None:
    """Shortest chain of parent/child/partner links, each step named."""
    prev = {a: None}
    q = deque([(a, 0)])
    while q:
        cur, d = q.popleft()
        if cur == b:
            break
        if d >= limit:
            continue
        nbrs = [(p, ancestor_word(1, g.people[p].gender)) for p, _f, _r in g.parents(cur)]
        nbrs += [(c, descendant_word(1, g.people[c].gender)) for c, _f, _r in g.children(cur)]
        nbrs += [(p, _spouse_word(g, p, f)) for p, f in g.partners(cur) if p]
        for n, rel in nbrs:
            if n not in prev:
                prev[n] = (cur, rel)
                q.append((n, d + 1))
    if b not in prev:
        return None
    out = []
    cur = b
    while cur != a:
        p, rel = prev[cur]
        out.append({"id": cur, "rel": rel})
        cur = p
    out.append({"id": a, "rel": None})
    return list(reversed(out))
