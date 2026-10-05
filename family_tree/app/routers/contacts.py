"""Contact details for living relatives, the address book and vCard (§13.11).
Visible to every enabled household user like the rest of the tree;
never exported unless someone ticks *Contact details* in the Export options.
Contacts of someone who has died are hidden (kept in history)."""
import re

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from pydantic import Field

from .. import features, db, graph as graph_mod, kin, settings, upcoming
from ..auth import require_user
from ..models import Strict, clean, require_person
from ..history import Batch
from .people import _nm

router = APIRouter(prefix="/api", tags=["contacts"], dependencies=[Depends(features.required("contacts"))])

KINDS = ("phone", "whatsapp", "email", "address", "other")
MAX_PER_PERSON = 20
_EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def normalize_phone(raw: str, default_code: str | None = None) -> str:
    """"+91 90000 12345" → "+919000012345"; "(202) 555-0100" → "+12025550100" with the
    default code; "0091…"/"011…" international prefixes too. ValueError if it can't be a number."""
    s = (raw or "").strip()
    plus = s.startswith("+")
    digits = re.sub(r"\D", "", s)
    if not digits:
        raise ValueError("That isn't a phone number.")
    if not plus:
        if digits.startswith("00"):
            digits, plus = digits[2:], True
        elif digits.startswith("011") and len(digits) > 11:
            digits, plus = digits[3:], True
    if not plus:
        code = (default_code or settings.get("default_phone_code")).lstrip("+")
        digits = code + digits.lstrip("0")
    if not 7 <= len(digits) <= 15:
        raise ValueError("A phone number has 7 to 15 digits, with its country code.")
    return "+" + digits


def contact_out(r) -> dict:
    v = r["value"]
    link = None
    if r["kind"] == "phone":
        link = f"tel:{v}"
    elif r["kind"] == "whatsapp":
        link = f"https://wa.me/{v.lstrip('+')}"
    elif r["kind"] == "email":
        link = f"mailto:{v}"
    elif r["kind"] == "address":
        from urllib.parse import quote
        link = f"https://maps.google.com/?q={quote(v)}"
    return {"id": r["id"], "kind": r["kind"], "label": r["label"], "value": v, "position": r["position"], "link": link}


def contacts_for(conn, pid: str) -> list:
    return [contact_out(r) for r in conn.execute("SELECT * FROM contacts WHERE person_id = ? ORDER BY position, rowid", (pid,))]


class ContactIn(Strict):
    kind: str
    label: str | None = Field(default=None, max_length=60)
    value: str = Field(min_length=1, max_length=500)


def clean_contact(kind: str, value: str) -> str:
    if kind not in KINDS:
        raise HTTPException(422, f"Kind must be one of: {', '.join(KINDS)}.")
    v = value.strip()
    try:
        if kind in ("phone", "whatsapp"):
            return normalize_phone(v)
    except ValueError as e:
        raise HTTPException(422, str(e))
    if kind == "email":
        if not _EMAIL.match(v):
            raise HTTPException(422, "That doesn't look like an email address.")
        return v.lower()
    v = "\n".join(" ".join(line.split()) for line in v.splitlines() if line.strip()) if kind == "address" else " ".join(v.split())
    if not v:
        raise HTTPException(422, "It's empty.")
    return v


def _living_person(conn, pid):
    row = require_person(conn, pid)
    g = graph_mod.get(conn)
    if not graph_mod.living(g.people[pid]):
        raise HTTPException(409, "Contact details are only for living relatives.")
    return row


@router.get("/phone-preview")
def phone_preview(value: str = Query(..., max_length=60), user: dict = Depends(require_user)):
    """What a typed number will be saved as — shown before saving."""
    try:
        return {"value": normalize_phone(value), "defaultCode": settings.get("default_phone_code")}
    except ValueError as e:
        raise HTTPException(422, str(e))


@router.post("/people/{pid}/contacts", status_code=201)
def add_contact(pid: str, body: ContactIn, user: dict = Depends(require_user)):
    with db.get_conn() as conn:
        row = _living_person(conn, pid)
        n = conn.execute("SELECT COUNT(*) FROM contacts WHERE person_id = ?", (pid,)).fetchone()[0]
        if n >= MAX_PER_PERSON:
            raise HTTPException(422, f"At most {MAX_PER_PERSON} contact details per person.")
        b = Batch(conn, user["id"], f"Added contact details for {_nm(row)}")
        b.insert("contacts", {"id": db.new_id(), "person_id": pid, "kind": body.kind, "label": clean(body.label),
                              "value": clean_contact(body.kind, body.value), "position": n, "updated_at": b.now})
        b.touch(pid)
        return {"contacts": contacts_for(conn, pid), "batchId": b.id}


class ContactPatch(Strict):
    label: str | None = Field(default=None, max_length=60)
    value: str | None = Field(default=None, min_length=1, max_length=500)
    position: int | None = Field(default=None, ge=0, le=100)


