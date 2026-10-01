"""Life events of people and families (birth and death also have their own
fields on the person form; these routes handle every event type)."""
from fastapi import APIRouter, Depends, HTTPException
from pydantic import Field

from .. import db, features
from ..auth import require_user
from ..common import (FAMILY_EVENT_TYPES, PERSON_EVENT_TYPES, TIME_PATTERN, TIME_TYPES, DateIn, Strict, check_time, clean,
                      date_cols, event_out,
                      require_family, require_person)
from ..history import Batch

router = APIRouter(prefix="/api", tags=["events"])

LABELS = {"birth": "birth", "death": "death", "burial": "burial", "baptism": "baptism", "education": "education",
          "occupation": "occupation", "residence": "residence", "immigration": "immigration",
          "emigration": "emigration", "military": "military service", "religion": "religious event",
          "retirement": "retirement", "custom": "event", "marriage": "marriage", "engagement": "engagement",
          "divorce": "divorce"}
from ..ceremonies import ALL as _CER   # noqa: E402
LABELS.update({k: v["en"].split(" (")[0] for k, v in _CER.items()})
MAX_EVENTS_PER_PERSON = 50


class EventIn(Strict):
    personId: str | None = None
    familyId: str | None = None
    type: str
    title: str | None = Field(default=None, max_length=200)
    date: DateIn | None = None
    time: str | None = Field(default=None, pattern=TIME_PATTERN)     # birth and death only
    place: str | None = Field(default=None, max_length=200)
    description: str | None = Field(default=None, max_length=2000)


class EventPatch(Strict):
    type: str | None = None
    title: str | None = Field(default=None, max_length=200)
    date: DateIn | None = None
    time: str | None = Field(default=None, pattern=TIME_PATTERN)     # birth and death only
    place: str | None = Field(default=None, max_length=200)
    description: str | None = Field(default=None, max_length=2000)


def _touch_owner(b: Batch, row) -> None:
    if row["person_id"]:
        b.touch(row["person_id"])
    else:
        f = b.conn.execute("SELECT partner1_id, partner2_id FROM families WHERE id = ?", (row["family_id"],)).fetchone()
        if f:
            b.touch(f["partner1_id"], f["partner2_id"])


def _check_type(etype: str, for_person: bool, user: dict | None = None):
    allowed = PERSON_EVENT_TYPES if for_person else FAMILY_EVENT_TYPES
    if etype not in allowed:
        raise HTTPException(422, f"'{etype}' isn't a {'person' if for_person else 'family'} event type.")
    if etype in features.CEREMONY_TYPES:
        features.check("ceremonies", user)


@router.post("/events", status_code=201)
def create_event(body: EventIn, user: dict = Depends(require_user)):
    if bool(body.personId) == bool(body.familyId):
        raise HTTPException(422, "An event belongs to either a person or a family.")
    _check_type(body.type, bool(body.personId), user)
    with db.get_conn() as conn:
        if body.personId:
            require_person(conn, body.personId)
            n = conn.execute("SELECT COUNT(*) FROM events WHERE person_id = ?", (body.personId,)).fetchone()[0]
            if n >= MAX_EVENTS_PER_PERSON:
                raise HTTPException(422, f"A person can have at most {MAX_EVENTS_PER_PERSON} events.")
        else:
            require_family(conn, body.familyId)
        if body.type in ("birth", "death", "marriage"):
            col = "person_id" if body.personId else "family_id"
            if conn.execute(f"SELECT 1 FROM events WHERE {col} = ? AND type = ?",
                            (body.personId or body.familyId, body.type)).fetchone():
                raise HTTPException(409, f"There's already a {body.type} — edit that one instead.")
        if body.type == "custom" and not clean(body.title):
            raise HTTPException(422, "Give a custom event a title.")
        cols = date_cols(body.date)
        if body.time and body.type not in TIME_TYPES:
            raise HTTPException(422, "Only a birth or a death can have a time.")
        cols["time"] = check_time(body.time, cols)
        b = Batch(conn, user["id"], f"Added {LABELS[body.type]}")
        eid = db.new_id()
        row = b.insert("events", dict(cols, id=eid, person_id=body.personId, family_id=body.familyId, type=body.type,
                                      title=clean(body.title), place=clean(body.place),
                                      description=(body.description or "").strip() or None,
                                      created_at=b.now, updated_at=b.now))
        _touch_owner(b, row)
        return {"event": event_out(conn.execute("SELECT * FROM events WHERE id = ?", (eid,)).fetchone()), "batchId": b.id}


@router.patch("/events/{eid}")
def update_event(eid: str, body: EventPatch, user: dict = Depends(require_user)):
    with db.get_conn() as conn:
        row = conn.execute("SELECT * FROM events WHERE id = ?", (eid,)).fetchone()
        if not row:
            raise HTTPException(404, "That event doesn't exist.")
        if row["type"] in features.CEREMONY_TYPES:
            features.check("ceremonies", user)
        sent = body.model_fields_set
        fields = {}
        if "type" in sent and body.type != row["type"]:
            _check_type(body.type, bool(row["person_id"]), user)
            if body.type in ("birth", "death", "marriage") or row["type"] in ("birth", "death", "marriage"):
                raise HTTPException(422, "Birth, death and marriage can't be changed into another type.")
            fields["type"] = body.type
        if "title" in sent:
            fields["title"] = clean(body.title)
        if "date" in sent:
            fields.update(date_cols(body.date))
        if "place" in sent:
            fields["place"] = clean(body.place)
        if "description" in sent:
            fields["description"] = (body.description or "").strip() or None
        if "time" in sent or "date" in sent:
            if body.time and row["type"] not in TIME_TYPES:
                raise HTTPException(422, "Only a birth or a death can have a time.")
            time = body.time if "time" in sent else row["time"]
            merged = {k: fields.get(k, row[k]) for k in ("date_y", "date_m", "date_d", "date_approx")}
            if "time" not in sent and time and (merged["date_approx"] or not all(merged[k] for k in ("date_y", "date_m", "date_d"))):
                time = None                          # the date is no longer exact: the time goes with it
            fields["time"] = check_time(time, merged)
        if (fields.get("type", row["type"]) == "custom") and not fields.get("title", row["title"]):
            raise HTTPException(422, "Give a custom event a title.")
        b = Batch(conn, user["id"], f"Edited {LABELS.get(row['type'], 'event')}")
        b.update("events", eid, fields)
        _touch_owner(b, row)
        return {"event": event_out(conn.execute("SELECT * FROM events WHERE id = ?", (eid,)).fetchone()), "batchId": b.id}


@router.delete("/events/{eid}")
def delete_event(eid: str, user: dict = Depends(require_user)):
    with db.get_conn() as conn:
        row = conn.execute("SELECT * FROM events WHERE id = ?", (eid,)).fetchone()
        if not row:
            raise HTTPException(404, "That event doesn't exist.")
        if row["type"] in features.CEREMONY_TYPES:
            features.check("ceremonies", user)
        b = Batch(conn, user["id"], f"Deleted {LABELS.get(row['type'], 'event')}")
        # media links to the event go with it (recorded, so undo brings them back)
        for ml in conn.execute("SELECT id FROM media_links WHERE event_id = ?", (eid,)).fetchall():
            b.hard_delete("media_links", ml["id"], op="unlink")
        b.hard_delete("events", eid)
        _touch_owner(b, row)
        return {"ok": True, "batchId": b.id}
