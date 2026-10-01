"""Vault operations (SPEC §4, §5): setup and unlock, opening and saving vaults,
the key ring, Household, sharing, re-keying and password changes.

Routers call these with an open DB connection; file writes happen inside the
caller's transaction (storage.write_version), so a failure leaves the DB as
it was and the previous file version intact.
"""
import hmac
import os
import logging
import time
from datetime import timedelta

from fastapi import HTTPException

from . import alerts, config, db, kdbx, keyring, passwords, sessions, settings, storage

logger = logging.getLogger("service")
ROLES = ("owner", "manager", "editor", "viewer")
EDIT_ROLES = ("owner", "manager", "editor")
MANAGE_ROLES = ("owner", "manager")
MAX_SHARED_VAULTS = 50
MAX_MEMBERS = 20


# ---------------------------------------------------------------------------
# KDF settings
# ---------------------------------------------------------------------------

def strong_kdf(conn) -> dict:
    """Argon2id for person-chosen passwords: memory from config, iterations tuned once on
    this machine to take about KDF_TARGET_SECONDS, never fewer than 3 (SPEC §5.1)."""
    mem = config.KDF_MEMORY_KIB * 1024
    cached = db.get_setting(conn, "argon2_iterations")
    if cached and db.get_setting(conn, "argon2_memory") == str(mem):
        iters = int(cached)
    else:
        t0 = time.perf_counter()
        kdbx.argon2_raw(b"x" * 32, b"s" * 32, 1, mem, 2, False)
        one = max(time.perf_counter() - t0, 1e-4)
        iters = max(kdbx.MIN_ITERATIONS, min(20, round(config.KDF_TARGET_SECONDS / one)))
        db.set_setting(conn, "argon2_iterations", str(iters))
        db.set_setting(conn, "argon2_memory", str(mem))
        db.set_setting(conn, "argon2_seconds_per_iteration", f"{one:.3f}")
    return {"memory": mem, "iterations": iters, "parallelism": 2}


def kdf_for(conn, mode: str) -> dict:
    return kdbx.kdf_params(strong_kdf(conn) if mode == "chosen" else kdbx.LIGHT_KDF)


# ---------------------------------------------------------------------------
# Rows and permissions
# ---------------------------------------------------------------------------

def vault_row(conn, vault_id: str):
    return conn.execute("SELECT * FROM vaults WHERE id = ?", (vault_id,)).fetchone()


def role_of(conn, vault_id: str, user_id: str) -> str | None:
    r = conn.execute("SELECT role FROM vault_members WHERE vault_id = ? AND user_id = ?", (vault_id, user_id)).fetchone()
    return r["role"] if r else None


def personal_of(conn, user_id: str):
    return conn.execute("SELECT * FROM vaults WHERE kind = 'personal' AND owner_user_id = ?", (user_id,)).fetchone()


def household(conn):
    return conn.execute("SELECT * FROM vaults WHERE kind = 'household'").fetchone()


def user_row(conn, user_id: str):
    return conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()


def add_member(conn, vault_id: str, user_id: str, role: str, by: str | None) -> None:
    conn.execute("INSERT INTO vault_members (vault_id, user_id, role, added_by, added_at) VALUES (?, ?, ?, ?, ?) "
                 "ON CONFLICT(vault_id, user_id) DO UPDATE SET role = excluded.role",
                 (vault_id, user_id, role, by, config.now_iso()))


def remember(conn, vault_id: str, user_id: str, password: str, epoch: int) -> None:
    u = user_row(conn, user_id)
    if not u or not u["public_key"]:
        return
    conn.execute("INSERT INTO vault_keys (vault_id, user_id, sealed, key_epoch, created_at) VALUES (?, ?, ?, ?, ?) "
                 "ON CONFLICT(vault_id, user_id) DO UPDATE SET sealed = excluded.sealed, key_epoch = excluded.key_epoch, "
                 "created_at = excluded.created_at",
                 (vault_id, user_id, keyring.seal(u["public_key"], password, vault_id), epoch, config.now_iso()))
    from . import copies
    copies.access_changed(conn, user_id)


