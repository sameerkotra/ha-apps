"""Me, my settings, "How the app sees you", and the people I can share with."""
import json

from fastapi import APIRouter, Depends, Request
from pydantic import Field

from .. import config, db, notify, settings, share_folders, tags
from ..auth import TURNED_OFF, get_current_user, require_user
from ..common import ha_notify, whoami as whoami_core
from ..search import saved
from ..store import moving, roots
from .common import Strict

router = APIRouter(prefix="/api", tags=["me"])

PREF_DEFAULTS = {"notifyShares": True, "foldersFirst": True, "sort": "name", "newSheets": "xlsx",
                 "numberStyle": "auto", "defaultNote": "note",
                 # step 8–11 (§17.14): follow notifications and quiet hours, AI buttons, the scan's file name
                 "notifyFollows": True, "quietFrom": "22:00", "quietTo": "07:00", "showAi": True,
                 "scanName": "Scan {date} {time}"}
# More fields for /api/me from later steps: fn(conn, user) -> {field: value}
ME_EXTRAS: list = []
# numberStyle (sheets, §4): "auto" = the device's own, "en" = 1,234.56, "de" = 1.234,56, "in" = 12,34,567.89
NUMBER_STYLES = ("auto", "en", "de", "in")
SORTS = ("name", "modified", "size")


def prefs_json(row) -> dict:
    p = dict(PREF_DEFAULTS)
    p.update({k: v for k, v in notify.prefs_of(row).items() if k in PREF_DEFAULTS})
    return p


def docs_folder_public(is_admin: bool) -> dict:
    st = roots.status()
    out = {"ok": st["ok"], "message": st["message"], "confirmed": st.get("confirmed", False)}
    if is_admin:
        out["path"] = st["path"]
    return out


@router.get("/me")
def me(user: dict = Depends(get_current_user)):
    root_id = None
    with db.get_conn() as conn:
        if not user["disabled"]:
            root = roots.ensure_person_root(conn, user["id"])         # their folder, made on the first visit
            root_id = root["id"] if root else None
        row = conn.execute("SELECT * FROM users WHERE id = ?", (user["id"],)).fetchone()
        s = settings.all_values(conn)
        linked = bool(ha_notify.services_for({"id": user["id"]}, conn))
        pinned = [] if user["disabled"] else saved.pinned(conn, user["id"])
        folders = [] if user["disabled"] else share_folders.for_user(conn, user, include_missing=user["is_admin"])
        tag_list = [] if user["disabled"] else tags.listing(conn, user)
        more: dict = {}
        if not user["disabled"]:
            for fn in ME_EXTRAS:
                more.update(fn(conn, user) or {})
    return {"id": user["id"], "name": user["name"], "username": user["username"], "nameSent": bool(user["username"]),
            "isAdmin": user["is_admin"], "displayNameOnly": user["display_name_only"],
            "noAdmin": not config.ADMIN_NAMES, "disabled": user["disabled"],
            "disabledMessage": TURNED_OFF if user["disabled"] else None,
            "folder": row["folder"], "rootId": root_id, "prefs": prefs_json(row), "notifyLinked": linked,
            "docsFolder": docs_folder_public(user["is_admin"]), "readOnly": moving.public_state(),
            "app": {"everyoneShares": s["everyone_shares"] and not user["is_child"],
                    "everyoneDefaultRole": s["everyone_default_role"],
                    "secretHint": s["secret_hint"], "maxDocMb": s["max_doc_mb"], "uploadMb": s["upload_mb"],
                    "regexSearch": s["regex_search"] and not user["is_child"], "currency": config.currency(),
                    "timeZone": config.timezone_name()},
            "pinnedSearches": pinned, "sharedFolders": folders, "tags": tag_list,
            "version": config.APP_VERSION, **more}


class PrefsIn(Strict):
    notifyShares: bool | None = None
    foldersFirst: bool | None = None
    sort: str | None = Field(default=None, pattern="^(name|modified|size)$")
    newSheets: str | None = Field(default=None, pattern="^(xlsx|csv)$")
    numberStyle: str | None = Field(default=None, pattern="^(auto|en|de|in)$")
    defaultNote: str | None = Field(default=None, pattern="^(note|markdown)$")
    notifyFollows: bool | None = None
    quietFrom: str | None = Field(default=None, pattern="^([01][0-9]|2[0-3]):[0-5][0-9]$")
    quietTo: str | None = Field(default=None, pattern="^([01][0-9]|2[0-3]):[0-5][0-9]$")
    showAi: bool | None = None
    scanName: str | None = Field(default=None, min_length=1, max_length=120)


@router.put("/me/settings")
def put_settings(body: PrefsIn, user: dict = Depends(require_user)):
    with db.get_conn() as conn:
        row = conn.execute("SELECT * FROM users WHERE id = ?", (user["id"],)).fetchone()
        p = prefs_json(row)
        for k, v in body.model_dump(exclude_none=True).items():
            p[k] = v
        conn.execute("UPDATE users SET prefs = ? WHERE id = ?", (json.dumps(p), user["id"]))
    return {"prefs": p}


@router.get("/whoami")
def whoami(request: Request, user: dict = Depends(get_current_user)):
    """Works for turned-off people too, so they can see why (common/whoami.py)."""
    with db.get_conn() as conn:
        linked = bool(ha_notify.services_for({"id": user["id"]}, conn))
        root = roots.person_root(conn, user["id"])
        n = conn.execute("SELECT COUNT(*) FROM nodes WHERE root_id = ? AND gone_at IS NULL AND trash_id IS NULL "
                         "AND kind != 'folder'", (root["id"],)).fetchone()[0] if root else 0
    return whoami_core.build(
        request, user_id=user["id"], username=user["username"], display_name=user["name"],
        is_admin=user["is_admin"], admin_entries=len(config.ADMIN_NAMES),
        display_name_only=user["display_name_only"], notify_linked=linked,
        extras=[whoami_core.row("Access", "Turned off by an admin" if user["disabled"] else "Yes",
                                tone="danger" if user["disabled"] else None),
                whoami_core.row("Your folder", user["folder"] or "Made when you first open the app"),
                whoami_core.row("Files in your folder", n),
                whoami_core.notify_row(linked)],
        disabled=user["disabled"])


@router.get("/people")
def people(user: dict = Depends(require_user)):
    """Everyone you could share with (people with access), for the Share and Transfer dialogs."""
    with db.get_conn() as conn:
        rows = conn.execute("SELECT id, name FROM users WHERE disabled = 0 ORDER BY name COLLATE NOCASE").fetchall()
    return {"people": [{"id": r["id"], "name": r["name"], "you": r["id"] == user["id"]} for r in rows]}
