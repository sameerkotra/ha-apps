"""Me, unlocking, locking, settings, the whoami page, the generator."""
from fastapi import APIRouter, Depends, HTTPException
from pydantic import Field

from .. import alerts, config, copies, db, passwords, service, sessions, settings
from ..auth import get_current_user, require_user
from .common import Ctx, Strict, unlocked, unlocked_any

router = APIRouter(prefix="/api", tags=["me"])

NOTICE_TEXT = {
    "user_setup": "An admin set up your vault.",
    "user_reset": "An admin reset your vault (your old Personal vault was wiped).",
    "user_enabled": "An admin turned your access back on.",
    "backup_restored": "An admin restored a backup. Changes made after it was taken are gone — check your vaults.",
    "shared": "{actor} gave you access to {vault}.",
    "unshared": "{actor} removed you from {vault}.",
    "password_changed": "The password of {vault} was changed by {actor} — ask them for the new one.",
    "rekeyed": None,
    "emergency_added": "{actor} made you an emergency contact (Settings → Emergency access).",
    "emergency_revoked": "{actor} removed you as an emergency contact.",
    # emergency_requested: shown as its own banner with Deny / Approve (app.js emergencyNotice)
    "emergency_denied": "{actor} denied your request for emergency access.",
    "emergency_granted": "You now have {vault} (read-only).",
}


def _notices(conn, user_id: str, since: str | None) -> list:
    if not since:
        return []
    rows = conn.execute("SELECT a.*, v.name AS vname, u.name AS aname FROM audit_log a LEFT JOIN vaults v ON v.id = a.vault_id "
                        "LEFT JOIN users u ON u.id = a.actor_id WHERE a.user_id = ? AND a.created_at > ? "
                        "AND (a.actor_id IS NULL OR a.actor_id != a.user_id) ORDER BY a.created_at DESC LIMIT 20",
                        (user_id, since)).fetchall()
    out = []
    for r in rows:
        t = NOTICE_TEXT.get(r["action"])
        if not t:
            continue
        vault = alerts.vault_label(conn, r["vault_id"]) if r["vault_id"] else "a vault"
        out.append({"at": r["created_at"], "action": r["action"],
                    "text": t.format(actor=r["aname"] or "An admin", vault=vault)})
    notes = conn.execute("SELECT created_at FROM audit_log WHERE action = 'backup_restored' AND created_at > ? "
                         "ORDER BY created_at DESC LIMIT 1", (since,)).fetchone()
    if notes and not any(n["action"] == "backup_restored" for n in out):
        out.insert(0, {"at": notes["created_at"], "action": "backup_restored", "text": NOTICE_TEXT["backup_restored"]})
    return out


def _last(conn, user_id: str, action: str, own: bool = False) -> str | None:
    q = "SELECT MAX(created_at) FROM audit_log WHERE user_id = ? AND action = ?"
    args = [user_id, action]
    if own:                                   # my own master-password change, not someone else's vault
        q += " AND actor_id = ? AND vault_id = (SELECT id FROM vaults WHERE kind = 'personal' AND owner_user_id = ?)"
        args += [user_id, user_id]
    return conn.execute(q, args).fetchone()[0]


def me_out(conn, user: dict) -> dict:
    u = service.user_row(conn, user["id"])
    app = settings.all_values(conn)
    return {"id": user["id"], "name": user["name"], "username": user["username"], "isAdmin": user["is_admin"],
            "noAdmin": not config.ADMIN_NAMES,      # nobody can open Admin yet: the page shows how to fix that
            "disabled": user["disabled"],
            "status": u["status"], "autoLockMinutes": u["auto_lock_minutes"], "searchNotes": bool(u["search_notes"]),
            "clipboardClearSeconds": app["clipboard_clear_seconds"],
            "minPasswordLength": app["min_master_password_length"],
            "lastUnlock": u["previous_unlock"],
            "breachAllowed": bool(app["allow_breach_check"]),
            "breachCheck": bool(app["allow_breach_check"] and u["breach_check"]),
            "downloadReminderDays": u["download_reminder_days"],
            "lastDownloadAll": _last(conn, user["id"], "downloaded_all"),
            "lastMasterChange": _last(conn, user["id"], "password_changed", own=True),
            "notifyLinked": bool(alerts.services_for(conn, u)),
            "securityAlerts": bool(u["security_alerts"]), "expiryAlerts": bool(u["expiry_alerts"]),
            "hideLockSeconds": u["hide_lock_seconds"],
            "personalCopy": copies.status_for(conn, user["id"]),
            "guestWifi": conn.execute("SELECT 1 FROM guest_wifi").fetchone() is not None and u["status"] == "active"}


@router.get("/me")
def me(user: dict = Depends(get_current_user)):
    with db.get_conn() as conn:
        return me_out(conn, user)


