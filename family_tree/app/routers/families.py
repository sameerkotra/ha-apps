"""Families (partners + children), their events, and quick family entry (§13.18)."""
from fastapi import APIRouter, Depends, HTTPException
from pydantic import Field

from .. import db, features, graph as graph_mod
from ..auth import require_admin, require_user
from ..models import (FAMILY_ENDED, FAMILY_KINDS, RELATIONS, LifeEventIn, PersonRef, Strict, event_out, life_out,
                      people_in_loops, reject_new_loops, require_family, require_person, resolve_ref,
                      summary_with_rel)
from ..history import Batch
from .people import _nm, add_child_link, new_family, set_marriage

router = APIRouter(prefix="/api", tags=["families"])


def family_out(conn, fid: str, me_id: str | None) -> dict:
    row = require_family(conn, fid)
    g = graph_mod.get(conn)
    fam = g.families[fid]
    events = [event_out(r) for r in conn.execute(
        "SELECT * FROM events WHERE family_id = ? ORDER BY sort_key IS NULL, sort_key", (fid,))]
    if not features.on("ceremonies"):
        events = [e for e in events if e["type"] not in features.CEREMONY_TYPES]
    return {
        "id": fid, "kind": row["kind"], "ended": row["ended"],
        "partners": [summary_with_rel(g, p, me_id) for p in fam.partners()],
        "marriage": life_out(fam.marriage), "events": events,
        "children": [dict(summary_with_rel(g, c, me_id), relation=rel, position=pos) for c, rel, pos in fam.children],
        "custom": _custom().values_for(conn, family_id=fid) if features.on("custom_fields") else [],
    }


def _custom():
    from . import custom
    return custom


def family_people(conn, fid) -> list:
    """Partners and children of a family (deleted ones included)."""
    row = conn.execute("SELECT partner1_id, partner2_id FROM families WHERE id = ?", (fid,)).fetchone()
    kids = [r["child_id"] for r in conn.execute("SELECT child_id FROM family_children WHERE family_id = ?", (fid,))]
    return [p for p in (row["partner1_id"], row["partner2_id"]) if p] + kids if row else kids


def _label(conn, fid) -> str:
    row = conn.execute("SELECT partner1_id, partner2_id FROM families WHERE id = ?", (fid,)).fetchone()
    names = []
    for pid in (row["partner1_id"], row["partner2_id"]):
        if pid:
            names.append(_nm(conn.execute("SELECT * FROM people WHERE id = ?", (pid,)).fetchone()))
    return " & ".join(names) or "a family"


class FamilyIn(Strict):
    partner1Id: str | None = None
    partner2Id: str | None = None
    kind: str = "married"
    marriage: LifeEventIn | None = None


class FamilyPatch(Strict):
    kind: str | None = None
    ended: str | None = None
    marriage: LifeEventIn | None = None


class ChildIn(PersonRef):
    relation: str = "birth"


class ChildPatch(Strict):
    relation: str | None = None
    position: int | None = Field(default=None, ge=0, le=500)


class QuickFamily(Strict):
    partners: list[PersonRef] = Field(min_length=1, max_length=2)
    kind: str = "married"
    marriage: LifeEventIn | None = None
    children: list[ChildIn] = Field(default_factory=list, max_length=30)


@router.get("/families/{fid}")
def get_family(fid: str, user: dict = Depends(require_user)):
    with db.get_conn() as conn:
        return family_out(conn, fid, user["me_person_id"])


@router.post("/families", status_code=201)
def create_family(body: FamilyIn, user: dict = Depends(require_user)):
    with db.get_conn() as conn:
        for pid in (body.partner1Id, body.partner2Id):
            if pid:
                require_person(conn, pid)
        if not (body.partner1Id or body.partner2Id):
            raise HTTPException(422, "A family needs at least one partner.")
        b = Batch(conn, user["id"], "Added a family")
        fid = new_family(b, body.partner1Id, body.partner2Id, body.kind)
        set_marriage(b, fid, body.marriage)
        conn.execute("UPDATE batches SET label = ? WHERE id = ?", (f"Added family {_label(conn, fid)}", b.id))
        return {"family": family_out(conn, fid, user["me_person_id"]), "batchId": b.id}