@router.patch("/contacts/{cid}")
def patch_contact(cid: str, body: ContactPatch, user: dict = Depends(require_user)):
    with db.get_conn() as conn:
        c = conn.execute("SELECT * FROM contacts WHERE id = ?", (cid,)).fetchone()
        if not c:
            raise HTTPException(404, "That contact detail doesn't exist.")
        row = _living_person(conn, c["person_id"])
        upd = {}
        sent = body.model_fields_set
        if "label" in sent:
            upd["label"] = clean(body.label)
        if "value" in sent and body.value:
            upd["value"] = clean_contact(c["kind"], body.value)
        if "position" in sent and body.position is not None:
            upd["position"] = body.position
        b = Batch(conn, user["id"], f"Edited contact details for {_nm(row)}")
        if upd:
            b.update("contacts", cid, upd)
        b.touch(c["person_id"])
        return {"contacts": contacts_for(conn, c["person_id"]), "batchId": b.id}


@router.delete("/contacts/{cid}")
def delete_contact(cid: str, user: dict = Depends(require_user)):
    with db.get_conn() as conn:
        c = conn.execute("SELECT * FROM contacts WHERE id = ?", (cid,)).fetchone()
        if not c:
            raise HTTPException(404, "That contact detail doesn't exist.")
        row = require_person(conn, c["person_id"])
        b = Batch(conn, user["id"], f"Removed contact details for {_nm(row)}")
        b.hard_delete("contacts", cid)
        b.touch(c["person_id"])
        return {"contacts": contacts_for(conn, c["person_id"]), "batchId": b.id}


# ---------- address book & vCard ----------
def _book(conn, user, scope: str, surname: str | None, ids: list | None):
    g = graph_mod.get(conn)
    me = user["me_person_id"] if user["me_person_id"] in g.people else None
    close = upcoming.close_family(g, me) if (scope == "close" and me) else None
    rows = {}
    for r in conn.execute("SELECT * FROM contacts ORDER BY position, rowid"):
        rows.setdefault(r["person_id"], []).append(r)
    label = kin.labeler(g, me)
    out = []
    for pid, cs in rows.items():
        p = g.people.get(pid)
        if not p or not graph_mod.living(p):
            continue
        if ids is not None and pid not in ids:
            continue
        if close is not None and pid not in close:
            continue
        if surname and (p.surname or "").lower() != surname.lower():
            continue
        s = graph_mod.summary(p)
        s["relationship"] = label(pid) if label and pid != me else None
        s["contacts"] = [contact_out(c) for c in cs]
        s["_p"] = p
        out.append(s)
    out.sort(key=lambda s: ((s["surname"] or "~").lower(), (s["given"] or "").lower()))
    return out


@router.get("/contacts")
def address_book(scope: str = Query("all", pattern="^(all|close)$"), surname: str | None = None,
                 user: dict = Depends(require_user)):
    with db.get_conn() as conn:
        items = _book(conn, user, scope, surname, None)
    for s in items:
        s.pop("_p")
    return {"items": items, "total": len(items)}


def _esc(v: str) -> str:
    return v.replace("\\", "\\\\").replace(",", "\\,").replace(";", "\\;").replace("\n", "\\n")


def vcard(p, contacts: list) -> str:
    lines = ["BEGIN:VCARD", "VERSION:3.0",
             f"N:{_esc(p.surname or '')};{_esc(p.given or '')};;;", f"FN:{_esc(p.name)}"]
    if p.nickname:
        lines.append(f"NICKNAME:{_esc(p.nickname)}")
    if p.local_name and features.on("script_names"):
        lines.append(f"X-PHONETIC-FIRST-NAME:{_esc(p.given or '')}")
        lines.append(f"NOTE:{_esc(p.local_name)}")
    b = p.birth or {}
    if b.get("date_m") and b.get("date_d"):
        if b.get("date_y"):
            lines.append(f"BDAY:{b['date_y']:04d}-{b['date_m']:02d}-{b['date_d']:02d}")
        else:                                           # the iPhone "no year" form
            lines.append(f"BDAY;X-APPLE-OMIT-YEAR=1604:1604-{b['date_m']:02d}-{b['date_d']:02d}")
    for c in contacts:
        typ = f";TYPE={_esc(c['label'])}" if c["label"] else ""
        if c["kind"] == "phone":
            lines.append(f"TEL{typ or ';TYPE=CELL'}:{c['value']}")
        elif c["kind"] == "whatsapp":
            lines.append(f"TEL;TYPE=WhatsApp:{c['value']}")
            lines.append(f"URL:https://wa.me/{c['value'].lstrip('+')}")
        elif c["kind"] == "email":
            lines.append(f"EMAIL{typ}:{c['value']}")
        elif c["kind"] == "address":
            lines.append(f"ADR{typ}:;;{_esc(c['value'])};;;;")
        else:
            lines.append(f"NOTE:{_esc((c['label'] + ': ') if c['label'] else '')}{_esc(c['value'])}")
    lines.append("END:VCARD")
    return "\r\n".join(lines) + "\r\n"


@router.get("/contacts.vcf")
def download_vcf(ids: str | None = None, scope: str = Query("all", pattern="^(all|close)$"),
                 user: dict = Depends(require_user)):
    """vCards for the selection (comma-separated ids), or the whole address book."""
    wanted = [x for x in (ids or "").split(",") if x] or None
    with db.get_conn() as conn:
        items = _book(conn, user, scope, None, wanted)
    if not items:
        raise HTTPException(404, "Nobody with contact details in that selection.")
    body = "".join(vcard(s["_p"], s["contacts"]) for s in items)
    return Response(body, media_type="text/vcard; charset=utf-8",
                    headers={"Content-Disposition": 'attachment; filename="family-contacts.vcf"'})