class SettingsIn(Strict):
    autoLockMinutes: int | None = Field(default=None, ge=1, le=60)
    searchNotes: bool | None = None
    breachCheck: bool | None = None
    downloadReminderDays: int | None = None
    securityAlerts: bool | None = None
    expiryAlerts: bool | None = None
    hideLockSeconds: int | None = None


@router.put("/me/settings")
def put_settings(body: SettingsIn, user: dict = Depends(require_user)):
    with db.get_conn() as conn:
        if body.autoLockMinutes is not None:
            conn.execute("UPDATE users SET auto_lock_minutes = ? WHERE id = ?", (body.autoLockMinutes, user["id"]))
        if body.searchNotes is not None:
            conn.execute("UPDATE users SET search_notes = ? WHERE id = ?", (1 if body.searchNotes else 0, user["id"]))
        if body.breachCheck is not None:
            if body.breachCheck and not settings.all_values(conn)["allow_breach_check"]:
                raise HTTPException(403, "An admin hasn't allowed the breach check.")
            conn.execute("UPDATE users SET breach_check = ? WHERE id = ?", (1 if body.breachCheck else 0, user["id"]))
        if body.downloadReminderDays is not None:
            if body.downloadReminderDays not in (0, 30, 60, 90):
                raise HTTPException(422, "Remind after 30, 60 or 90 days, or never (0).")
            conn.execute("UPDATE users SET download_reminder_days = ? WHERE id = ?",
                         (body.downloadReminderDays, user["id"]))
        for flag, col in ((body.securityAlerts, "security_alerts"), (body.expiryAlerts, "expiry_alerts")):
            if flag is not None:
                conn.execute(f"UPDATE users SET {col} = ? WHERE id = ?", (1 if flag else 0, user["id"]))
        if body.hideLockSeconds is not None:
            if body.hideLockSeconds not in (-1, 0, 10, 60):
                raise HTTPException(422, "Lock when hidden: at once (0), after 10 or 60 seconds, or with Lock after (-1).")
            conn.execute("UPDATE users SET hide_lock_seconds = ? WHERE id = ?", (body.hideLockSeconds, user["id"]))
        user = dict(user, auto_lock_minutes=body.autoLockMinutes or user["auto_lock_minutes"])
        return me_out(conn, user)


@router.get("/whoami")
def whoami(user: dict = Depends(get_current_user)):
    """Works for disabled people too, so they can see why."""
    with db.get_conn() as conn:
        u = service.user_row(conn, user["id"])
        count = conn.execute("SELECT COUNT(*) FROM vault_members WHERE user_id = ?", (user["id"],)).fetchone()[0]
        linked = bool(alerts.services_for(conn, u))
    return {"haUserId": user["id"], "haUsername": user["username"], "haDisplayName": user["name"],
            "isAdmin": user["is_admin"], "displayNameOnly": user["display_name_only"],
            "adminEntries": len(config.ADMIN_NAMES), "noAdmin": not config.ADMIN_NAMES, "status": u["status"], "vaultCount": count,
            "disabled": user["disabled"], "notifyLinked": linked}


class UnlockIn(Strict):
    password: str = Field(min_length=1, max_length=1000)
    quickUnlockId: str | None = Field(default=None, max_length=64)      # the password came from a passkey (§12.5)


def _vault_list(conn, ctx_user: str, s: sessions.Session) -> list:
    # your own Emergency vault is a mirror of marked Personal items — never listed (or searched) twice
    rows = conn.execute("SELECT v.*, m.role FROM vaults v JOIN vault_members m ON m.vault_id = v.id AND m.user_id = ? "
                        "WHERE NOT (v.kind = 'emergency' AND v.owner_user_id = ?) "
                        "ORDER BY CASE v.kind WHEN 'personal' THEN 0 WHEN 'household' THEN 1 WHEN 'shared' THEN 2 ELSE 3 END, "
                        "v.name COLLATE NOCASE", (ctx_user, ctx_user)).fetchall()
    return [_vault_out(conn, r, s, ctx_user) for r in rows]


def _vault_out(conn, r, s: sessions.Session, uid: str) -> dict:
    members = conn.execute("SELECT m.user_id, m.role, u.name FROM vault_members m JOIN users u ON u.id = m.user_id "
                           "WHERE m.vault_id = ? ORDER BY CASE m.role WHEN 'owner' THEN 0 WHEN 'manager' THEN 1 "
                           "WHEN 'editor' THEN 2 ELSE 3 END, u.name COLLATE NOCASE", (r["id"],)).fetchall()
    remembered = conn.execute("SELECT 1 FROM vault_keys WHERE vault_id = ? AND user_id = ?", (r["id"], uid)).fetchone()
    owner = conn.execute("SELECT name FROM users WHERE id = ?", (r["owner_user_id"],)).fetchone() if r["owner_user_id"] else None
    upd = conn.execute("SELECT name FROM users WHERE id = ?", (r["updated_by"],)).fetchone() if r["updated_by"] else None
    name = r["name"]
    if r["kind"] == "personal" and r["owner_user_id"] != uid:
        name = f"{owner['name'] if owner else 'Someone'}'s Personal"
    elif r["kind"] == "emergency":
        name = f"{owner['name'] if owner else 'Someone'}'s emergency items"
    return {"id": r["id"], "kind": r["kind"], "name": name, "role": r["role"], "passwordMode": r["password_mode"],
            "open": r["id"] in s.vaults, "remembered": bool(remembered), "owner": owner["name"] if owner else None,
            "mine": r["owner_user_id"] == uid,
            "members": [{"id": m["user_id"], "name": m["name"], "role": m["role"]} for m in members],
            "version": r["version"], "updatedAt": r["updated_at"], "updatedBy": upd["name"] if upd else None,
            "rekeyPending": bool(r["rekey_pending"]), "passwordChangeSuggested": bool(r["password_change_suggested"])}