@router.post("/families/quick", status_code=201)
def quick_family(body: QuickFamily, user: dict = Depends(require_user)):
    """A couple (or one parent), their marriage and all their children in one batch."""
    if body.kind not in FAMILY_KINDS:
        raise HTTPException(422, "Kind must be married, partners or unknown.")
    with db.get_conn() as conn:
        b = Batch(conn, user["id"], "Added a family")
        ids = [resolve_ref(b, ref) for ref in body.partners]
        if len(ids) == 2 and ids[0] == ids[1]:
            raise HTTPException(422, "The two partners must be different people.")
        fid = new_family(b, ids[0], ids[1] if len(ids) > 1 else None, body.kind)
        set_marriage(b, fid, body.marriage)
        order: list[str] = []
        for ch in body.children:
            if ch.relation not in RELATIONS:
                raise HTTPException(422, "Relation must be birth, adopted, step, foster or unknown.")
            cid = resolve_ref(b, ch)
            if cid in order or cid in ids:
                raise HTTPException(422, "The same person appears twice in this family.")
            order.append(cid)
            add_child_link(b, fid, cid, ch.relation)
        # the form's row order is the birth order the person typed
        for pos, cid in enumerate(order):
            b.update("family_children", f"{fid}|{cid}", {"position": pos})
        conn.execute("UPDATE batches SET label = ? WHERE id = ?", (f"Added family {_label(conn, fid)}", b.id))
        return {"family": family_out(conn, fid, user["me_person_id"]), "batchId": b.id}



@router.patch("/families/{fid}")
def update_family(fid: str, body: FamilyPatch, user: dict = Depends(require_user)):
    with db.get_conn() as conn:
        row = require_family(conn, fid)
        b = Batch(conn, user["id"], f"Edited family {_label(conn, fid)}")
        fields = {}
        sent = body.model_fields_set
        if "kind" in sent:
            if body.kind not in FAMILY_KINDS:
                raise HTTPException(422, "Kind must be married, partners or unknown.")
            fields["kind"] = body.kind
        if "ended" in sent:
            if body.ended not in FAMILY_ENDED + (None,):
                raise HTTPException(422, "Ended must be divorced, separated, widowed or empty.")
            fields["ended"] = body.ended
        if fields:
            b.update("families", fid, fields)
        if "marriage" in sent:
            set_marriage(b, fid, body.marriage or LifeEventIn())
        b.touch(row["partner1_id"], row["partner2_id"])
        return {"family": family_out(conn, fid, user["me_person_id"]), "batchId": b.id}


@router.delete("/families/{fid}")
def delete_family(fid: str, user: dict = Depends(require_user)):
    with db.get_conn() as conn:
        row = require_family(conn, fid)
        b = Batch(conn, user["id"], f"Deleted family {_label(conn, fid)}")
        b.soft_delete("families", fid)
        b.touch(row["partner1_id"], row["partner2_id"],
                *[r["child_id"] for r in conn.execute("SELECT child_id FROM family_children WHERE family_id = ?", (fid,))])
        return {"ok": True, "batchId": b.id}


@router.post("/families/{fid}/restore")
def restore_family(fid: str, user: dict = Depends(require_admin)):
    """Restore from the trash — admin-only, like the Trash page."""
    with db.get_conn() as conn:
        row = require_family(conn, fid, allow_deleted=True)
        if not row["deleted_at"]:
            raise HTTPException(409, "That family isn't in the trash.")
        pids = family_people(conn, fid)
        before = people_in_loops(graph_mod.get(conn), pids)      # before the Batch bumps tree_version
        b = Batch(conn, user["id"], f"Restored family {_label(conn, fid)}")
        b.restore("families", fid)
        reject_new_loops(conn, pids, before)
        b.touch(row["partner1_id"], row["partner2_id"])
        return {"family": family_out(conn, fid, user["me_person_id"]), "batchId": b.id}


