"""Relationship search (§13.17). Registered before routers/people.py,
so /people/related isn't taken for a person id."""
from fastapi import APIRouter, Depends, HTTPException, Query

from .. import db, features, graph as graph_mod, kin, related, sides
from ..auth import require_user

router = APIRouter(prefix="/api", tags=["related"])


@router.get("/people/related/parse")
def parse(q: str = Query(..., max_length=200), user: dict = Depends(require_user)):
    """A typed phrase ("all my Babais", "cousins of Ravi") → picker values."""
    with db.get_conn() as conn:
        g = graph_mod.get(conn)
        me = user["me_person_id"] if user["me_person_id"] in g.people else None
        try:
            return related.parse(g, q, me, kin.current())
        except related.ParseError as e:
            raise HTTPException(422, str(e))


@router.get("/people/related")
def search(anchor: str | None = None, rel: str = Query("relatives"), term: str | None = Query(None, max_length=60),
           side: str | None = Query(None, pattern="^(paternal|maternal)$"),
           living: str | None = Query(None, pattern="^(living|deceased)$"),
           generation: int | None = Query(None, ge=-10, le=10), gender: str | None = Query(None, pattern="^(male|female)$"),
           user: dict = Depends(require_user)):
    """Everyone who is <rel> of <anchor> (default: you), with labels relative to
    the anchor. The ids can go straight into an export's "Pick people"."""
    if rel not in related.RELS:
        raise HTTPException(422, f"rel must be one of: {', '.join(related.RELS)}.")
    if side:
        features.check("sides", user)
    lang = kin.current()
    with db.get_conn() as conn:
        g = graph_mod.get(conn)
        anchor = anchor or user["me_person_id"]
        if not anchor or anchor not in g.people:
            raise HTTPException(422 if not anchor else 404,
                                "Choose whose relatives to find (or set “This is me”)." if not anchor
                                else "That person isn't in the tree.")
        found = related.search(conn, g, anchor, lang, rel, term, side, living, generation, gender)
        side_map = sides.get(conn, g, anchor)
    items = []
    for pid, it in found:
        s = graph_mod.summary(g.people[pid])
        s.update(relationship=it["label"], english=it["english"], generation=it["gen"], side=side_map.get(pid) if features.on("sides") else None)
        items.append(s)
    return {"anchor": {"id": anchor, "name": g.people[anchor].name}, "rel": rel, "items": items, "total": len(items)}
