"""Sources and citations (§13.9): where information came from — a
certificate, a book, a website, a scanned document, or an interview ("told by
Grandma, 2026") — and which facts each one supports. History entities."""
from fastapi import APIRouter, Depends, HTTPException
from pydantic import Field

from .. import features, db, graph as graph_mod, names
from ..auth import require_user
from ..models import Strict, clean, require_person
from ..history import Batch

router = APIRouter(prefix="/api", tags=["sources"], dependencies=[Depends(features.required("sources"))])

KINDS = ("certificate", "document", "book", "website", "photo", "interview", "other")
FACTS = ("name", "gender", "birth", "death", "parents", "biography")        # plus custom:<field_id>


def source_out(r, counts=None) -> dict:
    return {"id": r["id"], "title": r["title"], "kind": r["kind"], "author": r["author"], "date": r["date_text"],
            "repository": r["repository"], "url": r["url"], "note": r["note"], "mediaId": r["media_id"],
            "toldBy": r["told_by_person_id"], "createdAt": r["created_at"],
            "citations": counts.get(r["id"], 0) if counts is not None else None}


def citation_out(conn, c) -> dict:
    s = conn.execute("SELECT id, title, kind FROM sources WHERE id = ?", (c["source_id"],)).fetchone()
    return {"id": c["id"], "sourceId": c["source_id"], "sourceTitle": s["title"] if s else "?", "sourceKind": s["kind"] if s else None,
            "personId": c["person_id"], "familyId": c["family_id"], "eventId": c["event_id"], "fact": c["fact"],
            "page": c["page"], "quality": c["quality"], "note": c["note"]}


def citations_for_person(conn, pid: str) -> list:
    """Citations on the person, their events, and the families they're a partner in."""
    rows = conn.execute(
        "SELECT c.* FROM citations c JOIN sources s ON s.id = c.source_id AND s.deleted_at IS NULL WHERE c.person_id = ? "
        "OR c.event_id IN (SELECT id FROM events WHERE person_id = ?) "
        "OR c.family_id IN (SELECT id FROM families WHERE partner1_id = ? OR partner2_id = ?) "
        "OR c.event_id IN (SELECT e.id FROM events e JOIN families f ON f.id = e.family_id "
        "                  WHERE f.partner1_id = ? OR f.partner2_id = ?) ORDER BY c.created_at",
        (pid,) * 6).fetchall()
    return [citation_out(conn, c) for c in rows]


class SourceIn(Strict):
    title: str = Field(min_length=1, max_length=200)
    kind: str = "document"
    author: str | None = Field(default=None, max_length=200)
    date: str | None = Field(default=None, max_length=60)
    repository: str | None = Field(default=None, max_length=200)
    url: str | None = Field(default=None, max_length=500)
    note: str | None = Field(default=None, max_length=5000)
    mediaId: str | None = None
    toldBy: str | None = None


def _source_fields(conn, body: SourceIn, sent: set) -> dict:
    out = {}
    if "title" in sent:
        out["title"] = clean(body.title)
        if not out["title"]:
            raise HTTPException(422, "Give the source a title.")
    if "kind" in sent:
        if body.kind not in KINDS:
            raise HTTPException(422, f"Kind must be one of: {', '.join(KINDS)}.")
        out["kind"] = body.kind
    for k, col in (("author", "author"), ("date", "date_text"), ("repository", "repository"), ("note", "note")):
        if k in sent:
            out[col] = clean(getattr(body, k))
    if "url" in sent:
        u = clean(body.url)
        if u and not u.lower().startswith(("http://", "https://")):
            raise HTTPException(422, "A web address must start with https:// or http://.")
        out["url"] = u
    if "mediaId" in sent:
        if body.mediaId and not conn.execute("SELECT 1 FROM media WHERE id = ? AND deleted_at IS NULL", (body.mediaId,)).fetchone():
            raise HTTPException(404, "That document isn't in Photos & documents.")
        out["media_id"] = body.mediaId
    if "toldBy" in sent:
        if body.toldBy:
            require_person(conn, body.toldBy)
        out["told_by_person_id"] = body.toldBy
    return out