@router.post("/families/{fid}/children", status_code=201)
def add_child(fid: str, body: ChildIn, user: dict = Depends(require_user)):
    if body.relation not in RELATIONS:
        raise HTTPException(422, "Relation must be birth, adopted, step, foster or unknown.")
    with db.get_conn() as conn:
        require_family(conn, fid)
        b = Batch(conn, user["id"], f"Added a child to {_label(conn, fid)}")
        cid = resolve_ref(b, body)
        add_child_link(b, fid, cid, body.relation)
        return {"family": family_out(conn, fid, user["me_person_id"]), "newPersonId": cid, "batchId": b.id}


@router.patch("/families/{fid}/children/{cid}")
def update_child(fid: str, cid: str, body: ChildPatch, user: dict = Depends(require_user)):
    with db.get_conn() as conn:
        require_family(conn, fid)
        link = conn.execute("SELECT * FROM family_children WHERE family_id = ? AND child_id = ?", (fid, cid)).fetchone()
        if not link:
            raise HTTPException(404, "That child isn't in this family.")
        b = Batch(conn, user["id"], f"Edited a child of {_label(conn, fid)}")
        sent = body.model_fields_set
        if "relation" in sent:
            if body.relation not in RELATIONS:
                raise HTTPException(422, "Relation must be birth, adopted, step, foster or unknown.")
            b.update("family_children", f"{fid}|{cid}", {"relation": body.relation})
        if "position" in sent and body.position is not None:
            order = [r["child_id"] for r in conn.execute(
                "SELECT child_id FROM family_children WHERE family_id = ? ORDER BY position", (fid,))]
            order.remove(cid)
            order.insert(min(body.position, len(order)), cid)
            for pos, k in enumerate(order):
                b.update("family_children", f"{fid}|{k}", {"position": pos})
        b.touch(cid)
        return {"family": family_out(conn, fid, user["me_person_id"]), "batchId": b.id}


@router.delete("/families/{fid}/children/{cid}")
def remove_child(fid: str, cid: str, user: dict = Depends(require_user)):
    with db.get_conn() as conn:
        require_family(conn, fid)
        if not conn.execute("SELECT 1 FROM family_children WHERE family_id = ? AND child_id = ?", (fid, cid)).fetchone():
            raise HTTPException(404, "That child isn't in this family.")
        b = Batch(conn, user["id"], f"Removed a child from {_label(conn, fid)}")
        b.hard_delete("family_children", f"{fid}|{cid}", op="unlink")
        b.touch(cid)
        return {"family": family_out(conn, fid, user["me_person_id"]), "batchId": b.id}


class PartnerSet(Strict):
    personId: str | None = None


@router.put("/families/{fid}/partners/{slot}")
def set_partner(fid: str, slot: int, body: PartnerSet, user: dict = Depends(require_user)):
    """Replace or clear one partner (slot 1 or 2) — e.g. unlink a wrongly linked parent."""
    if slot not in (1, 2):
        raise HTTPException(404, "Slot must be 1 or 2.")
    with db.get_conn() as conn:
        row = require_family(conn, fid)
        col, other = (f"partner{slot}_id", row[f"partner{3 - slot}_id"])
        if body.personId:
            require_person(conn, body.personId)
            if body.personId == other:
                raise HTTPException(422, "Someone can't be their own partner.")
            g = graph_mod.load_fresh(conn)
            for k in conn.execute("SELECT child_id FROM family_children WHERE family_id = ?", (fid,)).fetchall():
                if k["child_id"] in g.people and g.is_descendant(body.personId, k["child_id"]):
                    raise HTTPException(422, "That would make someone their own ancestor.")
        elif not other:
            raise HTTPException(422, "A family needs at least one partner — delete the family instead.")
        b = Batch(conn, user["id"], f"Changed partners of {_label(conn, fid)}")
        b.update("families", fid, {col: body.personId})
        b.touch(row[col], body.personId, other)
        return {"family": family_out(conn, fid, user["me_person_id"]), "batchId": b.id}
