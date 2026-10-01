"""Duplicates & merge (§13.4). The whole merge is one history batch, so
it can be undone in one go."""
import json

from fastapi import APIRouter, Depends, HTTPException
from pydantic import Field

from .. import features, config, db, duplicates, graph as graph_mod
from ..auth import require_user
from ..common import Strict, reject_new_loops, require_person
from ..history import Batch
from .people import _nm, person_detail

router = APIRouter(prefix="/api", tags=["duplicates"], dependencies=[Depends(features.required("duplicates"))])

NAME_FIELDS = ("given_names", "surname", "birth_surname", "nickname", "given_local", "surname_local")
PLAIN_FIELDS = ("gender", "biography", "name_order")
LIFE = ("birth", "death")


@router.get("/duplicates")
def list_duplicates(user: dict = Depends(require_user)):
    with db.get_conn() as conn:
        g = graph_mod.get(conn)
        pairs = duplicates.find(conn, g)
    out = []
    for p in pairs[:200]:
        out.append(dict(p, people=[graph_mod.summary(g.people[x]) for x in (p["a"], p["b"])]))
    return {"items": out, "total": len(pairs)}


class NotDup(Strict):
    a: str
    b: str


@router.post("/duplicates/dismiss")
def dismiss(body: NotDup, user: dict = Depends(require_user)):
    """"Not the same person" — remembered for everyone."""
    a, b = sorted((body.a, body.b))
    if a == b:
        raise HTTPException(422, "That's the same person.")
    with db.get_conn() as conn:
        require_person(conn, a)
        require_person(conn, b)
        conn.execute("INSERT OR IGNORE INTO not_duplicates (a, b, by_user, created_at) VALUES (?, ?, ?, ?)",
                     (a, b, user["id"], config.now_iso()))
    return {"ok": True}


def _life(conn, pid, etype):
    r = conn.execute("SELECT * FROM events WHERE person_id = ? AND type = ?", (pid, etype)).fetchone()
    return dict(r) if r else None


@router.get("/duplicates/compare")
def compare(a: str, b: str, user: dict = Depends(require_user)):
    """Both people side by side, for the merge screen."""
    with db.get_conn() as conn:
        return {"a": person_detail(conn, a, user["me_person_id"]), "b": person_detail(conn, b, user["me_person_id"])}


class MergeIn(Strict):
    otherId: str
    # per fact: "keep" (this person's), "other" (the other's), or "both" (names: the other's becomes an
    # alternate name; birth/death: the other's is kept as an extra event)
    choose: dict[str, str] = Field(default_factory=dict)


