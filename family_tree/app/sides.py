"""Father's side / mother's side (§13.16), relative to one person (the
viewer's "me", or the focus person):

- paternal: reached through the father; maternal: through the mother;
- both: reached both ways (after a cousin — menarikam — marriage);
- None: the person's own line (themselves, their descendants, full siblings
  and theirs), and anyone not connected by blood or marriage.

Ancestors take the side of the parent they're reached through. Other blood
relatives take the side of their closest common ancestors with the anchor;
partners take the side of the person they married.
"""
import threading

from . import db

_lock = threading.Lock()
_cache: dict = {}


def _parents(g, pid):
    """(father, mother) from the birth family first; unknown genders fill the gaps."""
    fams = sorted(g.people[pid].parent_fams, key=lambda x: x[1] != "birth")
    if not fams:
        return None, None
    partners = g.families[fams[0][0]].partners()
    father = next((p for p in partners if g.people[p].gender == "male"), None)
    mother = next((p for p in partners if g.people[p].gender == "female"), None)
    for p in partners:                                   # a parent of unknown gender takes the free slot
        if p not in (father, mother):
            if father is None:
                father = p
            elif mother is None:
                mother = p
    return father, mother


def compute(g, anchor: str) -> dict:
    """{person_id: "paternal" | "maternal" | "both"}; people without a side are left out."""
    if anchor not in g.people:
        return {}
    father, mother = _parents(g, anchor)
    pat = g.ancestors(father) if father else {}
    mat = g.ancestors(mother) if mother else {}
    mine = g.ancestors(anchor)                          # distances from the anchor
    out = {}

    def side_of(x):
        a, b = x in pat, x in mat
        return "both" if a and b else "paternal" if a else "maternal" if b else None

    for x in set(pat) | set(mat):
        out[x] = side_of(x)
    for pid in g.people:
        if pid in out or pid == anchor:
            continue
        up = g.ancestors(pid)
        if anchor in up:                                # a descendant of the anchor: own line
            continue
        common = [a for a in up if a in pat or a in mat]
        if not common:
            continue
        best = min(len(up[a]) + len(mine.get(a, [])) for a in common)
        nearest = {a for a in common if len(up[a]) + len(mine.get(a, [])) == best}
        if father and mother and {father, mother} <= nearest:
            continue                                    # a full sibling (or their descendant): own line
        sides = {side_of(a) for a in nearest}
        out[pid] = "both" if "both" in sides or {"paternal", "maternal"} <= sides else sides.pop()
    # partners take the side of the person they married
    for pid, side in list(out.items()):
        for partner, _fid in g.partners(pid):
            if partner and partner != anchor and partner not in out and anchor not in g.ancestors(partner):
                out[partner] = side
    return out


def get(conn, g, anchor: str | None) -> dict:
    """Cached per tree version and anchor."""
    if not anchor:
        return {}
    version = db.get_setting(conn, "tree_version", "0")
    key = (version, anchor)
    with _lock:
        if key in _cache:
            return _cache[key]
    result = compute(g, anchor)
    with _lock:
        if len(_cache) > 50:
            _cache.clear()
        _cache[key] = result
    return result