def forget(conn, vault_id: str, user_id: str) -> None:
    conn.execute("DELETE FROM vault_keys WHERE vault_id = ? AND user_id = ?", (vault_id, user_id))
    from . import copies
    copies.access_changed(conn, user_id)


# ---------------------------------------------------------------------------
# Opening and saving
# ---------------------------------------------------------------------------

def open_vault(conn, vault_id: str, password: str) -> sessions.OpenVault:
    """The open vault (shared), opening the file if needed. Raises kdbx.WrongKey."""
    ov = sessions.get_open(vault_id)
    if ov is not None:
        if not hmac.compare_digest(ov.password.encode(), password.encode()):
            raise kdbx.WrongKey("Wrong password.")
        return ov
    row = vault_row(conn, vault_id)
    data = storage.read_current(conn, vault_id)
    database = kdbx.open_bytes(data, password)
    ov = sessions.put_open(sessions.OpenVault(vault_id, database, password, row["key_epoch"]))
    from . import items, reminders
    with ov.lock:
        reminders.refresh(conn, vault_id, ov.db)
        upgraded = items.sync_keepass_otp(ov.db)
    if upgraded:
        # older vault files get KeePass's own TOTP fields once, so KeePass 2.47+ shows the codes too
        try:
            save(conn, ov, None)
            logger.info("Added KeePass TOTP fields to %s item(s) in a vault.", upgraded)
        except Exception:
            logger.exception("Couldn't save KeePass TOTP fields; they're added on the next save")
    return ov


def save(conn, ov: sessions.OpenVault, user_id: str | None, new_password: str | None = None,
         kdf: dict | None = None) -> int:
    """Write the open vault as a new version. With `new_password`: a password change or re-key —
    new KDF salt, older versions purged, key_epoch + 1 (SPEC §5.4)."""
    from . import items
    with ov.lock:
        items.sync_keepass_otp(ov.db)          # every TOTP is also in KeePass's own fields
        if new_password is not None:
            data = kdbx.save_bytes(ov.db, new_password, kdf=kdf, new_salt=True)
            epoch = ov.key_epoch + 1
            conn.execute("UPDATE vaults SET key_epoch = ? WHERE id = ?", (epoch, ov.vault_id))
            version = storage.write_version(conn, ov.vault_id, data, user_id, epoch)
            storage.purge_older(conn, ov.vault_id)
            ov.password = new_password
            ov.key_epoch = epoch
        else:
            data = kdbx.save_bytes(ov.db, ov.password)
            version = storage.write_version(conn, ov.vault_id, data, user_id, ov.key_epoch)
        ov.changed()
        from . import guest_wifi, reminders
        reminders.refresh(conn, ov.vault_id, ov.db)
        guest_wifi.after_save(conn, ov.vault_id, ov.db)
    from . import copies, emergency
    emergency.after_save(conn, ov.vault_id, user_id)
    copies.vault_changed(conn, ov.vault_id)
    return version


def create_vault_file(conn, vault_id: str, name: str, password: str, mode: str, user_id: str | None,
                      database: "kdbx.Database | None" = None) -> kdbx.Database:
    d = database or kdbx.Database.create(name)
    d.kdf = kdf_for(conn, mode)
    d.transformed = None
    data = kdbx.save_bytes(d, password, new_salt=True)
    storage.write_version(conn, vault_id, data, user_id, 1)
    return d


# ---------------------------------------------------------------------------
# Admin setup, first unlock, reset
# ---------------------------------------------------------------------------

def admin_setup(conn, user_id: str, admin_id: str) -> str:
    """Enable and set up: a one-time passphrase and an empty Personal vault. Returns the passphrase (once)."""
    u = user_row(conn, user_id)
    if u is None:
        raise HTTPException(404, "That person isn't known to Household Vault.")
    if personal_of(conn, user_id):
        raise HTTPException(409, "They're already set up. Use Reset first if they forgot their password.")
    one_time = passwords.passphrase(5)
    vid = storage.new_vault_row(conn, "personal", "chosen", "Personal", user_id)
    add_member(conn, vid, user_id, "owner", admin_id)
    create_vault_file(conn, vid, f"{u['name']} — Personal", one_time, "chosen", admin_id)
    conn.execute("UPDATE users SET disabled = 0, status = 'temporary', public_key = NULL, private_key_wrapped = NULL, "
                 "unlock_failures = 0, unlock_blocked_until = NULL WHERE id = ?", (user_id,))
    db.audit(conn, "user_setup", user_id, vid, admin_id)
    return one_time