@router.post("/people/{keep}/merge")
def merge(keep: str, body: MergeIn, user: dict = Depends(require_user)):
    other = body.otherId
    if keep == other:
        raise HTTPException(422, "Choose two different people.")
    for k, v in body.choose.items():
        if v not in ("keep", "other", "both") or k not in NAME_FIELDS + PLAIN_FIELDS + LIFE:
            raise HTTPException(422, f"Can't choose {v!r} for {k!r}.")
    with db.get_conn() as conn:
        kr, orow = require_person(conn, keep), require_person(conn, other)
        g = graph_mod.load_fresh(conn)
        if g.is_descendant(keep, other, limit=60) or g.is_descendant(other, keep, limit=60):
            raise HTTPException(422, "One of them is the other's ancestor — they can't be the same person.")
        if conn.execute("SELECT 1 FROM families WHERE deleted_at IS NULL AND ((partner1_id = ? AND partner2_id = ?) OR "
                        "(partner1_id = ? AND partner2_id = ?))", (keep, other, other, keep)).fetchone():
            raise HTTPException(422, "They're recorded as partners of each other, so they can't be the same person.")
        ku = conn.execute("SELECT id, name FROM users WHERE me_person_id = ?", (keep,)).fetchone()
        ou = conn.execute("SELECT id, name FROM users WHERE me_person_id = ?", (other,)).fetchone()
        if ku and ou:
            raise HTTPException(409, f"{ku['name']} and {ou['name']} have each said one of them is them. "
                                     "Unlink one of them first (Admin → Users).")
        b = Batch(conn, user["id"], f"Merged {_nm(orow)} into {_nm(kr)}")
        fields, extra_names = {}, json.loads(kr["other_names"] or "[]")
        for f in NAME_FIELDS + PLAIN_FIELDS:
            c = body.choose.get(f, "keep" if kr[f] not in (None, "", "unknown") else "other")
            if c == "other" and orow[f] not in (None, ""):
                if f in NAME_FIELDS and kr[f] and f in ("given_names", "surname", "nickname"):
                    extra_names.append({"type": "aka", "name": kr[f]})
                fields[f] = orow[f]
            elif c == "both" and f in NAME_FIELDS and orow[f] and orow[f] != kr[f]:
                if kr[f]:
                    extra_names.append({"type": "aka", "name": orow[f]})
                else:
                    fields[f] = orow[f]
        for n in json.loads(orow["other_names"] or "[]"):
            extra_names.append(n)
        seen, uniq = set(), []
        for n in extra_names:
            key = (n.get("type"), (n.get("name") or "").lower())
            if n.get("name") and key not in seen and n["name"] not in (fields.get("given_names", kr["given_names"]),
                                                                       fields.get("surname", kr["surname"])):
                seen.add(key)
                uniq.append(n)
        fields["other_names"] = json.dumps(uniq[:10]) if uniq else None
        if orow["deceased"] and not kr["deceased"]:
            fields["deceased"] = 1
        if orow["remind"]:
            fields["remind"] = 1
        if not kr["photo_media_id"] and orow["photo_media_id"]:
            fields.update(photo_media_id=orow["photo_media_id"], photo_region_id=orow["photo_region_id"])
        if orow["never_export"]:
            fields["never_export"] = 1
        b.update("people", keep, fields)

        # birth and death
        for etype in LIFE:
            ke, oe = _life(conn, keep, etype), _life(conn, other, etype)
            if not oe:
                continue
            c = body.choose.get(etype, "keep" if ke else "other")
            if not ke or c == "other":
                if ke:
                    b.update("events", ke["id"], {"type": "custom", "title": f"{etype.title()} (other record)"})
                b.update("events", oe["id"], {"person_id": keep})
            else:
                b.update("events", oe["id"], {"person_id": keep, "type": "custom",
                                               "title": f"{etype.title()} (other record)"})
        # every other event, story, contact, citation, custom value, photo tag and box
        for r in conn.execute("SELECT id FROM events WHERE person_id = ?", (other,)).fetchall():
            b.update("events", r["id"], {"person_id": keep})
        for table, col in (("stories", "person_id"), ("contacts", "person_id"), ("citations", "person_id"),
                           ("media_regions", "person_id"), ("sources", "told_by_person_id")):
            for r in conn.execute(f"SELECT id FROM {table} WHERE {col} = ?", (other,)).fetchall():
                b.update(table, r["id"], {col: keep})
        have = {r["field_id"] for r in conn.execute("SELECT field_id FROM custom_values WHERE person_id = ?", (keep,))}
        for r in conn.execute("SELECT * FROM custom_values WHERE person_id = ?", (other,)).fetchall():
            if r["field_id"] in have:
                b.hard_delete("custom_values", r["id"])
            else:
                b.update("custom_values", r["id"], {"person_id": keep})
        linked = {r["media_id"] for r in conn.execute("SELECT media_id FROM media_links WHERE person_id = ?", (keep,))}
        for r in conn.execute("SELECT * FROM media_links WHERE person_id = ?", (other,)).fetchall():
            if r["media_id"] in linked:
                b.hard_delete("media_links", r["id"], op="unlink")
            else:
                b.update("media_links", r["id"], {"person_id": keep})
        # families: partner slots and child links
        for f in conn.execute("SELECT * FROM families WHERE partner1_id = ? OR partner2_id = ?", (other, other)).fetchall():
            col = "partner1_id" if f["partner1_id"] == other else "partner2_id"
            b.update("families", f["id"], {col: keep})
        for fc in conn.execute("SELECT * FROM family_children WHERE child_id = ?", (other,)).fetchall():
            fam_key = f"{fc['family_id']}|{other}"
            b.hard_delete("family_children", fam_key, op="unlink")
            if not conn.execute("SELECT 1 FROM family_children WHERE family_id = ? AND child_id = ?",
                                (fc["family_id"], keep)).fetchone():
                b.insert("family_children", {"family_id": fc["family_id"], "child_id": keep, "position": fc["position"],
                                             "relation": fc["relation"]}, op="link")
        # "This is me"
        if ou:
            conn.execute("UPDATE users SET me_person_id = NULL WHERE id = ?", (ou["id"],))
            conn.execute("UPDATE users SET me_person_id = ? WHERE id = ?", (keep, ou["id"]))
        # the other person goes, pointing at the kept one
        b.update("people", other, {"deleted_at": b.now, "merged_into": keep}, op="delete")
        # their history now also shows on the kept person's History tab
        conn.execute("INSERT OR IGNORE INTO batch_people (batch_id, person_id) "
                     "SELECT batch_id, ? FROM batch_people WHERE person_id = ?", (keep, other))
        b.touch(keep, other)
        reject_new_loops(conn, [keep], set())
        return {"person": person_detail(conn, keep, user["me_person_id"] if user["me_person_id"] != other else keep),
                "batchId": b.id}
