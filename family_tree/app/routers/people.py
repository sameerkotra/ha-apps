"""People: list/search, detail, create/edit/delete/restore, and the quick adds
(add parent / partner / child), each one history batch."""
import json

from fastapi import APIRouter, Depends, HTTPException, Query

from .. import config, dates, db, features, graph as graph_mod, kin, names, relations, sides
from ..auth import require_admin, require_user
from ..common import (RELATIONS, FAMILY_KINDS, LifeEventIn, PersonIn, PersonRef, clean, create_person, date_cols,
                      event_out, life_out, person_fields, reject_new_loops, require_family, require_person, resolve_ref,
                      set_life_event, summary_with_rel, warnings_for)
from ..history import Batch

router = APIRouter(prefix="/api", tags=["people"])


class _Lazy:
    """Routers that import this one (custom, sources, contacts) are reached lazily."""
    def __init__(self, name):
        self.name = name

    def __getattr__(self, attr):
        import importlib
        return getattr(importlib.import_module(f"app.routers.{self.name}"), attr)


custom_router, sources_router, contacts_router = _Lazy("custom"), _Lazy("sources"), _Lazy("contacts")
tithi_router = _Lazy("tithi")


# ---------- list ----------
@router.get("/people")
def list_people(q: str | None = None, living: str | None = None, surname: str | None = None,
                has_photo: bool | None = None, remind: str | None = Query(None, pattern="^(on|off)$"),
                side: str | None = Query(None, pattern="^(paternal|maternal)$"),
                field: str | None = None, value: str | None = None, sort: str = "name", page: int = Query(1, ge=1),
                page_size: int = Query(50, ge=1, le=500), user: dict = Depends(require_user)):
    with db.get_conn() as conn:
        g = graph_mod.get(conn)
        rows = {r["id"]: r for r in conn.execute(
            "SELECT id, other_names, birth_surname, nickname, updated_at, given_local, surname_local FROM people "
            "WHERE deleted_at IS NULL")}
        custom_on, script_on = features.on("custom_fields"), features.on("script_names")
        if (field or value) and not custom_on:
            features.check("custom_fields", user)
        if remind is not None:
            features.check("reminders", user)
        if side:
            features.check("sides", user)
        custom_vals = {}
        for r in conn.execute("SELECT v.person_id, v.field_id, v.value FROM custom_values v JOIN custom_fields f "
                              "ON f.id = v.field_id WHERE f.archived = 0 AND v.person_id IS NOT NULL"):
            custom_vals.setdefault(r["person_id"], {})[r["field_id"]] = r["value"]
        if side:
            if user["me_person_id"] not in g.people:
                raise HTTPException(422, "Father's side and mother's side are worked out from you — set “This is me” first.")
            side_map = sides.get(conn, g, user["me_person_id"])
    items = []
    needle = (q or "").strip().lower()
    # "babai" (or "cousin") also finds everyone who is that to you (§13.1)
    rel_label = None
    me = user["me_person_id"] if user["me_person_id"] in g.people else None
    if needle and me and kin.is_kin_word(needle, kin.current()):
        rel_label = kin.labeler(g, me)
    for p in g.people.values():
        r = rows.get(p.id)
        if not r:
            continue
        if needle:
            hay = " ".join(x for x in (p.given, p.surname, p.birth_surname, p.nickname,
                                       *((r["given_local"], r["surname_local"]) if script_on else ()),
                                       *(custom_vals.get(p.id, {}).values() if custom_on else ())) if x).lower()
            if r["other_names"]:
                hay += " " + " ".join(n.get("name", "") for n in json.loads(r["other_names"])).lower()
            by_name = all(part in hay for part in needle.split())
            by_rel = bool(rel_label) and p.id != me and kin.word_matches(needle, rel_label(p.id))
            if not (by_name or by_rel):
                continue
        alive = graph_mod.living(p)
        if living == "living" and not alive or living == "deceased" and alive:
            continue
        if surname and (p.surname or "").lower() != surname.lower() and (p.birth_surname or "").lower() != surname.lower():
            continue
        if has_photo is not None and bool(p.photo) != has_photo:
            continue
        if remind is not None and p.remind != (remind == "on"):
            continue
        if side and side_map.get(p.id) not in (side, "both"):
            continue
        if field and (custom_vals.get(p.id, {}).get(field) or "").lower() != (value or "").lower() \
                and not (value is None and custom_vals.get(p.id, {}).get(field)):
            continue
        s = graph_mod.summary(p)
        s.update(birthDisplay=dates.display(p.birth) if p.birth else None,
                 deathDisplay=dates.display(p.death) if p.death else None,
                 birthSurname=p.birth_surname, nickname=p.nickname, updatedAt=r["updated_at"],
                 _sort_birth=(p.birth or {}).get("sort_key"))
        items.append(s)
    if sort == "birth":
        items.sort(key=lambda s: (s["_sort_birth"] is None, s["_sort_birth"] or "", s["name"].lower()))
    elif sort == "changed":
        items.sort(key=lambda s: s["updatedAt"] or "", reverse=True)
    else:
        items.sort(key=lambda s: ((s["surname"] or "~").lower(), (s["given"] or "").lower()))
    total = len(items)
    chunk = items[(page - 1) * page_size: page * page_size]
    for s in chunk:
        s.pop("_sort_birth", None)
    return {"items": chunk, "total": total, "page": page, "pageSize": page_size}


