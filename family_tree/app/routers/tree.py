"""Chart data: an hourglass around a focus person (§11), and relationships."""
from fastapi import APIRouter, Depends, HTTPException, Query

from .. import config, db, features, graph as graph_mod, kin, relations, sides as sides_mod, upcoming
from ..auth import require_user

router = APIRouter(prefix="/api", tags=["tree"])

MAX_NODES = 2000


class _Budget:
    def __init__(self, n):
        self.left = n

    def take(self) -> bool:
        if self.left <= 0:
            return False
        self.left -= 1
        return True


def _card(g, pid, deco):
    """A card; `deco(pid, card)` adds the viewer's relationship and side."""
    s = graph_mod.summary(g.people[pid])
    if deco is not None:
        deco(pid, s)
    return s


def _ancestors(g, pid, depth, budget, me_rel, seen):
    """{p, parents: {familyId, relation, a, b} | None, moreParents: n}"""
    node = {"p": _card(g, pid, me_rel), "parents": None, "moreParents": 0, "truncated": False}
    fams = sorted(g.people[pid].parent_fams, key=lambda x: x[1] != "birth")
    node["moreParents"] = max(len(fams) - 1, 0)
    if not fams:
        return node
    if depth <= 0:
        node["truncated"] = True
        return node
    fid, rel = fams[0]
    fam = g.families[fid]
    sides = []
    for par in (fam.p1, fam.p2):
        if par and par not in seen and budget.take():
            seen.add(par)
            sides.append(_ancestors(g, par, depth - 1, budget, me_rel, seen))
        else:
            sides.append({"p": _card(g, par, me_rel), "parents": None, "moreParents": 0, "truncated": True,
                          "repeat": True} if par else None)
    node["parents"] = {"familyId": fid, "relation": rel, "a": sides[0], "b": sides[1]}
    return node


def _descendants(g, pid, depth, budget, me_rel, seen):
    """{p, families: [{id, kind, ended, partner, marriageYear, children: [node]}], truncated}"""
    node = {"p": _card(g, pid, me_rel), "families": [], "truncated": False}
    for fid in g.people[pid].partner_fams:
        fam = g.families[fid]
        other = fam.other(pid)
        entry = {"id": fid, "kind": fam.kind, "ended": fam.ended,
                 "partner": _card(g, other, me_rel) if other else None,
                 "marriageYear": fam.marriage["date_y"] if fam.marriage else None, "children": [],
                 "childCount": len(fam.children)}
        if depth > 0:
            for cid, rel, _pos in fam.children:
                if cid in seen or not budget.take():
                    entry["truncated"] = True
                    continue
                seen.add(cid)
                child = _descendants(g, cid, depth - 1, budget, me_rel, seen)
                child["relation"] = rel
                entry["children"].append(child)
        elif fam.children:
            node["truncated"] = True
        node["families"].append(entry)
    return node


@router.get("/tree")
def tree(focus: str | None = None, up: int = Query(4, ge=0, le=10), down: int = Query(3, ge=0, le=10),
         siblings: bool = True, sides: bool = False, user: dict = Depends(require_user)):
    """`sides=true` adds each card's side (§13.16) relative to "me", or to the
    focus person when "me" isn't set; `sideAnchor` says which."""
    with db.get_conn() as conn:
        g = graph_mod.get(conn)
    me = user["me_person_id"] if user["me_person_id"] in g.people else None
    if not focus:
        focus = me
    if not focus:
        if not g.people:
            return {"focus": None, "empty": True, "meId": None}
        # the most connected person makes a better default than a random one
        focus = max(g.people, key=lambda p: (len(g.people[p].parent_fams) + len(g.people[p].partner_fams),
                                             g.people[p].name))
    if focus not in g.people:
        raise HTTPException(404, "That person isn't in the tree.")
    budget = _Budget(MAX_NODES)
    label = kin.labeler(g, me)
    side_anchor = (me or focus) if sides and features.on("sides") else None
    side_map = {}
    if side_anchor:
        with db.get_conn() as conn:
            side_map = sides_mod.get(conn, g, side_anchor)

    def deco(pid, card):
        if label:
            card["relationship"] = label(pid)
        if side_anchor:
            card["side"] = side_map.get(pid)

    rel_fn = deco if (label or side_anchor) else None
    seen_up, seen_down = {focus}, {focus}
    anc = _ancestors(g, focus, up, budget, rel_fn, seen_up)
    desc = _descendants(g, focus, down, budget, rel_fn, seen_down)
    sibs = []
    if siblings:
        order = []
        fams = sorted(g.people[focus].parent_fams, key=lambda x: x[1] != "birth")
        if fams:
            order = [c for c, _r, _p in g.families[fams[0][0]].children]
        for sid, is_full in g.siblings(focus):
            if budget.take():
                s = _card(g, sid, rel_fn)
                s["full"] = is_full
                s["older"] = sid in order and focus in order and order.index(sid) < order.index(focus)
                sibs.append(s)
        sibs.sort(key=lambda s: (not s["older"], order.index(s["id"]) if s["id"] in order else 999))
    return {"focus": focus, "meId": me, "ancestors": anc, "descendants": desc, "siblings": sibs,
            "truncated": budget.left <= 0, "people": len(g.people), "sideAnchor": side_anchor}


