"""Relationship names (§13.1): your language, and the family's own
words (Settings → Relationship names). Any user can edit a term; every change
is a history batch, so it shows in History and can be undone."""
from fastapi import APIRouter, Depends, HTTPException
from pydantic import Field

from .. import config, db, features, kin
from ..auth import require_user
from ..models import Strict
from ..history import Batch

router = APIRouter(prefix="/api", tags=["kin"])

LANG_NAMES = {"te": "Telugu", "hi": "Hindi"}


class LangIn(Strict):
    kinLang: str | None = Field(default=None, pattern="^(en|te|hi)$")


@router.put("/me/kin-lang", dependencies=[Depends(features.required("kin_names"))])
def set_lang(body: LangIn, user: dict = Depends(require_user)):
    """Your own relationship-name language; null = the App setting."""
    with db.get_conn() as conn:
        conn.execute("UPDATE users SET kin_lang = ? WHERE id = ?", (body.kinLang, user["id"]))
    user = dict(user, kin_lang=body.kinLang)
    return {"kinLang": body.kinLang, "kinLangEffective": kin.user_lang(user)}


def _lang(lang: str) -> str:
    if lang not in LANG_NAMES:
        raise HTTPException(404, "Relationship names come in Telugu (te) and Hindi (hi).")
    return lang


def _key(key: str) -> str:
    if not kin.KEY_RE.match(key or ""):
        raise HTTPException(422, "That isn't a relationship key (like F.B- or M.Z.S+).")
    return key


def _order(key: str):
    base = key.split("@")[0]
    return (base.count("."), base.replace("+", "1").replace("-", "2"), key)


@router.get("/kin-terms", dependencies=[Depends(features.required("kin_names"))])
def list_terms(lang: str, user: dict = Depends(require_user)):
    _lang(lang)
    with db.get_conn() as conn:
        table = kin.terms(conn)[lang]
    keys = set(table) | set(kin.SEED[lang])
    items = []
    for k in sorted(keys, key=_order):
        row = table.get(k)
        items.append({"key": k, "meaning": kin.meaning_of_key(k), "term": row["term"] if row else None,
                      "note": row["note"] if row else None, "source": row["source"] if row else "seed",
                      "seedTerm": kin.SEED[lang].get(k)})
    return {"lang": lang, "language": LANG_NAMES[lang], "items": items}


class TermIn(Strict):
    term: str = Field(min_length=1, max_length=60)
    note: str | None = Field(default=None, max_length=120)


def _clean(s):
    return " ".join(s.split()) if s else None


@router.put("/kin-terms/{lang}/{key}", dependencies=[Depends(features.required("kin_names"))])
def put_term(lang: str, key: str, body: TermIn, user: dict = Depends(require_user)):
    _lang(lang)
    _key(key)
    term = _clean(body.term)
    if not term:
        raise HTTPException(422, "Type the word your family uses.")
    note = _clean(body.note)
    with db.get_conn() as conn:
        row = conn.execute("SELECT * FROM kin_terms WHERE lang = ? AND kin_key = ?", (lang, key)).fetchone()
        seed = kin.SEED[lang].get(key)
        label = f"{LANG_NAMES[lang]} name for {kin.meaning_of_key(key)}: {term}"
        if row and row["term"] == term and row["note"] == note:
            return {"ok": True, "batchId": None}
        if not row and seed == term and not note:
            return {"ok": True, "batchId": None}              # same as the default: nothing to store
        b = Batch(conn, user["id"], label)
        if row:
            b.update("kin_terms", f"{lang}|{key}", {"term": term, "note": note, "updated_by": user["id"]})
        else:
            b.insert("kin_terms", {"lang": lang, "kin_key": key, "term": term, "note": note, "source": "custom",
                                   "updated_by": user["id"], "updated_at": config.now_iso()})
        return {"ok": True, "batchId": b.id}


@router.delete("/kin-terms/{lang}/{key}", dependencies=[Depends(features.required("kin_names"))])
def reset_term(lang: str, key: str, user: dict = Depends(require_user)):
    """Reset to default: drop the family's own word (a key without a seed term disappears)."""
    _lang(lang)
    _key(key)
    with db.get_conn() as conn:
        row = conn.execute("SELECT * FROM kin_terms WHERE lang = ? AND kin_key = ?", (lang, key)).fetchone()
        if not row:
            raise HTTPException(404, "That name is already the default.")
        b = Batch(conn, user["id"], f"Reset the {LANG_NAMES[lang]} name for {kin.meaning_of_key(key)}")
        b.hard_delete("kin_terms", f"{lang}|{key}")
        return {"ok": True, "batchId": b.id}


class NameDisplayIn(Strict):
    nameDisplay: str = Field(pattern="^(en|script|both)$")


@router.put("/me/name-display", dependencies=[Depends(features.required("script_names"))])
def set_name_display(body: NameDisplayIn, user: dict = Depends(require_user)):
    """English names, names in script (where there is one), or both (§13.7)."""
    with db.get_conn() as conn:
        conn.execute("UPDATE users SET name_display = ? WHERE id = ?", (body.nameDisplay, user["id"]))
    return {"nameDisplay": body.nameDisplay}