@router.get("/surnames")
def surnames(user: dict = Depends(require_user)):
    with db.get_conn() as conn:
        rows = conn.execute("SELECT surname, COUNT(*) AS n FROM people WHERE deleted_at IS NULL AND surname IS NOT NULL "
                            "GROUP BY surname COLLATE NOCASE ORDER BY surname COLLATE NOCASE").fetchall()
    return [{"surname": r["surname"], "count": r["n"]} for r in rows]


# ---------- detail ----------
def person_detail(conn, pid: str, me_id: str | None) -> dict:
    row = require_person(conn, pid)
    g = graph_mod.get(conn)
    p = g.people[pid]
    today = config.today()
    alive = graph_mod.living(p)
    events = [event_out(r) for r in conn.execute(
        "SELECT * FROM events WHERE person_id = ? ORDER BY sort_key IS NULL, sort_key, date_m, date_d, created_at", (pid,))]
    age = dates.age_on(p.birth, today) if alive else dates.age_between(p.birth, p.death)

    def sref(x):
        return summary_with_rel(g, x, me_id)

    parents = [dict(sref(par), familyId=fid, relation=rel) for par, fid, rel in g.parents(pid)]
    parent_families = []
    for fid, rel in p.parent_fams:
        fam = g.families[fid]
        parent_families.append({"id": fid, "relation": rel, "partners": [sref(x) for x in fam.partners()],
                                "openSlot": len(fam.partners()) < 2})
    families = []
    for fid in p.partner_fams:
        fam = g.families[fid]
        other = fam.other(pid)
        fam_events = [event_out(r) for r in conn.execute(
            "SELECT * FROM events WHERE family_id = ? ORDER BY sort_key IS NULL, sort_key", (fid,))]
        families.append({
            "id": fid, "kind": fam.kind, "ended": fam.ended,
            "partner": sref(other) if other else None,
            "marriage": life_out(fam.marriage),
            "events": fam_events,
            "children": [dict(sref(cid), relation=rel, position=pos) for cid, rel, pos in fam.children],
        })
    siblings = [dict(sref(sid), full=full) for sid, full in g.siblings(pid)]
    claimed = conn.execute("SELECT id, name FROM users WHERE me_person_id = ?", (pid,)).fetchone()
    rel_to_me = (kin.describe(g, me_id, relations.relationship(g, me_id, pid), kin.current())
                 if me_id and me_id in g.people else None)
    on = features.states()
    if not on["ceremonies"]:
        events = [e for e in events if e["type"] not in features.CEREMONY_TYPES]
        for f in families:
            f["events"] = [e for e in f["events"] if e["type"] not in features.CEREMONY_TYPES]
    script = on["script_names"]
    return {
        "id": pid,
        "givenNames": row["given_names"], "surname": row["surname"], "birthSurname": row["birth_surname"],
        "givenLocal": row["given_local"] if script else None, "surnameLocal": row["surname_local"] if script else None,
        "nameLocal": p.local_name if script else None,
        "localScript": names.script_of((row["given_local"] or "") + (row["surname_local"] or "")) if script else None,
        "nameOrder": row["name_order"], "nameOrderEffective": names.order(row["name_order"]),
        "nickname": row["nickname"], "neverExport": bool(row["never_export"]), "remind": bool(row["remind"]) and on["reminders"], "otherNames": json.loads(row["other_names"]) if row["other_names"] else [],
        "name": p.name, "gender": row["gender"], "deceased": bool(row["deceased"]), "living": alive,
        "biography": row["biography"], "photo": row["photo_media_id"],
        "photoRegion": row["photo_region_id"] if row["photo_media_id"] else None,
        "birth": life_out(p.birth), "death": life_out(p.death),
        "age": age, "years": dates.years_label(p.birth, p.death, alive),
        "events": events, "parents": parents, "parentFamilies": parent_families,
        "families": families, "siblings": siblings,
        "relationshipToMe": rel_to_me,
        "claimedBy": {"id": claimed["id"], "name": claimed["name"]} if claimed else None,
        "warnings": warnings_for(g, pid),
        "photoCount": _media_count(conn, pid, "photo"),
        "documentCount": _media_count(conn, pid, "document"),
        "storyCount": _count(conn, "SELECT COUNT(*) FROM stories WHERE person_id = ?", pid),
        "createdAt": row["created_at"], "updatedAt": row["updated_at"],
        "custom": custom_router.values_for(conn, person_id=pid) if on["custom_fields"] else [],
        "citations": sources_router.citations_for_person(conn, pid) if on["sources"] else [],
        "contacts": contacts_router.contacts_for(conn, pid) if alive and on["contacts"] else [],
        "tithi": tithi_router.for_person(conn, p, kin.current()) if on["tithi"] else {},
    }


