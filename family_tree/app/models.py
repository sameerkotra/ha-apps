"""Request models, validation and read helpers for the routers (was common.py)."""
import json

from fastapi import HTTPException
from pydantic import BaseModel, ConfigDict, Field

from . import config, dates, db, graph as graph_mod, kin
from .history import Batch

GENDERS = ("female", "male", "other", "unknown")
PERSON_EVENT_TYPES = ("birth", "death", "burial", "baptism", "education", "occupation", "residence",
                      "immigration", "emigration", "military", "religion", "retirement", "custom")
FAMILY_EVENT_TYPES = ("marriage", "engagement", "divorce", "residence", "custom")
RELATIONS = ("birth", "adopted", "step", "foster", "unknown")
FAMILY_KINDS = ("married", "partners", "unknown")
FAMILY_ENDED = ("divorced", "separated", "widowed")
from .ceremonies import FAMILY as _CER_F, PERSON as _CER_P   # noqa: E402  (§13.14)
PERSON_EVENT_TYPES = PERSON_EVENT_TYPES + tuple(_CER_P)
FAMILY_EVENT_TYPES = FAMILY_EVENT_TYPES + tuple(_CER_F)
OTHER_NAME_TYPES = ("aka", "married", "birth", "religious", "nickname", "other")


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class DateIn(Strict):
    qual: str = "exact"
    d: int | None = None
    m: int | None = None
    y: int | None = None
    d2: int | None = None
    m2: int | None = None
    y2: int | None = None


class OtherName(Strict):
    type: str = "aka"
    name: str = Field(min_length=1, max_length=100)


TIME_PATTERN = r"^([01][0-9]|2[0-3]):[0-5][0-9]$"
TIME_TYPES = ("birth", "death")


class LifeEventIn(Strict):
    date: DateIn | None = None
    time: str | None = Field(default=None, pattern=TIME_PATTERN)      # local time, 24-hour "HH:MM"
    place: str | None = Field(default=None, max_length=200)


def check_time(time: str | None, cols: dict) -> str | None:
    """A time needs an exact, full date (day, month and year)."""
    if not time:
        return None
    if cols.get("date_approx") or not (cols.get("date_y") and cols.get("date_m") and cols.get("date_d")):
        raise HTTPException(422, "A time needs the full, exact date (day, month and year).")
    return time


class PersonIn(Strict):
    given_names: str | None = Field(default=None, max_length=100)
    surname: str | None = Field(default=None, max_length=100)
    birth_surname: str | None = Field(default=None, max_length=100)
    nickname: str | None = Field(default=None, max_length=100)
    other_names: list[OtherName] | None = Field(default=None, max_length=10)
    gender: str | None = None
    deceased: bool | None = None
    biography: str | None = Field(default=None, max_length=20000)
    never_export: bool | None = None
    remind: bool | None = None                  # 🔔 (§9); ignored when creating — new people start off
    given_local: str | None = Field(default=None, max_length=100)     # §13.7 names in script
    surname_local: str | None = Field(default=None, max_length=100)
    name_order: str | None = None               # given_first | surname_first | None (App setting)
    birth: LifeEventIn | None = None
    death: LifeEventIn | None = None


class PersonRef(Strict):
    """Either an existing person or a new one."""
    existingId: str | None = None
    person: PersonIn | None = None


def clean(s: str | None) -> str | None:
    if s is None:
        return None
    s = " ".join(str(s).split()) if "\n" not in str(s) else str(s).strip()
    return s or None


def date_cols(d: DateIn | None) -> dict:
    try:
        cols = dates.from_parts(d.model_dump() if d else None)
    except dates.DateError as e:
        raise HTTPException(422, str(e))
    return cols or {"date_text": None, "date_y": None, "date_m": None, "date_d": None,
                    "date_approx": 0, "sort_key": None}


def person_fields(body: PersonIn, only_set: bool) -> dict:
    """PersonIn → people columns (only the fields the client sent, for PATCH)."""
    sent = body.model_fields_set if only_set else set(PersonIn.model_fields)
    from . import features
    if only_set and sent & {"given_local", "surname_local"}:
        features.check("script_names")
    if only_set and "remind" in sent:
        features.check("reminders")
    out = {}
    for f in ("given_names", "surname", "birth_surname", "nickname", "given_local", "surname_local"):
        if f in sent:
            out[f] = clean(getattr(body, f))
    if "biography" in sent:
        out["biography"] = (body.biography or "").strip() or None
    if "gender" in sent:
        g = body.gender or "unknown"
        if g not in GENDERS:
            raise HTTPException(422, "Gender must be female, male, other or unknown.")
        out["gender"] = g
    if "deceased" in sent:
        out["deceased"] = 1 if body.deceased else 0
    if "never_export" in sent:
        out["never_export"] = 1 if body.never_export else 0
    if "name_order" in sent:
        if body.name_order not in (None, "given_first", "surname_first"):
            raise HTTPException(422, "Name order must be given_first, surname_first or empty (the household default).")
        out["name_order"] = body.name_order
    if "remind" in sent:
        out["remind"] = 1 if body.remind else 0
    if "other_names" in sent:
        names = []
        for n in body.other_names or []:
            if n.type not in OTHER_NAME_TYPES:
                raise HTTPException(422, f"Unknown name type '{n.type}'.")
            names.append({"type": n.type, "name": clean(n.name)})
        out["other_names"] = json.dumps(names) if names else None
    return out