MAX_ALL = 3000


def generations(g) -> tuple[dict, dict]:
    """Row (generation) and component for every person: parents one row up,
    children one down, partners on the same row. Where the data disagrees
    (e.g. someone married a cousin's child) the first assignment wins. Each
    connected group of relatives is numbered from 0 at its top row."""
    order = sorted(g.people, key=lambda p: (-(len(g.people[p].parent_fams) + len(g.people[p].partner_fams)), p))

    def neighbours(cur):
        return ([(par, -1) for par, _f, _r in g.parents(cur)] + [(c, 1) for c, _f, _r in g.children(cur)]
                + [(x, 0) for x, _f in g.partners(cur) if x])
    return graph_mod.generation_rows(order, neighbours)


@router.get("/tree/all")
def whole_tree(sides: bool = False, user: dict = Depends(require_user)):
    """Everyone at once, for the whole-tree view: people, families and a row
    (generation) per person; the browser does the layout. `sides=true` adds
    each person's side relative to "me" (§13.16)."""
    with db.get_conn() as conn:
        g = graph_mod.get(conn)
        me = user["me_person_id"] if user["me_person_id"] in g.people else None
        side_map = sides_mod.get(conn, g, me) if sides and me and features.on("sides") else None
    if not g.people:
        return {"empty": True, "meId": None, "people": [], "families": []}
    gen, comp = generations(g)
    # the group of relatives that includes "me" first, then the biggest groups
    sizes = {}
    for c in comp.values():
        sizes[c] = sizes.get(c, 0) + 1
    rank = sorted(sizes, key=lambda c: (c != comp.get(me), -sizes[c], c))
    rank_of = {c: i for i, c in enumerate(rank)}
    ids = sorted(g.people, key=lambda p: (rank_of[comp[p]], gen[p], g.people[p].name))
    truncated = len(ids) > MAX_ALL
    ids = ids[:MAX_ALL]
    keep = set(ids)
    people = []
    for pid in ids:
        s = graph_mod.summary(g.people[pid])
        s["gen"] = gen[pid]
        s["comp"] = rank_of[comp[pid]]
        if me and len(ids) <= 800:
            s["relationship"] = kin.label_for(g, me, pid)
        if side_map is not None:
            s["side"] = side_map.get(pid)
        people.append(s)
    families = []
    for f in g.families.values():
        partners = [p for p in f.partners() if p in keep]
        kids = [{"id": c, "relation": r} for c, r, _pos in f.children if c in keep]
        if not partners and not kids:
            continue
        families.append({"id": f.id, "p1": f.p1 if f.p1 in keep else None, "p2": f.p2 if f.p2 in keep else None,
                         "kind": f.kind, "ended": f.ended, "children": kids})
    return {"meId": me, "people": people, "families": families, "truncated": truncated, "total": len(g.people),
            "sideAnchor": me if side_map is not None else None}


@router.get("/relationship")
def relationship(from_: str = Query(alias="from"), to: str = Query(...), user: dict = Depends(require_user)):
    with db.get_conn() as conn:
        g = graph_mod.get(conn)
    if from_ not in g.people or to not in g.people:
        raise HTTPException(404, "That person isn't in the tree.")
    return kin.describe(g, from_, relations.relationship(g, from_, to), kin.current())


@router.get("/upcoming")
def upcoming_view(days: int = Query(60, ge=1, le=366), scope: str = Query("close", pattern="^(close|all)$"),
                  remembrance: bool = False, tithi: bool = True, milestones: bool = True,
                  user: dict = Depends(require_user)):
    """Birthdays, anniversaries and (optionally) remembrance days coming up; with
    Also tithi days (🪔) and milestones (🎉), while their switches are on.
    "close" means within 3 steps of "This is me" (everyone when it isn't set)."""
    with db.get_conn() as conn:
        g = graph_mod.get(conn)
        me = user["me_person_id"] if user["me_person_id"] in g.people else None
        items = upcoming.entries(g, config.today(), days, me, scope, remembrance)
        extra = upcoming.extras(conn, g, config.today(), days, me, scope, tithis=tithi,
                                milestones_on=milestones)
    items = upcoming.merge(items, extra)
    return {"today": config.today().isoformat(), "meSet": bool(me), "scope": scope if me else "all", "items": items}