def _count(conn, sql, pid) -> int:
    return conn.execute(sql, (pid,)).fetchone()[0]


def _media_count(conn, pid, kind) -> int:
    """What their Photos & documents tab lists (GET /media?personId=): tagged
    with them, or with one of their events."""
    return conn.execute("SELECT COUNT(*) FROM media m WHERE m.kind = ? AND m.deleted_at IS NULL AND m.id IN ("
                        "SELECT media_id FROM media_links WHERE person_id = ? UNION SELECT ml.media_id FROM media_links ml "
                        "JOIN events e ON e.id = ml.event_id WHERE e.person_id = ?)", (kind, pid, pid)).fetchone()[0]


@router.get("/people/{pid}")
def get_person(pid: str, user: dict = Depends(require_user)):
    with db.get_conn() as conn:
        return person_detail(conn, pid, user["me_person_id"])


@router.get("/people/{pid}/timeline")
def timeline(pid: str, user: dict = Depends(require_user)):
    """The person's own events plus relatives' births, marriages and deaths during their life."""
    with db.get_conn() as conn:
        require_person(conn, pid)
        g = graph_mod.get(conn)
        p = g.people[pid]
        items = [dict(event_out(r), own=True) for r in conn.execute(
            "SELECT * FROM events WHERE person_id = ? AND date_text IS NOT NULL", (pid,))]
        for fid in p.partner_fams:
            for r in conn.execute("SELECT * FROM events WHERE family_id = ? AND date_text IS NOT NULL", (fid,)):
                items.append(dict(event_out(r), own=True))
        start = (p.birth or {}).get("sort_key")
        end = (p.death or {}).get("sort_key") if p.death else None
        relatives = []
        relatives += [(par, "parent") for par, _f, _r in g.parents(pid)]
        relatives += [(c, "child") for c, _f, _r in g.children(pid)]
        relatives += [(s, "sibling") for s, _full in g.siblings(pid)]
        relatives += [(x, "partner") for x, _f in g.partners(pid) if x]
        for gc_parent, _kind in [(c, "child") for c, _f, _r in g.children(pid)]:
            relatives += [(gc, "grandchild") for gc, _f, _r in g.children(gc_parent)]
        me_id = user["me_person_id"]
        seen = set()
        for rid, kind in relatives:
            if rid in seen:
                continue
            seen.add(rid)
            rp = g.people[rid]
            label = relations.relationship(g, pid, rid)["label"]
            for etype, ev in (("birth", rp.birth), ("death", rp.death)):
                if not ev or not ev.get("sort_key"):
                    continue
                if etype == "birth" and kind in ("parent", "partner"):
                    continue
                key = ev["sort_key"]
                if start and key < start or end and key > end:
                    continue
                items.append({"type": f"relative-{etype}", "personId": rid, "name": rp.name, "relation": label,
                              "dateDisplay": dates.display(ev), "sortKey": key, "place": ev.get("place"), "own": False})
        if not features.on("ceremonies"):
            items = [e for e in items if e["type"] not in features.CEREMONY_TYPES]
        items.sort(key=lambda e: (e.get("sortKey") is None, e.get("sortKey") or ""))
        return {"items": items, "meId": me_id}