def check_blocked(u) -> None:
    until = config.parse_iso(u["unlock_blocked_until"])
    if until and config.utcnow() < until:
        secs = int((until - config.utcnow()).total_seconds()) + 1
        raise HTTPException(429, f"Too many wrong passwords. Try again in {max(1, round(secs / 60))} minute(s).")


def wrong_password(conn, user_id: str) -> None:
    u = user_row(conn, user_id)
    fails = u["unlock_failures"] + 1
    until = None
    if fails >= 5:
        wait = min(60 * (2 ** (fails - 5)), 3600)
        until = (config.utcnow() + timedelta(seconds=wait)).strftime("%Y-%m-%dT%H:%M:%S.000Z")
        db.audit(conn, "unlock_blocked", user_id)
    conn.execute("UPDATE users SET unlock_failures = ?, unlock_blocked_until = ? WHERE id = ?", (fails, until, user_id))
    if fails >= 3:
        alerts.send(conn, [user_id], f"Someone typed a wrong password {fails} times in a row in your Household Vault"
                    + (" — it's now blocked for a while" if until else "")
                    + ". If it wasn't you, change your Home Assistant password.",
                    throttle_key=f"wrong:{user_id}", throttle_seconds=900)


def unlock(conn, user: dict, password: str) -> tuple:
    """(session, report). Opens Personal and every remembered vault."""
    u = user_row(conn, user["id"])
    if u["status"] == "none":
        raise HTTPException(409, "An admin hasn't set up your vault yet.")
    check_blocked(u)
    personal = personal_of(conn, user["id"])
    try:
        ov = open_vault(conn, personal["id"], password)
    except kdbx.WrongKey:
        wrong_password(conn, user["id"])
        conn.commit()
        raise HTTPException(403, "Wrong master password.")
    conn.execute("UPDATE users SET unlock_failures = 0, unlock_blocked_until = NULL, previous_unlock = last_unlock, "
                 "last_unlock = ? WHERE id = ?", (config.now_iso(), user["id"]))
    if u["status"] == "temporary":
        s = sessions.new_session(user["id"], None, must_change=True)
        sessions.attach(s, ov)
        return s, {"mustChangePassword": True, "failed": []}
    private = keyring.unwrap_private(u["private_key_wrapped"], ov.db.transformed, user["id"])
    s = sessions.new_session(user["id"], private)
    sessions.attach(s, ov)
    from . import emergency
    emergency.release_due(conn)
    failed = open_remembered(conn, s)
    emergency.sync(conn, user["id"], user["id"])
    from . import copies
    if copies.enabled(conn):
        r = conn.execute("SELECT file, stale FROM user_copies WHERE user_id = ?", (user["id"],)).fetchone()
        if r is None or r["stale"] or not os.path.exists(os.path.join(copies.copy_dir(), r["file"])):
            copies.schedule([user["id"]])          # changes made while they were away (SPEC §12.10)
    return s, {"mustChangePassword": False, "failed": failed}


def open_remembered(conn, s: sessions.Session) -> list:
    """Open every vault whose password is in the person's key ring. Returns names that didn't open."""
    failed = []
    rows = conn.execute("SELECT k.*, v.name, v.kind, v.password_mode, v.rekey_pending FROM vault_keys k "
                        "JOIN vaults v ON v.id = k.vault_id JOIN vault_members m ON m.vault_id = k.vault_id "
                        "AND m.user_id = k.user_id WHERE k.user_id = ?", (s.user_id,)).fetchall()
    for r in rows:
        try:
            pw = keyring.unseal(s.private_key, r["sealed"], r["vault_id"])
            ov = open_vault(conn, r["vault_id"], pw)
        except Exception:                     # stale key (password changed) — forget it
            forget(conn, r["vault_id"], s.user_id)
            failed.append(r["name"])
            continue
        sessions.attach(s, ov)
        if r["kind"] == "household":
            household_sweep(conn, ov, s.user_id)
        if r["rekey_pending"] and r["password_mode"] == "random":
            rekey(conn, ov, s.user_id)
    return failed