@router.post("/unlock")
def unlock(body: UnlockIn, user: dict = Depends(require_user)):
    from . import quick
    with db.get_conn() as conn:
        if body.quickUnlockId:
            quick.check_quick(conn, user["id"], body.quickUnlockId)
        try:
            s, report = service.unlock(conn, user, body.password)
        except HTTPException as e:
            if body.quickUnlockId and e.status_code == 403:
                quick.quick_failed(conn, body.quickUnlockId)
                conn.commit()
            raise
        if body.quickUnlockId:
            quick.quick_worked(conn, body.quickUnlockId)
        else:
            conn.execute("UPDATE users SET last_typed_unlock = ? WHERE id = ?", (config.now_iso(), user["id"]))
        u = service.user_row(conn, user["id"])
        notices = _notices(conn, user["id"], u["previous_unlock"])
        return {"session": s.token, "mustChangePassword": report["mustChangePassword"],
                "couldNotOpen": report["failed"], "notices": notices,
                "vaults": [] if s.must_change else _vault_list(conn, user["id"], s),
                "autoLockMinutes": u["auto_lock_minutes"]}


@router.post("/lock")
def lock(ctx: Ctx = Depends(unlocked_any)):
    sessions.end(ctx.session.token)
    return {"locked": True}


@router.post("/lock-everywhere")
def lock_everywhere(ctx: Ctx = Depends(unlocked_any)):
    n = sessions.end_user(ctx.user["id"])
    return {"locked": n}


@router.get("/session")
def session_state(ctx: Ctx = Depends(unlocked_any)):
    """Is my session still unlocked? (the page checks after sleeping)."""
    return {"unlocked": True, "mustChangePassword": ctx.session.must_change}


class ActivateIn(Strict):
    newPassword: str = Field(min_length=1, max_length=1000)


@router.post("/me/activate")
def activate(body: ActivateIn, ctx: Ctx = Depends(unlocked_any)):
    with db.get_conn() as conn:
        service.activate(conn, ctx.session, ctx.user, body.newPassword)
        return {"vaults": _vault_list(conn, ctx.user["id"], ctx.session)}


class PasswordIn(Strict):
    current: str = Field(min_length=1, max_length=1000)
    new: str = Field(min_length=1, max_length=1000)


@router.post("/me/password")
def change_password(body: PasswordIn, ctx: Ctx = Depends(unlocked)):
    with db.get_conn() as conn:
        service.change_master(conn, ctx.session, ctx.user, body.current, body.new)
    return {"ok": True}


@router.get("/users")
def users(ctx: Ctx = Depends(unlocked)):
    """Enabled, active people — for sharing."""
    with db.get_conn() as conn:
        rows = conn.execute("SELECT id, name FROM users WHERE disabled = 0 AND status = 'active' AND id != ? "
                            "ORDER BY name COLLATE NOCASE", (ctx.user["id"],)).fetchall()
    return {"users": [dict(r) for r in rows]}


class GenerateIn(Strict):
    kind: str = Field(default="random", pattern="^(random|passphrase)$")
    length: int = Field(default=20, ge=8, le=64)
    lower: bool = True
    upper: bool = True
    digits: bool = True
    symbols: bool = True
    avoidAmbiguous: bool = False
    words: int = Field(default=6, ge=3, le=10)
    separator: str = Field(default="-", max_length=3)
    capitalize: bool = False
    number: bool = False


@router.post("/generate")
def generate(body: GenerateIn, user: dict = Depends(require_user)):
    if body.kind == "passphrase":
        pw = passwords.passphrase(body.words, body.separator, body.capitalize, body.number)
    else:
        pw = passwords.generate(body.length, body.lower, body.upper, body.digits, body.symbols, body.avoidAmbiguous)
    return {"password": pw, "strength": passwords.strength(pw)}


class StrengthIn(Strict):
    password: str = Field(max_length=1000)


@router.post("/strength")
def strength(body: StrengthIn, user: dict = Depends(get_current_user)):
    return passwords.strength(body.password)