# ---------- create / edit / delete ----------
@router.post("/people", status_code=201)
def create(body: PersonIn, user: dict = Depends(require_user)):
    with db.get_conn() as conn:
        b = Batch(conn, user["id"], "Added person")
        pid = create_person(b, body)
        name = conn.execute("SELECT given_names, surname, nickname, name_order FROM people WHERE id = ?", (pid,)).fetchone()
        conn.execute("UPDATE batches SET label = ? WHERE id = ?", (f"Added {_nm(name)}", b.id))
        return {"person": person_detail(conn, pid, user["me_person_id"]), "batchId": b.id}


def _relabel(conn, b: Batch, new_id: str, role: str, anchor_row) -> None:
    """'Added Subba Rao as a parent of Lakshmi Sharma' reads better in History."""
    row = conn.execute("SELECT * FROM people WHERE id = ?", (new_id,)).fetchone()
    conn.execute("UPDATE batches SET label = ? WHERE id = ?", (f"Added {_nm(row)} as {role} of {_nm(anchor_row)}"[:200], b.id))


def _nm(row) -> str:
    return names.row_name(row)


@router.patch("/people/{pid}")
def update(pid: str, body: PersonIn, user: dict = Depends(require_user)):
    with db.get_conn() as conn:
        row = require_person(conn, pid)
        fields = person_fields(body, only_set=True)
        sent = body.model_fields_set
        label = f"Edited {_nm(row)}"
        if set(fields) == {"remind"} and not sent - {"remind"}:        # the 🔔 button
            label = f"Turned reminders {'on' if fields['remind'] else 'off'} for {_nm(row)}"
        b = Batch(conn, user["id"], label)
        if fields:
            merged = {**row, **fields}
            if not any(merged.get(f) for f in ("given_names", "surname", "nickname")):
                raise HTTPException(422, "Give the person at least a first name, surname or nickname.")
            b.update("people", pid, fields)
        if "birth" in sent:
            set_life_event(b, pid, "birth", body.birth)
        if "death" in sent:
            set_life_event(b, pid, "death", body.death)
        b.touch(pid)
        return {"person": person_detail(conn, pid, user["me_person_id"]), "batchId": b.id}


@router.delete("/people/{pid}")
def delete(pid: str, user: dict = Depends(require_user)):
    with db.get_conn() as conn:
        row = require_person(conn, pid)
        b = Batch(conn, user["id"], f"Deleted {_nm(row)}")
        b.soft_delete("people", pid)
        b.touch(pid)
        return {"ok": True, "batchId": b.id}


@router.post("/people/{pid}/restore")
def restore(pid: str, user: dict = Depends(require_admin)):
    """Restore from the trash — admin-only, like the Trash page."""
    with db.get_conn() as conn:
        row = require_person(conn, pid, allow_deleted=True)
        if not row["deleted_at"]:
            raise HTTPException(409, "That person isn't in the trash.")
        b = Batch(conn, user["id"], f"Restored {_nm(row)}")
        b.restore("people", pid)
        reject_new_loops(conn, [pid], set())
        b.touch(pid)
        return {"person": person_detail(conn, pid, user["me_person_id"]), "batchId": b.id}


# ---------- quick adds ----------
class AddParent(PersonRef):
    relation: str = "birth"
    familyId: str | None = None


class AddPartner(PersonRef):
    kind: str = "married"
    familyId: str | None = None          # fill the empty slot of this family instead of starting a new one
    marriage: LifeEventIn | None = None


class AddChild(PersonRef):
    relation: str = "birth"
    familyId: str | None = None


def _check_relation(rel: str):
    if rel not in RELATIONS:
        raise HTTPException(422, "Relation must be birth, adopted, step, foster or unknown.")


def child_position(conn, g, fid: str, child_id: str) -> int:
    """Birth order: after siblings born earlier (when dates are known), else last."""
    kids = conn.execute("SELECT child_id, position FROM family_children WHERE family_id = ? ORDER BY position",
                        (fid,)).fetchall()
    if not kids:
        return 0
    row = conn.execute("SELECT sort_key FROM events WHERE person_id = ? AND type = 'birth'", (child_id,)).fetchone()
    mine = row["sort_key"] if row else None
    last = kids[-1]["position"] + 1
    if not mine:
        return last
    for k in kids:
        r = conn.execute("SELECT sort_key FROM events WHERE person_id = ? AND type = 'birth'", (k["child_id"],)).fetchone()
        if r and r["sort_key"] and r["sort_key"] > mine:
            return k["position"]
    return last