def activate(conn, s: sessions.Session, user: dict, new_password: str) -> None:
    """First unlock: the person's own master password, a fresh keypair, older versions purged."""
    u = user_row(conn, user["id"])
    if u["status"] != "temporary" or not s.must_change:
        raise HTTPException(409, "You've already chosen your own master password.")
    personal = personal_of(conn, user["id"])
    ov = sessions.get_open(personal["id"])
    msg = passwords.check_master(new_password, settings.get("min_master_password_length"))
    if msg:
        raise HTTPException(422, msg)
    if hmac.compare_digest(new_password.encode(), ov.password.encode()):
        raise HTTPException(422, "Choose a new password — not the one-time password from your admin.")
    save(conn, ov, user["id"], new_password=new_password, kdf=kdf_for(conn, "chosen"))
    private, public = keyring.new_keypair()
    conn.execute("UPDATE users SET status = 'active', public_key = ?, private_key_wrapped = ? WHERE id = ?",
                 (public, keyring.wrap_private(private, ov.db.transformed, user["id"]), user["id"]))
    s.private_key = private
    s.must_change = False
    db.audit(conn, "user_activated", user["id"], personal["id"])
    ensure_household(conn, s)


def change_master(conn, s: sessions.Session, user: dict, current: str, new: str) -> None:
    personal = personal_of(conn, user["id"])
    ov = sessions.get_open(personal["id"])
    if ov is None or not hmac.compare_digest(current.encode(), ov.password.encode()):
        raise HTTPException(403, "Your current master password isn't right.")
    msg = passwords.check_master(new, settings.get("min_master_password_length"))
    if msg:
        raise HTTPException(422, msg)
    save(conn, ov, user["id"], new_password=new, kdf=kdf_for(conn, "chosen"))
    conn.execute("UPDATE users SET private_key_wrapped = ?, master_epoch = master_epoch + 1 WHERE id = ?",
                 (keyring.wrap_private(s.private_key, ov.db.transformed, user["id"]), user["id"]))
    conn.execute("DELETE FROM quick_unlock WHERE user_id = ?", (user["id"],))     # they hold the old password
    # people it's shared with (same password) must ask for the new one
    for r in conn.execute("SELECT user_id FROM vault_members WHERE vault_id = ? AND user_id != ?",
                          (personal["id"], user["id"])).fetchall():
        forget(conn, personal["id"], r["user_id"])
        db.audit(conn, "password_changed", r["user_id"], personal["id"], user["id"])
    sessions.detach_all_but(personal["id"], user["id"])
    sessions.end_user(user["id"], except_token=s.token)
    db.audit(conn, "password_changed", user["id"], personal["id"])
    alerts.send(conn, [user["id"]], "Your Household Vault master password was just changed. "
                "If you didn't do this, tell an admin straight away.")


def _leave_all_but_personal(conn, user_id: str, admin_id: str) -> None:
    """Take the person out of every vault except their own Personal (random-password ones get re-keyed)."""
    for r in conn.execute("SELECT vault_id FROM vault_members WHERE user_id = ?", (user_id,)).fetchall():
        v = vault_row(conn, r["vault_id"])
        if v["kind"] in ("personal", "emergency") and v["owner_user_id"] == user_id:
            continue
        remove_member(conn, r["vault_id"], user_id, admin_id, audit_action=None)


