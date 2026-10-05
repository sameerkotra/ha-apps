"""Custom fields (§13.8): admins define them (Settings → Custom
fields), everyone fills them in. Removing a field archives it — values are
kept and it can be restored. Every value change is a history batch."""
import json

from fastapi import APIRouter, Body, Depends, HTTPException
from pydantic import Field

from .. import features, dates, db
from ..auth import require_admin, require_user
from ..models import Strict, clean, require_family, require_person
from ..history import Batch
from .people import _nm

router = APIRouter(prefix="/api", tags=["custom"], dependencies=[Depends(features.required("custom_fields"))])

KINDS = ("text", "choice", "place", "date")
NAKSHATRAS = ["Ashwini", "Bharani", "Krittika", "Rohini", "Mrigashira", "Ardra", "Punarvasu", "Pushya", "Ashlesha",
              "Magha", "Purva Phalguni", "Uttara Phalguni", "Hasta", "Chitra", "Swati", "Vishakha", "Anuradha",
              "Jyeshtha", "Mula", "Purva Ashadha", "Uttara Ashadha", "Shravana", "Dhanishta", "Shatabhisha",
              "Purva Bhadrapada", "Uttara Bhadrapada", "Revati"]
RASIS = ["Mesha", "Vrishabha", "Mithuna", "Karka", "Simha", "Kanya", "Tula", "Vrischika", "Dhanu", "Makara",
         "Kumbha", "Meena"]
TEMPLATES = {
    "native_village": {"label": "Native village", "kind": "place"},
    "gotram": {"label": "Gotram", "kind": "text"},
    "nakshatram": {"label": "Nakshatram", "kind": "choice", "choices": NAKSHATRAS},
    "rasi": {"label": "Rasi", "kind": "choice", "choices": RASIS},
    "kuladaivam": {"label": "Kuladaivam (family deity)", "kind": "text"},
}
MAX_VALUE = 500


def field_out(r) -> dict:
    return {"id": r["id"], "label": r["label"], "kind": r["kind"],
            "choices": json.loads(r["choices"]) if r["choices"] else [], "appliesTo": r["applies_to"],
            "position": r["position"], "onCard": bool(r["on_card"]), "exportDefault": bool(r["export_default"]),
            "archived": bool(r["archived"])}


def fields(conn, applies_to: str | None = None, archived: bool = False) -> list:
    q = "SELECT * FROM custom_fields" + ("" if archived else " WHERE archived = 0")
    out = [field_out(r) for r in conn.execute(q + " ORDER BY position, label COLLATE NOCASE")]
    return [f for f in out if applies_to is None or f["appliesTo"] == applies_to]


def display(field: dict, value: str) -> str:
    return dates.field_display(field["kind"], value)


def values_for(conn, person_id: str | None = None, family_id: str | None = None) -> list:
    """[{fieldId, label, kind, value, display, choices, onCard}] for the non-archived fields that apply."""
    col, oid, applies = ("person_id", person_id, "person") if person_id else ("family_id", family_id, "family")
    vals = {r["field_id"]: r["value"] for r in conn.execute(f"SELECT field_id, value FROM custom_values WHERE {col} = ?", (oid,))}
    out = []
    for f in fields(conn, applies):
        v = vals.get(f["id"])
        out.append({"fieldId": f["id"], "label": f["label"], "kind": f["kind"], "choices": f["choices"],
                    "onCard": f["onCard"], "value": v, "display": display(f, v) if v is not None else None})
    return out


def _bump(conn):
    """Field definitions show on cards: make the cached tree graph reload."""
    db.set_setting(conn, "tree_version", db.new_id())


# ---------- field definitions (admin) ----------
class FieldIn(Strict):
    label: str = Field(min_length=1, max_length=60)
    kind: str = "text"
    choices: list[str] | None = Field(default=None, max_length=100)
    appliesTo: str = "person"
    onCard: bool = False
    exportDefault: bool = True


class FieldPatch(Strict):
    label: str | None = Field(default=None, min_length=1, max_length=60)
    choices: list[str] | None = Field(default=None, max_length=100)
    position: int | None = Field(default=None, ge=0, le=1000)
    onCard: bool | None = None
    exportDefault: bool | None = None
    archived: bool | None = None


def _check_choices(kind, choices):
    if kind == "choice":
        clean_c = [c.strip() for c in (choices or []) if c and c.strip()]
        if not clean_c:
            raise HTTPException(422, "A choice field needs at least one choice.")
        if any(len(c) > 60 for c in clean_c) or len(set(clean_c)) != len(clean_c):
            raise HTTPException(422, "Choices must be different and at most 60 characters.")
        return json.dumps(clean_c, ensure_ascii=False)
    return None


@router.get("/custom-fields")
def list_fields(archived: bool = False, user: dict = Depends(require_user)):
    with db.get_conn() as conn:
        return {"fields": fields(conn, archived=archived and user["is_admin"]),
                "templates": [{"key": k, **{kk: vv for kk, vv in t.items() if kk != "choices"}} for k, t in TEMPLATES.items()]}


@router.post("/custom-fields", status_code=201)
def create_field(body: FieldIn, admin: dict = Depends(require_admin)):
    if body.kind not in KINDS or body.appliesTo not in ("person", "family"):
        raise HTTPException(422, "Kind must be text, choice, place or date; a field applies to people or families.")
    label = " ".join(body.label.split())
    with db.get_conn() as conn:
        if conn.execute("SELECT 1 FROM custom_fields WHERE label = ? COLLATE NOCASE", (label,)).fetchone():
            raise HTTPException(409, f"There's already a field called {label}.")
        pos = conn.execute("SELECT COALESCE(MAX(position), -1) + 1 FROM custom_fields").fetchone()[0]
        fid = db.new_id()
        conn.execute("INSERT INTO custom_fields (id, label, kind, choices, applies_to, position, on_card, export_default) "
                     "VALUES (?, ?, ?, ?, ?, ?, ?, ?)", (fid, label, body.kind, _check_choices(body.kind, body.choices),
                                                         body.appliesTo, pos, int(body.onCard), int(body.exportDefault)))
        _bump(conn)
        return field_out(conn.execute("SELECT * FROM custom_fields WHERE id = ?", (fid,)).fetchone())