def add_child_link(b: Batch, fid: str, cid: str, relation: str) -> None:
    """Insert family_children at its birth-order position, shifting later siblings."""
    conn = b.conn
    if conn.execute("SELECT 1 FROM family_children WHERE family_id = ? AND child_id = ?", (fid, cid)).fetchone():
        raise HTTPException(409, "That child is already in this family.")
    fam = require_family(conn, fid)
    g = graph_mod.load_fresh(conn)
    for par in (fam["partner1_id"], fam["partner2_id"]):
        if par and g.people.get(par) and cid in g.people and g.is_descendant(par, cid):
            raise HTTPException(422, "That would make someone their own ancestor.")
    pos = child_position(conn, g, fid, cid)
    for k in conn.execute("SELECT child_id, position FROM family_children WHERE family_id = ? AND position >= ? "
                          "ORDER BY position DESC", (fid, pos)).fetchall():
        b.update("family_children", f"{fid}|{k['child_id']}", {"position": k["position"] + 1})
    b.insert("family_children", {"family_id": fid, "child_id": cid, "position": pos, "relation": relation}, op="link")
    b.touch(cid, fam["partner1_id"], fam["partner2_id"])


def open_slot(conn, fam) -> str | None:
    """The partner column a new partner/parent goes into: an empty one, else one
    whose person is in the trash (they're hidden everywhere, so the family shows
    that slot as open). None when both partners are live."""
    for col in ("partner1_id", "partner2_id"):
        if not fam[col]:
            return col
    for col in ("partner2_id", "partner1_id"):
        row = conn.execute("SELECT deleted_at FROM people WHERE id = ?", (fam[col],)).fetchone()
        if not row or row["deleted_at"]:
            return col
    return None


def new_family(b: Batch, p1: str | None, p2: str | None, kind: str = "married") -> str:
    if kind not in FAMILY_KINDS:
        raise HTTPException(422, "Kind must be married, partners or unknown.")
    if p1 and p1 == p2:
        raise HTTPException(422, "Someone can't be their own partner.")
    # keep a male partner first when we know, so charts put him on the left
    if p1 and p2:
        g1 = b.conn.execute("SELECT gender FROM people WHERE id = ?", (p1,)).fetchone()["gender"]
        g2 = b.conn.execute("SELECT gender FROM people WHERE id = ?", (p2,)).fetchone()["gender"]
        if g1 == "female" and g2 == "male":
            p1, p2 = p2, p1
    fid = db.new_id()
    b.insert("families", {"id": fid, "partner1_id": p1, "partner2_id": p2, "kind": kind,
                          "created_at": b.now, "updated_at": b.now})
    b.touch(p1, p2)
    return fid


def set_marriage(b: Batch, fid: str, m: LifeEventIn | None) -> None:
    if m is None:
        return
    cols = date_cols(m.date)
    place = clean(m.place)
    row = b.conn.execute("SELECT id FROM events WHERE family_id = ? AND type = 'marriage'", (fid,)).fetchone()
    if cols["date_text"] is None and not place:
        if row:
            b.hard_delete("events", row["id"])
        return
    if row:
        b.update("events", row["id"], dict(cols, place=place))
    else:
        b.insert("events", dict(cols, id=db.new_id(), family_id=fid, type="marriage", place=place,
                                created_at=b.now, updated_at=b.now))