def reset_user(conn, user_id: str, admin_id: str) -> None:
    """Forgotten master password: wipe Personal and the key ring; leave every shared vault."""
    from . import emergency
    sessions.end_user(user_id)
    _leave_all_but_personal(conn, user_id, admin_id)
    emergency.forget_person(conn, user_id, True, admin_id)
    personal = personal_of(conn, user_id)
    if personal:
        sessions.close(personal["id"])
        conn.execute("DELETE FROM vaults WHERE id = ?", (personal["id"],))
        storage.delete_vault_files(personal["id"])
    conn.execute("DELETE FROM vault_keys WHERE user_id = ?", (user_id,))
    conn.execute("DELETE FROM recent_items WHERE user_id = ?", (user_id,))
    conn.execute("DELETE FROM quick_unlock WHERE user_id = ?", (user_id,))
    from . import copies
    copies.delete(conn, user_id)
    conn.execute("UPDATE users SET status = 'none', public_key = NULL, private_key_wrapped = NULL, unlock_failures = 0, "
                 "unlock_blocked_until = NULL WHERE id = ?", (user_id,))
    db.audit(conn, "user_reset", user_id, None, admin_id)
    alerts.send(conn, [user_id], "An admin reset your Household Vault: your Personal vault was wiped. "
                "Ask them to set you up again.", kind="admin")


def set_disabled(conn, user_id: str, disabled: bool, admin_id: str) -> None:
    u = user_row(conn, user_id)
    if u is None:
        raise HTTPException(404, "That person isn't known to Household Vault.")
    if disabled:
        if u["disabled"]:
            return
        from . import copies, emergency
        sessions.end_user(user_id)
        _leave_all_but_personal(conn, user_id, admin_id)
        emergency.forget_person(conn, user_id, False, admin_id)
        copies.delete(conn, user_id)
        conn.execute("UPDATE users SET disabled = 1 WHERE id = ?", (user_id,))
        db.audit(conn, "user_disabled", user_id, None, admin_id)
        alerts.send(conn, [user_id], "An admin turned off your access to Household Vault.", kind="admin")
    else:
        if not u["disabled"]:
            return
        conn.execute("UPDATE users SET disabled = 0 WHERE id = ?", (user_id,))
        db.audit(conn, "user_enabled", user_id, None, admin_id)
        h = household(conn)
        if h and u["status"] == "active":
            ov = sessions.get_open(h["id"])
            if ov is not None:
                household_sweep(conn, ov, admin_id)


# ---------------------------------------------------------------------------
# Household
# ---------------------------------------------------------------------------

def ensure_household(conn, s: sessions.Session) -> None:
    """Create Household when the first person becomes active; otherwise join it if it's open."""
    h = household(conn)
    if h is None:
        pw = passwords.random_vault_password()
        vid = storage.new_vault_row(conn, "household", "random", "Household", None)
        database = create_vault_file(conn, vid, "Household", pw, "random", s.user_id)
        ov = sessions.put_open(sessions.OpenVault(vid, kdbx.open_bytes(storage.read_current(conn, vid), pw), pw, 1))
        del database
        household_sweep(conn, ov, s.user_id)
        sessions.attach(s, ov)
        db.audit(conn, "created", None, vid, s.user_id)
        return
    ov = sessions.get_open(h["id"])
    if ov is not None:
        household_sweep(conn, ov, s.user_id)
        sessions.attach(s, ov)


def household_sweep(conn, ov: sessions.OpenVault, actor: str | None) -> list:
    """Give every enabled, active person who isn't in Household yet membership and the key."""
    added = []
    rows = conn.execute("SELECT id FROM users WHERE disabled = 0 AND status = 'active' AND public_key IS NOT NULL").fetchall()
    for r in rows:
        member = role_of(conn, ov.vault_id, r["id"])
        has_key = conn.execute("SELECT 1 FROM vault_keys WHERE vault_id = ? AND user_id = ? AND key_epoch = ?",
                               (ov.vault_id, r["id"], ov.key_epoch)).fetchone()
        if member and has_key:
            continue
        if not member:
            add_member(conn, ov.vault_id, r["id"], "editor", actor)
            db.audit(conn, "shared", r["id"], ov.vault_id, actor)
            added.append(r["id"])
        remember(conn, ov.vault_id, r["id"], ov.password, ov.key_epoch)
        for s in sessions.user_sessions(r["id"]):         # already unlocked elsewhere: open it for them now
            if s.private_key is not None:
                sessions.attach(s, ov)
    return added