@router.get("/sources")
def list_sources(q: str | None = None, user: dict = Depends(require_user)):
    with db.get_conn() as conn:
        counts = {r[0]: r[1] for r in conn.execute("SELECT source_id, COUNT(*) FROM citations GROUP BY source_id")}
        rows = conn.execute("SELECT * FROM sources WHERE deleted_at IS NULL ORDER BY title COLLATE NOCASE").fetchall()
    items = [source_out(r, counts) for r in rows]
    if q and q.strip():
        n = q.strip().lower()
        items = [s for s in items if n in " ".join(filter(None, (s["title"], s["author"], s["repository"], s["note"]))).lower()]
    return {"items": items}


@router.post("/sources", status_code=201)
def create_source(body: SourceIn, user: dict = Depends(require_user)):
    with db.get_conn() as conn:
        fields = _source_fields(conn, body, set(SourceIn.model_fields) | {"title", "kind"})
        b = Batch(conn, user["id"], f"Added the source {fields['title']}")
        sid = db.new_id()
        b.insert("sources", dict(fields, id=sid, created_by=user["id"], created_at=b.now, updated_at=b.now))
        if fields.get("told_by_person_id"):
            b.touch(fields["told_by_person_id"])
        return {"source": source_out(conn.execute("SELECT * FROM sources WHERE id = ?", (sid,)).fetchone()), "batchId": b.id}


def _source(conn, sid):
    r = conn.execute("SELECT * FROM sources WHERE id = ?", (sid,)).fetchone()
    if not r or r["deleted_at"]:
        raise HTTPException(404, "That source isn't there (it may have been deleted).")
    return r


@router.get("/sources/{sid}")
def get_source(sid: str, user: dict = Depends(require_user)):
    """The source and everything it supports, with names."""
    with db.get_conn() as conn:
        r = _source(conn, sid)
        g = graph_mod.get(conn)
        cites = []
        for c in conn.execute("SELECT * FROM citations WHERE source_id = ? ORDER BY created_at", (sid,)):
            item = citation_out(conn, c)
            if c["event_id"]:
                ev = conn.execute("SELECT * FROM events WHERE id = ?", (c["event_id"],)).fetchone()
                item["what"] = ev["type"] if ev else "event"
                owner = ev["person_id"] if ev else None
                if ev and ev["family_id"] and ev["family_id"] in g.families:
                    owner = (g.families[ev["family_id"]].partners() or [None])[0]
            elif c["family_id"]:
                item["what"] = "family"
                fam = g.families.get(c["family_id"])
                owner = fam.partners()[0] if fam and fam.partners() else None
            else:
                item["what"] = c["fact"] or "record"
                owner = c["person_id"]
            item["personId"] = owner
            item["personName"] = g.people[owner].name if owner in g.people else None
            cites.append(item)
        out = source_out(r)
        out["toldByName"] = g.people[r["told_by_person_id"]].name if r["told_by_person_id"] in g.people else None
        out["citationsList"] = cites
        return out


@router.patch("/sources/{sid}")
def patch_source(sid: str, body: SourceIn, user: dict = Depends(require_user)):
    with db.get_conn() as conn:
        r = _source(conn, sid)
        fields = _source_fields(conn, body, body.model_fields_set)
        b = Batch(conn, user["id"], f"Edited the source {r['title']}")
        if fields:
            b.update("sources", sid, fields)
        return {"source": source_out(conn.execute("SELECT * FROM sources WHERE id = ?", (sid,)).fetchone()), "batchId": b.id}


@router.delete("/sources/{sid}")
def delete_source(sid: str, user: dict = Depends(require_user)):
    """A source goes to the trash-like soft delete; its citations stop showing. Undo brings both back."""
    with db.get_conn() as conn:
        r = _source(conn, sid)
        b = Batch(conn, user["id"], f"Deleted the source {r['title']}")
        b.soft_delete("sources", sid)
        return {"ok": True, "batchId": b.id}


class CitationIn(Strict):
    sourceId: str
    personId: str | None = None
    familyId: str | None = None
    eventId: str | None = None
    fact: str | None = Field(default=None, max_length=60)
    page: str | None = Field(default=None, max_length=200)
    quality: int | None = Field(default=None, ge=0, le=3)
    note: str | None = Field(default=None, max_length=2000)


class CitationPatch(Strict):
    page: str | None = Field(default=None, max_length=200)
    quality: int | None = Field(default=None, ge=0, le=3)
    note: str | None = Field(default=None, max_length=2000)