@router.post("/people/{pid}/add-parent", status_code=201)
def add_parent(pid: str, body: AddParent, user: dict = Depends(require_user)):
    _check_relation(body.relation)
    with db.get_conn() as conn:
        child = require_person(conn, pid)
        b = Batch(conn, user["id"], f"Added a parent of {_nm(child)}")
        parent_id = resolve_ref(b, body)
        g = graph_mod.load_fresh(conn)
        if g.is_descendant(parent_id, pid):
            raise HTTPException(422, "That would make someone their own ancestor.")
        target = None
        fams = conn.execute("SELECT f.*, fc.relation FROM family_children fc JOIN families f ON f.id = fc.family_id "
                            "WHERE fc.child_id = ? AND f.deleted_at IS NULL", (pid,)).fetchall()
        if body.familyId:
            target = next((f for f in fams if f["id"] == body.familyId), None)
            if not target:
                raise HTTPException(404, "That family isn't one of this person's parent families.")
            if not open_slot(conn, target):
                raise HTTPException(409, "That family already has two parents.")
        else:
            open_fams = [f for f in fams if open_slot(conn, f) and f["relation"] == body.relation]
            if open_fams:
                target = open_fams[0]
            elif any(f["relation"] == body.relation for f in fams) and body.relation == "birth":
                raise HTTPException(409, f"{_nm(child)} already has both birth parents. To add an adoptive, step "
                                         "or foster parent, choose that relation.")
        if target:
            if parent_id in (target["partner1_id"], target["partner2_id"]):
                raise HTTPException(409, "They're already a parent in that family.")
            for k in conn.execute("SELECT child_id FROM family_children WHERE family_id = ?", (target["id"],)).fetchall():
                if k["child_id"] in g.people and g.is_descendant(parent_id, k["child_id"]):
                    raise HTTPException(422, "That would make someone their own ancestor.")
            slot = open_slot(conn, target)
            b.update("families", target["id"], {slot: parent_id})
            b.touch(parent_id, target["partner1_id"], target["partner2_id"])
        else:
            fid = new_family(b, parent_id, None, "married")
            add_child_link(b, fid, pid, body.relation)
        b.touch(pid)
        _relabel(conn, b, parent_id, "a parent", child)
        return {"person": person_detail(conn, pid, user["me_person_id"]), "newPersonId": parent_id, "batchId": b.id}


@router.post("/people/{pid}/add-partner", status_code=201)
def add_partner(pid: str, body: AddPartner, user: dict = Depends(require_user)):
    with db.get_conn() as conn:
        person = require_person(conn, pid)
        b = Batch(conn, user["id"], f"Added a partner of {_nm(person)}")
        partner_id = resolve_ref(b, body)
        if partner_id == pid:
            raise HTTPException(422, "Someone can't be their own partner.")
        if body.familyId:
            fam = require_family(conn, body.familyId)
            if pid not in (fam["partner1_id"], fam["partner2_id"]):
                raise HTTPException(404, "That family doesn't belong to this person.")
            slot = open_slot(conn, fam)
            if not slot:
                raise HTTPException(409, "That family already has two partners.")
            b.update("families", fam["id"], {slot: partner_id, "kind": body.kind if body.kind in FAMILY_KINDS else fam["kind"]})
            fid = fam["id"]
            g = graph_mod.load_fresh(conn)
            for k in conn.execute("SELECT child_id FROM family_children WHERE family_id = ?", (fid,)).fetchall():
                if k["child_id"] in g.people and g.is_descendant(partner_id, k["child_id"]):
                    raise HTTPException(422, "That would make someone their own ancestor.")
        else:
            fid = new_family(b, pid, partner_id, body.kind)
        set_marriage(b, fid, body.marriage)
        b.touch(pid, partner_id)
        _relabel(conn, b, partner_id, "a partner", person)
        return {"person": person_detail(conn, pid, user["me_person_id"]), "newPersonId": partner_id,
                "familyId": fid, "batchId": b.id}


@router.post("/people/{pid}/add-child", status_code=201)
def add_child(pid: str, body: AddChild, user: dict = Depends(require_user)):
    _check_relation(body.relation)
    with db.get_conn() as conn:
        parent = require_person(conn, pid)
        b = Batch(conn, user["id"], f"Added a child of {_nm(parent)}")
        fams = conn.execute("SELECT * FROM families WHERE deleted_at IS NULL AND (partner1_id = ? OR partner2_id = ?)",
                            (pid, pid)).fetchall()
        if body.familyId:
            if not any(f["id"] == body.familyId for f in fams):
                raise HTTPException(404, "That family doesn't belong to this person.")
            fid = body.familyId
        elif len(fams) == 1:
            fid = fams[0]["id"]
        elif not fams:
            fid = None
        else:
            raise HTTPException(422, "This person has more than one partner — choose which family the child belongs to.")
        child_id = resolve_ref(b, body)
        if fid is None:
            fid = new_family(b, pid, None, "unknown")
        add_child_link(b, fid, child_id, body.relation)
        _relabel(conn, b, child_id, "a child", parent)
        return {"person": person_detail(conn, pid, user["me_person_id"]), "newPersonId": child_id,
                "familyId": fid, "batchId": b.id}