# ---------------------------------------------------------------------------
# Shared vaults
# ---------------------------------------------------------------------------

def create_shared(conn, s: sessions.Session, name: str, mode: str, password: str | None) -> str:
    n = conn.execute("SELECT COUNT(*) FROM vaults WHERE kind = 'shared'").fetchone()[0]
    if n >= MAX_SHARED_VAULTS:
        raise HTTPException(409, "There are already 50 shared vaults.")
    if mode == "chosen":
        msg = passwords.check_master(password or "", settings.get("min_master_password_length"))
        if msg:
            raise HTTPException(422, msg)
        pw = password
    else:
        pw = passwords.random_vault_password()
    vid = storage.new_vault_row(conn, "shared", mode, name, s.user_id)
    add_member(conn, vid, s.user_id, "owner", s.user_id)
    create_vault_file(conn, vid, name, pw, mode, s.user_id)
    ov = open_vault(conn, vid, pw)
    remember(conn, vid, s.user_id, pw, 1)
    sessions.attach(s, ov)
    db.audit(conn, "created", s.user_id, vid)
    return vid


def share(conn, s: sessions.Session, vault_id: str, user_id: str, role: str) -> None:
    v = vault_row(conn, vault_id)
    if v["kind"] == "household":
        raise HTTPException(409, "Everyone active is in Household automatically.")
    if role not in ("manager", "editor", "viewer"):
        raise HTTPException(422, "Role must be manager, editor or viewer.")
    if v["kind"] == "personal" and role == "manager":
        raise HTTPException(422, "Only you can manage your Personal vault.")
    target = user_row(conn, user_id)
    if not target or target["disabled"] or target["status"] != "active":
        raise HTTPException(409, "They need to be enabled and set up (with their own master password) first.")
    existing = role_of(conn, vault_id, user_id)
    if existing == "owner":
        raise HTTPException(409, "That's the owner.")
    count = conn.execute("SELECT COUNT(*) FROM vault_members WHERE vault_id = ?", (vault_id,)).fetchone()[0]
    if not existing and count >= MAX_MEMBERS:
        raise HTTPException(409, "A vault can have at most 20 people.")
    add_member(conn, vault_id, user_id, role, s.user_id)
    if v["password_mode"] == "random":
        ov = sessions.get_open(vault_id)
        remember(conn, vault_id, user_id, ov.password, ov.key_epoch)
        for us in sessions.user_sessions(user_id):
            if us.private_key is not None:
                sessions.attach(us, ov)
    if not existing:
        db.audit(conn, "shared", user_id, vault_id, s.user_id)


def remove_member(conn, vault_id: str, user_id: str, actor: str | None, audit_action: str | None = "unshared") -> None:
    """Remove someone; random-password vaults are re-keyed (now if open, else on the next member's unlock);
    same-password vaults are flagged so the owner changes the password (SPEC §5.3)."""
    v = vault_row(conn, vault_id)
    if role_of(conn, vault_id, user_id) is None:
        return
    conn.execute("DELETE FROM vault_members WHERE vault_id = ? AND user_id = ?", (vault_id, user_id))
    forget(conn, vault_id, user_id)
    sessions.detach_user(user_id, vault_id)
    if audit_action:
        db.audit(conn, audit_action, user_id, vault_id, actor)
    remaining = conn.execute("SELECT COUNT(*) FROM vault_members WHERE vault_id = ?", (vault_id,)).fetchone()[0]
    if v["kind"] in ("shared", "household") and remaining == 0:
        sessions.close(vault_id)
        conn.execute("DELETE FROM vaults WHERE id = ?", (vault_id,))
        storage.delete_vault_files(vault_id)
        return
    if v["password_mode"] == "random":
        ov = sessions.get_open(vault_id)
        if ov is not None:
            rekey(conn, ov, actor)
        else:
            conn.execute("UPDATE vaults SET rekey_pending = 1 WHERE id = ?", (vault_id,))
    else:
        conn.execute("UPDATE vaults SET password_change_suggested = 1 WHERE id = ?", (vault_id,))