def _owner_people(conn, c) -> list:
    if c.get("person_id"):
        return [c["person_id"]]
    if c.get("event_id"):
        e = conn.execute("SELECT person_id, family_id FROM events WHERE id = ?", (c["event_id"],)).fetchone()
        if not e:
            return []
        if e["person_id"]:
            return [e["person_id"]]
        c = {"family_id": e["family_id"]}
    f = conn.execute("SELECT partner1_id, partner2_id FROM families WHERE id = ?", (c.get("family_id"),)).fetchone()
    return [x for x in (f["partner1_id"], f["partner2_id"]) if x] if f else []


@router.post("/citations", status_code=201)
def add_citation(body: CitationIn, user: dict = Depends(require_user)):
    if sum(bool(x) for x in (body.personId, body.familyId, body.eventId)) != 1:
        raise HTTPException(422, "A citation is for exactly one person, family or event.")
    if body.fact and body.fact not in FACTS and not body.fact.startswith("custom:"):
        raise HTTPException(422, f"fact must be one of {', '.join(FACTS)} or custom:<field id>.")
    with db.get_conn() as conn:
        src = _source(conn, body.sourceId)
        if body.personId:
            require_person(conn, body.personId)
        elif body.familyId and not conn.execute("SELECT 1 FROM families WHERE id = ? AND deleted_at IS NULL", (body.familyId,)).fetchone():
            raise HTTPException(404, "That family isn't in the tree.")
        elif body.eventId and not conn.execute("SELECT 1 FROM events WHERE id = ?", (body.eventId,)).fetchone():
            raise HTTPException(404, "That event doesn't exist.")
        row = {"id": db.new_id(), "source_id": body.sourceId, "person_id": body.personId, "family_id": body.familyId,
               "event_id": body.eventId, "fact": body.fact, "page": clean(body.page), "quality": body.quality,
               "note": clean(body.note)}
        people = _owner_people(conn, row)
        who = names.row_name(conn.execute("SELECT * FROM people WHERE id = ?", (people[0],)).fetchone()) if people else "a family"
        b = Batch(conn, user["id"], f"Cited {src['title']} for {who}")
        row["created_at"] = b.now
        b.insert("citations", row)
        b.touch(*people)
        return {"citation": citation_out(conn, conn.execute("SELECT * FROM citations WHERE id = ?", (row["id"],)).fetchone()),
                "batchId": b.id}


@router.patch("/citations/{cid}")
def patch_citation(cid: str, body: CitationPatch, user: dict = Depends(require_user)):
    with db.get_conn() as conn:
        c = conn.execute("SELECT * FROM citations WHERE id = ?", (cid,)).fetchone()
        if not c:
            raise HTTPException(404, "That citation doesn't exist.")
        sent = body.model_fields_set
        upd = {k: (clean(getattr(body, k)) if k != "quality" else body.quality) for k in sent}
        b = Batch(conn, user["id"], "Edited a citation")
        if upd:
            b.update("citations", cid, upd)
        b.touch(*_owner_people(conn, dict(c)))
        return {"citation": citation_out(conn, conn.execute("SELECT * FROM citations WHERE id = ?", (cid,)).fetchone()),
                "batchId": b.id}


@router.delete("/citations/{cid}")
def delete_citation(cid: str, user: dict = Depends(require_user)):
    with db.get_conn() as conn:
        c = conn.execute("SELECT * FROM citations WHERE id = ?", (cid,)).fetchone()
        if not c:
            raise HTTPException(404, "That citation doesn't exist.")
        b = Batch(conn, user["id"], "Removed a citation")
        b.touch(*_owner_people(conn, dict(c)))
        b.hard_delete("citations", cid)
        return {"ok": True, "batchId": b.id}


@router.get("/citations")
def list_citations(personId: str | None = None, eventId: str | None = None, familyId: str | None = None,
                   user: dict = Depends(require_user)):
    with db.get_conn() as conn:
        if personId:
            return {"items": citations_for_person(conn, personId)}
        col, val = ("event_id", eventId) if eventId else ("family_id", familyId)
        if not val:
            raise HTTPException(422, "Say whose citations: personId, eventId or familyId.")
        rows = conn.execute(f"SELECT c.* FROM citations c JOIN sources s ON s.id = c.source_id AND s.deleted_at IS NULL "
                            f"WHERE c.{col} = ?", (val,)).fetchall()
        return {"items": [citation_out(conn, c) for c in rows]}