def require_person(conn, pid: str, allow_deleted: bool = False) -> dict:
    row = conn.execute("SELECT * FROM people WHERE id = ?", (pid,)).fetchone()
    if not row or (row["deleted_at"] and not allow_deleted):
        raise HTTPException(404, "That person isn't in the tree (they may have been deleted).")
    return dict(row)


def require_family(conn, fid: str, allow_deleted: bool = False) -> dict:
    row = conn.execute("SELECT * FROM families WHERE id = ?", (fid,)).fetchone()
    if not row or (row["deleted_at"] and not allow_deleted):
        raise HTTPException(404, "That family isn't in the tree.")
    return dict(row)


def set_life_event(batch: Batch, pid: str, etype: str, ev: LifeEventIn | None) -> None:
    """Create, update or remove a person's birth/death event."""
    conn = batch.conn
    row = conn.execute("SELECT * FROM events WHERE person_id = ? AND type = ?", (pid, etype)).fetchone()
    place = clean(ev.place) if ev else None
    cols = date_cols(ev.date if ev else None)
    time = check_time(ev.time if ev else None, cols)
    empty = cols["date_text"] is None and not place
    if empty:
        if row:
            batch.hard_delete("events", row["id"])
        return
    if row:
        batch.update("events", row["id"], dict(cols, place=place, time=time))
    else:
        batch.insert("events", dict(cols, id=db.new_id(), person_id=pid, type=etype, place=place, time=time,
                                    created_at=batch.now, updated_at=batch.now))


def create_person(batch: Batch, body: PersonIn) -> str:
    fields = person_fields(body, only_set=False)
    if fields.get("given_local") or fields.get("surname_local"):
        from . import features
        features.check("script_names")
    fields["remind"] = 0                        # reminders are off for every new person (§9)
    if not any(fields.get(f) for f in ("given_names", "surname", "nickname")):
        raise HTTPException(422, "Give the person at least a first name, surname or nickname.")
    pid = db.new_id()
    fields.setdefault("gender", "unknown")
    fields["deceased"] = fields.get("deceased") or 0
    batch.insert("people", dict(fields, id=pid, created_by=batch.user_id, created_at=batch.now, updated_at=batch.now))
    if body.birth:
        set_life_event(batch, pid, "birth", body.birth)
    if body.death:
        set_life_event(batch, pid, "death", body.death)
    batch.touch(pid)
    return pid


def resolve_ref(batch: Batch, ref: PersonRef, gender_hint: str | None = None) -> str:
    if ref.existingId:
        require_person(batch.conn, ref.existingId)
        batch.touch(ref.existingId)
        return ref.existingId
    if not ref.person:
        raise HTTPException(422, "Choose an existing person or enter a new one.")
    if gender_hint and (ref.person.gender in (None, "unknown")):
        ref.person.gender = gender_hint
    return create_person(batch, ref.person)


def event_out(r) -> dict:
    return {
        "id": r["id"], "personId": r["person_id"], "familyId": r["family_id"], "type": r["type"],
        "title": r["title"], "date": dates.to_parts(r), "dateText": r["date_text"], "dateDisplay": dates.display(r),
        "sortKey": r["sort_key"], "place": r["place"], "description": r["description"],
        "time": r["time"] if "time" in r.keys() else None,
    }


def life_out(ev: dict | None) -> dict | None:
    if not ev:
        return None
    return {"date": dates.to_parts(ev), "dateDisplay": dates.display(ev), "place": ev["place"],
            "time": ev.get("time") if isinstance(ev, dict) else None}


def warnings_for(g: graph_mod.Graph, pid: str) -> list:
    p = g.people.get(pid)
    if not p:
        return []
    out = []
    by = p.birth["date_y"] if p.birth else None
    dy = p.death["date_y"] if p.death else None
    if by and dy and (p.death["sort_key"] or "") < (p.birth["sort_key"] or ""):
        out.append("Death is recorded before birth.")
    if by and not p.deceased and not p.death and dates.born_over_110(p.birth, config.today()):
        out.append("Born more than 110 years ago with no death recorded — shown as deceased.")
    for par, _fid, rel in g.parents(pid):
        pp = g.people[par]
        pby = pp.birth["date_y"] if pp.birth else None
        if rel == "birth" and by and pby:
            if pby > by:
                out.append(f"Born before their parent {pp.name}.")
            elif by - pby < 12:
                out.append(f"{pp.name} would have been under 12 at this birth.")
    for cid, _fid, rel in g.children(pid):
        c = g.people[cid]
        cby = c.birth["date_y"] if c.birth else None
        if rel == "birth" and by and cby and cby < by:
            out.append(f"{c.name} is recorded as born before this parent.")
    if g.in_loop(pid):
        out.append("This person is recorded as their own ancestor — check the parent links.")
    return out


def people_in_loops(g: graph_mod.Graph, pids) -> set:
    return {p for p in pids if p and p in g.people and g.in_loop(p)}


def reject_new_loops(conn, pids, before: set, status: int = 422) -> None:
    """After a restore or undo (writes the add/link checks never see): refuse if
    it made any of `pids` their own ancestor — e.g. restoring a trashed person
    whose parent links were bridged by a newer link while they were deleted."""
    if people_in_loops(graph_mod.load_fresh(conn), pids) - set(before):
        raise HTTPException(status, "That would make someone their own ancestor — "
                                    "remove the newer parent link first.")


def summary_with_rel(g, pid, me_id):
    s = graph_mod.summary(g.people[pid])
    if me_id and me_id in g.people:
        s["relationship"] = kin.label_for(g, me_id, pid)
    return s