def rekey(conn, ov: sessions.OpenVault, actor: str | None) -> None:
    """New random password, older versions purged, re-sealed to every member (SPEC §5.4)."""
    pw = passwords.random_vault_password()
    save(conn, ov, actor, new_password=pw, kdf=kdbx.kdf_params(kdbx.LIGHT_KDF))
    members = [r["user_id"] for r in conn.execute("SELECT user_id FROM vault_members WHERE vault_id = ?", (ov.vault_id,))]
    conn.execute("DELETE FROM vault_keys WHERE vault_id = ?", (ov.vault_id,))
    for uid in members:
        remember(conn, ov.vault_id, uid, pw, ov.key_epoch)
    conn.execute("UPDATE vaults SET rekey_pending = 0 WHERE id = ?", (ov.vault_id,))
    if vault_row(conn, ov.vault_id)["kind"] == "emergency":
        from . import emergency
        emergency.reseal(conn, ov)
    db.audit(conn, "rekeyed", None, ov.vault_id, actor)


def change_vault_password(conn, s: sessions.Session, vault_id: str, new: str, remember_it: bool = True) -> None:
    """Owner of a same-password vault (not Personal — that's the master password)."""
    v = vault_row(conn, vault_id)
    if v["kind"] == "personal":
        raise HTTPException(409, "Your Personal vault's password is your master password — change it in Settings.")
    if v["password_mode"] != "chosen":
        raise HTTPException(409, "This vault has a random password — use Re-key.")
    msg = passwords.check_master(new, settings.get("min_master_password_length"))
    if msg:
        raise HTTPException(422, msg)
    ov = sessions.get_open(vault_id)
    save(conn, ov, s.user_id, new_password=new, kdf=kdf_for(conn, "chosen"))
    others = [r["user_id"] for r in conn.execute("SELECT user_id FROM vault_members WHERE vault_id = ? AND user_id != ?",
                                                 (vault_id, s.user_id))]
    conn.execute("DELETE FROM vault_keys WHERE vault_id = ?", (vault_id,))
    if remember_it:
        remember(conn, vault_id, s.user_id, new, ov.key_epoch)
    for uid in others:
        db.audit(conn, "password_changed", uid, vault_id, s.user_id)
    conn.execute("UPDATE vaults SET password_change_suggested = 0 WHERE id = ?", (vault_id,))
    sessions.detach_all_but(vault_id, s.user_id)
    db.audit(conn, "password_changed", s.user_id, vault_id)


def open_with_password(conn, s: sessions.Session, vault_id: str, password: str, remember_it: bool) -> None:
    if role_of(conn, vault_id, s.user_id) is None:
        raise HTTPException(404, "That vault doesn't exist.")
    try:
        ov = open_vault(conn, vault_id, password)
    except kdbx.WrongKey:
        wrong_password(conn, s.user_id)
        conn.commit()
        raise HTTPException(403, "That isn't the vault's password.")
    sessions.attach(s, ov)
    if remember_it and s.private_key is not None:
        remember(conn, vault_id, s.user_id, password, ov.key_epoch)


def transfer(conn, s: sessions.Session, vault_id: str, user_id: str) -> None:
    v = vault_row(conn, vault_id)
    if v["kind"] != "shared":
        raise HTTPException(409, "Only shared vaults can change owner.")
    if role_of(conn, vault_id, user_id) is None:
        raise HTTPException(409, "Share the vault with them first.")
    conn.execute("UPDATE vault_members SET role = 'manager' WHERE vault_id = ? AND user_id = ?", (vault_id, s.user_id))
    conn.execute("UPDATE vault_members SET role = 'owner' WHERE vault_id = ? AND user_id = ?", (vault_id, user_id))
    conn.execute("UPDATE vaults SET owner_user_id = ? WHERE id = ?", (user_id, vault_id))
    db.audit(conn, "transferred", user_id, vault_id, s.user_id)