@router.post("/custom-fields/templates/{key}", status_code=201)
def add_template(key: str, admin: dict = Depends(require_admin)):
    t = TEMPLATES.get(key)
    if not t:
        raise HTTPException(404, "No such template.")
    return create_field(FieldIn(label=t["label"], kind=t["kind"], choices=t.get("choices")), admin)


@router.patch("/custom-fields/{fid}")
def patch_field(fid: str, body: FieldPatch, admin: dict = Depends(require_admin)):
    with db.get_conn() as conn:
        row = conn.execute("SELECT * FROM custom_fields WHERE id = ?", (fid,)).fetchone()
        if not row:
            raise HTTPException(404, "No such field.")
        sent = body.model_fields_set
        upd = {}
        if "label" in sent and body.label:
            label = " ".join(body.label.split())
            if conn.execute("SELECT 1 FROM custom_fields WHERE label = ? COLLATE NOCASE AND id != ?", (label, fid)).fetchone():
                raise HTTPException(409, f"There's already a field called {label}.")
            upd["label"] = label
        if "choices" in sent and row["kind"] == "choice":
            upd["choices"] = _check_choices("choice", body.choices)
        for k, col in (("position", "position"), ("onCard", "on_card"), ("exportDefault", "export_default"),
                       ("archived", "archived")):
            if k in sent and getattr(body, k) is not None:
                upd[col] = int(getattr(body, k))
        if upd:
            conn.execute(f"UPDATE custom_fields SET {', '.join(f'{k} = ?' for k in upd)} WHERE id = ?",
                         list(upd.values()) + [fid])
            _bump(conn)
        return field_out(conn.execute("SELECT * FROM custom_fields WHERE id = ?", (fid,)).fetchone())


# ---------- values (everyone) ----------
def clean_value(field: dict, raw) -> str | None:
    if raw is None:
        return None
    if not isinstance(raw, str):
        raise HTTPException(422, f"{field['label']}: send text.")
    v = clean(raw)
    if not v:
        return None
    if len(v) > MAX_VALUE:
        raise HTTPException(422, f"{field['label']} is too long (at most {MAX_VALUE} characters).")
    if field["kind"] == "choice" and v not in field["choices"]:
        raise HTTPException(422, f"{field['label']} must be one of: {', '.join(field['choices'])}.")
    if field["kind"] == "date":
        try:
            v = dates.parse_text(v)["date_text"]
        except dates.DateError as e:
            raise HTTPException(422, f"{field['label']}: {e}")
    return v


def set_values(b: Batch, owner_col: str, owner_id: str, values: dict, applies: str) -> int:
    conn = b.conn
    defs = {f["id"]: f for f in fields(conn, applies, archived=True)}
    n = 0
    for fid, raw in values.items():
        f = defs.get(fid)
        if not f or f["archived"]:
            raise HTTPException(422, "That field doesn't exist (or is archived).")
        v = clean_value(f, raw)
        row = conn.execute(f"SELECT * FROM custom_values WHERE field_id = ? AND {owner_col} = ?", (fid, owner_id)).fetchone()
        if v is None and row:
            b.hard_delete("custom_values", row["id"])
            n += 1
        elif v is not None and row and row["value"] != v:
            b.update("custom_values", row["id"], {"value": v, "updated_by": b.user_id})
            n += 1
        elif v is not None and not row:
            b.insert("custom_values", {"id": db.new_id(), "field_id": fid, owner_col: owner_id, "value": v,
                                       "updated_by": b.user_id, "updated_at": b.now})
            n += 1
    return n


@router.put("/people/{pid}/custom")
def put_person_values(pid: str, body: dict = Body(...), user: dict = Depends(require_user)):
    """{fieldId: value or null, …} — one history batch."""
    with db.get_conn() as conn:
        row = require_person(conn, pid)
        b = Batch(conn, user["id"], f"Edited details of {_nm(row)}")
        set_values(b, "person_id", pid, body, "person")
        b.touch(pid)
        return {"custom": values_for(conn, person_id=pid), "batchId": b.id}


@router.put("/families/{fid}/custom")
def put_family_values(fid: str, body: dict = Body(...), user: dict = Depends(require_user)):
    with db.get_conn() as conn:
        fam = require_family(conn, fid)
        b = Batch(conn, user["id"], "Edited family details")
        set_values(b, "family_id", fid, body, "family")
        b.touch(fam["partner1_id"], fam["partner2_id"])
        return {"custom": values_for(conn, family_id=fid), "batchId": b.id}


@router.get("/custom-fields/{fid}/values")
def field_values(fid: str, user: dict = Depends(require_user)):
    """Distinct values in use, for the People filter."""
    with db.get_conn() as conn:
        rows = conn.execute("SELECT value, COUNT(*) AS n FROM custom_values v JOIN people p ON p.id = v.person_id "
                            "WHERE v.field_id = ? AND p.deleted_at IS NULL GROUP BY value ORDER BY value COLLATE NOCASE",
                            (fid,)).fetchall()
    return [{"value": r["value"], "count": r["n"]} for r in rows]
