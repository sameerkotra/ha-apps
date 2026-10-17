"""Vault operations (SPEC §4, §5): setup and unlock, opening and saving vaults,
the key ring, Household, sharing, re-keying and password changes.

Routers call these with an open DB connection; file writes happen inside the
caller's transaction (storage.write_version), so a failure leaves the DB as
it was and the previous file version intact.

Every change to an open vault happens inside `editing(conn, ov)`: the vault's
lock is taken **before** the transaction writes anything and held until it has
committed, and if anything fails the in-memory vault is read back from its
file — so a half-made change can't stay in memory for the next save to write
(review 2026-10).
"""
import os
import logging
import sqlite3
import threading
import time
from contextlib import contextmanager
from datetime import timedelta

from cryptography.exceptions import InvalidTag
from fastapi import HTTPException

from . import alerts, config, db, kdbx, keyring, passwords, sessions, settings, storage

logger = logging.getLogger("service")
ROLES = ("owner", "manager", "editor", "viewer")
EDIT_ROLES = ("owner", "manager", "editor")
MANAGE_ROLES = ("owner", "manager")
MAX_SHARED_VAULTS = 50
MAX_MEMBERS = 20
# A one-time password (an admin set someone up, they haven't unlocked yet) is good for this long: the temporary
# vault file sits in /data (and in Home Assistant's backups) meanwhile, and the admin knows its password
# (security review 2026-10, #4). After that the person is set up again.
SETUP_MAX_HOURS = 48
ONE_TIME_WORDS = 6                      # ~63 bits from the app's own list (five words were ~52)


# ---------------------------------------------------------------------------
# KDF settings
# ---------------------------------------------------------------------------

_tuned: dict = {}                   # memory (bytes) → (iterations, seconds per iteration), measured on this machine
_tune_lock = threading.Lock()


def _measure(mem: int) -> tuple:
    t0 = time.perf_counter()
    kdbx.argon2_raw(b"x" * 32, b"s" * 32, 1, mem, 2, False)
    one = max(time.perf_counter() - t0, 1e-4)
    return max(kdbx.MIN_ITERATIONS, min(20, round(config.KDF_TARGET_SECONDS / one))), one


def _store_tuning(conn, mem: int, iters: int, one: float) -> None:
    db.set_setting(conn, "argon2_iterations", str(iters))
    db.set_setting(conn, "argon2_memory", str(mem))
    db.set_setting(conn, "argon2_seconds_per_iteration", f"{one:.3f}")


def calibrate(force: bool = False) -> int:
    """Tune the Argon2 iterations for this machine — at start-up and after a restore (the backup may come from
    another machine), with no database transaction open while it runs (review 2026-10: it used to run inside
    the first admin's write)."""
    mem = config.KDF_MEMORY_KIB * 1024
    with _tune_lock:
        if not force:
            with db.get_conn() as conn:
                cached = db.get_setting(conn, "argon2_iterations")
                same = db.get_setting(conn, "argon2_memory") == str(mem)
                one = db.get_setting(conn, "argon2_seconds_per_iteration")
            if cached and same:
                _tuned[mem] = (int(cached), float(one or 0))
                return int(cached)
        iters, one = _measure(mem)
        _tuned[mem] = (iters, one)
    with db.get_conn() as conn:
        _store_tuning(conn, mem, iters, one)
    return iters


def strong_kdf(conn) -> dict:
    """Argon2id for person-chosen passwords: memory from config, iterations tuned once on
    this machine to take about KDF_TARGET_SECONDS, never fewer than 3 (SPEC §5.1)."""
    mem = config.KDF_MEMORY_KIB * 1024
    if mem in _tuned:
        iters = _tuned[mem][0]
    else:
        cached = db.get_setting(conn, "argon2_iterations")
        if cached and db.get_setting(conn, "argon2_memory") == str(mem):
            iters = int(cached)
        else:                                           # not tuned yet (start-up still running): measure now
            iters, one = _measure(mem)
            _store_tuning(conn, mem, iters, one)
        _tuned[mem] = (iters, 0.0)
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

def _snapshot(ov: sessions.OpenVault) -> tuple:
    d = ov.db
    return (ov.password, ov.composite, ov.key_epoch,
            dict(d.kdf) if d is not None and d.kdf else None, d.transformed if d is not None else None)


def reload(conn, ov: sessions.OpenVault, snap: tuple) -> None:
    """A change failed: put the open vault back as its file is — the version `conn` sees (after a rollback, the
    committed one), opened with the key it had before the change (no KDF run: the cached key is reused). If that
    can't be done the vault is closed, and members open it again from the file."""
    ov.password, ov.composite, ov.key_epoch = snap[0], snap[1], snap[2]
    ov.changed()
    if ov.db is None:                                     # closed meanwhile
        return
    try:
        row = conn.execute("SELECT version, key_epoch FROM vaults WHERE id = ?", (ov.vault_id,)).fetchone()
        if row is None or not row["version"]:
            sessions.close(ov.vault_id)
            return
        ov.db = kdbx.open_with_composite(storage.read_version(ov.vault_id, row["version"]), snap[1],
                                         known=(snap[3], snap[4]))
        ov.key_epoch = row["key_epoch"]
    except Exception:
        logger.exception("Couldn't read a vault back after a failed change; it's closed until it's opened again")
        sessions.close(ov.vault_id)


@contextmanager
def editing(conn, *ovs):
    """Change open vaults: their locks are taken in a fixed order (vault id) before the transaction writes
    anything, and held until it has committed. If anything fails — a check after a first change, the save,
    the commit — the transaction is rolled back and each vault is read back from its file.

    Nested uses (a save inside a route's `editing`, the emergency mirror saved after the Personal vault) join
    the outermost one: vaults locked later are held, and put back, with it. `editing(conn)` with no vault starts
    the transaction for a change that creates vaults as it goes."""
    state = conn.vault_edit
    outer = state is None
    if outer:
        if conn.in_transaction:
            conn.commit()                 # never wait for a vault's lock while holding the database's write lock
        state = conn.vault_edit = {"held": []}
    try:
        for ov in sorted((o for o in ovs if o is not None), key=lambda o: o.vault_id):
            if any(o is ov for o, _ in state["held"]):
                continue
            ov.lock.acquire()
            state["held"].append((ov, _snapshot(ov)))
            if ov.db is None:                       # closed (everyone locked it) since it was looked up
                raise HTTPException(423, "That vault is locked — open it with its password first.")
        if outer:
            conn.execute("BEGIN IMMEDIATE")
        yield
        if outer:
            conn.commit()
    except Exception:
        if outer:
            try:
                conn.rollback()
            except sqlite3.Error:
                logger.exception("Rolling back a failed vault change failed")
            for ov, snap in state["held"]:
                reload(conn, ov, snap)
        raise
    finally:
        if outer:
            conn.vault_edit = None
            for ov, _ in reversed(state["held"]):
                ov.lock.release()


def open_vault(conn, vault_id: str, password: str) -> sessions.OpenVault:
    """The open vault (shared), opening the file if needed. Raises kdbx.WrongKey."""
    ov = sessions.get_open(vault_id)
    if ov is not None:
        if not ov.matches(password):
            raise kdbx.WrongKey("Wrong password.")
        return ov
    row = vault_row(conn, vault_id)
    data = storage.read_current(conn, vault_id)
    database = kdbx.open_bytes(data, password)
    ov = sessions.put_open(sessions.OpenVault(vault_id, database, password, row["key_epoch"],
                                              keep_password=row["password_mode"] == "random"))
    if ov.db is not database:                       # someone opened it at the same moment
        if not ov.matches(password):
            raise kdbx.WrongKey("Wrong password.")
        return ov
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
         kdf: dict | None = None, keep_history: bool = False) -> int:
    """Write the open vault as a new version. With `new_password`: a password change or re-key —
    new KDF salt, key_epoch + 1, and the older versions purged (SPEC §5.4) or, with `keep_history` (a re-key),
    re-encrypted with the new password so they stay restorable. The files of purged versions go only once the
    transaction commits; if anything here fails, the save's own writes are undone (a savepoint) and the open
    vault is read back from its file."""
    from . import copies, emergency, guest_wifi, items, reminders
    with editing(conn, ov):
        snap = _snapshot(ov)
        conn.execute("SAVEPOINT vault_save")
        try:
            items.sync_keepass_otp(ov.db)          # every TOTP is also in KeePass's own fields
            if new_password is not None:
                old_composite = ov.composite
                data = kdbx.save_bytes(ov.db, new_password, kdf=kdf, new_salt=True)
                epoch = ov.key_epoch + 1
                conn.execute("UPDATE vaults SET key_epoch = ? WHERE id = ?", (epoch, ov.vault_id))
                version = storage.write_version(conn, ov.vault_id, data, user_id, epoch)
                if keep_history:
                    _reencrypt_history(conn, ov, old_composite, (snap[3], snap[4]), epoch)
                else:
                    storage.purge_older(conn, ov.vault_id)
                ov.set_password(new_password)
                ov.key_epoch = epoch
            else:
                data = kdbx.save_bytes(ov.db, composite=ov.composite)
                version = storage.write_version(conn, ov.vault_id, data, user_id, ov.key_epoch)
            ov.changed()
            reminders.refresh(conn, ov.vault_id, ov.db)
            guest_wifi.after_save(conn, ov.vault_id, ov.db)
            emergency.after_save(conn, ov.vault_id, user_id)
            copies.vault_changed(conn, ov.vault_id)
            conn.execute("RELEASE vault_save")
        except Exception:
            try:
                conn.execute("ROLLBACK TO vault_save")
                conn.execute("RELEASE vault_save")
            except sqlite3.Error:
                pass
            reload(conn, ov, snap)
            raise
    return version


def _reencrypt_history(conn, ov: sessions.OpenVault, old_composite: bytes, old_known: tuple, epoch: int) -> None:
    """A re-key keeps the older versions: each is opened with the old key (cached, no KDF run) and written again
    with the new one (its KDF output is the current file's, so no KDF run either)."""
    kdf, transformed, composite = dict(ov.db.kdf), ov.db.transformed, ov.db.composite
    for v in storage.older_versions(conn, ov.vault_id):
        try:
            d = kdbx.open_with_composite(storage.read_version(ov.vault_id, v), old_composite, known=old_known)
        except (kdbx.KdbxError, OSError):
            logger.warning("An older version of a vault couldn't be opened while re-keying; it's dropped")
            storage._delete_versions(conn, ov.vault_id, [v])
            continue
        d.kdf, d.transformed, d.composite = dict(kdf), transformed, composite
        storage.rewrite_version(conn, ov.vault_id, v, kdbx.save_bytes(d, composite=composite), epoch)


def create_vault_file(conn, vault_id: str, name: str, password: str, mode: str, user_id: str | None,
                      database: "kdbx.Database | None" = None) -> kdbx.Database:
    d = database or kdbx.Database.create(name)
    d.kdf = kdf_for(conn, mode)
    d.transformed = None
    data = kdbx.save_bytes(d, password, new_salt=True)
    storage.forget_on_rollback(conn, vault_id)          # no folder is left behind by a create that fails
    conn.on_rollback(lambda c: sessions.close(vault_id))
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
    one_time = passwords.passphrase(ONE_TIME_WORDS)
    vid = storage.new_vault_row(conn, "personal", "chosen", "Personal", user_id)
    add_member(conn, vid, user_id, "owner", admin_id)
    create_vault_file(conn, vid, f"{u['name']} — Personal", one_time, "chosen", admin_id)
    conn.execute("UPDATE users SET disabled = 0, status = 'temporary', public_key = NULL, private_key_wrapped = NULL, "
                 "unlock_failures = 0, unlock_blocked_until = NULL, setup_at = ? WHERE id = ?", (config.now_iso(), user_id))
    db.audit(conn, "user_setup", user_id, vid, admin_id)
    return one_time


def expire_setups(conn, max_hours: int = SETUP_MAX_HOURS) -> int:
    """Housekeeping: a one-time password nobody used within `max_hours` is void — the temporary Personal vault is
    deleted and the person is "not set up" again, so an admin sets them up afresh (a new one-time password).
    Returns how many were expired."""
    # set up before this column existed: dated from the audit log, else from now (a full period of grace)
    for r in conn.execute("SELECT id FROM users WHERE status = 'temporary' AND setup_at IS NULL").fetchall():
        when = conn.execute("SELECT MAX(created_at) FROM audit_log WHERE action = 'user_setup' AND user_id = ?",
                            (r["id"],)).fetchone()[0]
        conn.execute("UPDATE users SET setup_at = ? WHERE id = ?", (when or config.now_iso(), r["id"]))
    cutoff = (config.utcnow() - timedelta(hours=max_hours)).strftime("%Y-%m-%dT%H:%M:%S.000Z")
    rows = conn.execute("SELECT id FROM users WHERE status = 'temporary' AND setup_at < ?", (cutoff,)).fetchall()
    for r in rows:
        sessions.end_user(r["id"])
        personal = personal_of(conn, r["id"])
        if personal:
            sessions.close(personal["id"])
            conn.execute("DELETE FROM vaults WHERE id = ?", (personal["id"],))
            storage.delete_vault_files(conn, personal["id"])
        conn.execute("DELETE FROM vault_keys WHERE user_id = ?", (r["id"],))
        conn.execute("UPDATE users SET status = 'none', public_key = NULL, private_key_wrapped = NULL, unlock_failures = 0, "
                     "unlock_blocked_until = NULL, setup_at = NULL WHERE id = ?", (r["id"],))
        db.audit(conn, "setup_expired", r["id"], None, None)
        alerts.send(conn, [r["id"]], f"Your one-time password for Household Vault wasn't used within {max_hours} hours, "
                    "so it no longer works. Ask an admin to set you up again.", kind="admin")
    return len(rows)


def check_blocked(u) -> None:
    until = config.parse_iso(u["unlock_blocked_until"])
    if until and config.utcnow() < until:
        secs = int((until - config.utcnow()).total_seconds()) + 1
        raise HTTPException(429, f"Too many wrong passwords. Try again in {max(1, round(secs / 60))} minute(s).")


def _block_seconds(fails: int) -> int:
    return min(60 * (2 ** (fails - 5)), 3600)


def reserve_attempt(conn, user_id: str) -> int:
    """Count a password attempt as wrong before checking it, in one atomic UPDATE committed at once: attempts sent
    in parallel each take their own count, and none gets past a block (the check and the count are one step).
    From the fifth the block is set now, for the case the attempt is wrong. Returns the count with this attempt;
    `attempt_ok` gives it back when the password is right, `wrong_password` finishes a wrong one. 429 when blocked."""
    now = config.utcnow()
    row = conn.execute("UPDATE users SET unlock_failures = unlock_failures + 1 WHERE id = ? AND "
                       "(unlock_blocked_until IS NULL OR unlock_blocked_until <= ?) RETURNING unlock_failures",
                       (user_id, config.now_iso())).fetchone()
    if row is None:
        u = user_row(conn, user_id)
        check_blocked(u)
        raise HTTPException(429, "Too many wrong passwords. Try again in a minute.")
    fails = row[0]
    if fails >= 5:
        until = (now + timedelta(seconds=_block_seconds(fails))).strftime("%Y-%m-%dT%H:%M:%S.000Z")
        conn.execute("UPDATE users SET unlock_blocked_until = ? WHERE id = ?", (until, user_id))
    conn.commit()
    return fails


def attempt_ok(conn, user_id: str) -> None:
    """The reserved attempt was right: give its count back (and its block, unless earlier wrong ones keep it)."""
    conn.execute("UPDATE users SET unlock_failures = MAX(unlock_failures - 1, 0), unlock_blocked_until = "
                 "CASE WHEN unlock_failures - 1 >= 5 THEN unlock_blocked_until ELSE NULL END WHERE id = ?", (user_id,))
    conn.commit()


def wrong_password(conn, user_id: str, fails: int) -> None:
    """The reserved attempt was wrong (already counted): note the block and tell the person."""
    if fails >= 5:
        db.audit(conn, "unlock_blocked", user_id)
    if fails >= 3:
        alerts.send(conn, [user_id], f"Someone typed a wrong password {fails} times in a row in your Household Vault"
                    + (" — it's now blocked for a while" if fails >= 5 else "")
                    + ". If it wasn't you, change your Home Assistant password.",
                    throttle_key=f"wrong:{user_id}", throttle_seconds=900)
    conn.commit()


def check_master_password(conn, user_id: str, ov, password: str, message: str) -> None:
    """A check of the master password against the open Personal vault (download all, change it, quick unlock):
    counted and blocked like unlocking, so an unlocked tab can't be used to guess it. 403 with `message`."""
    fails = reserve_attempt(conn, user_id)
    if ov is None or not ov.matches(password):
        wrong_password(conn, user_id, fails)
        raise HTTPException(403, message)
    attempt_ok(conn, user_id)


def unlock(conn, user: dict, password: str) -> tuple:
    """(session, report). Opens Personal and every remembered vault."""
    u = user_row(conn, user["id"])
    if u["status"] == "none":
        raise HTTPException(409, "An admin hasn't set up your vault yet.")
    personal = personal_of(conn, user["id"])
    fails = reserve_attempt(conn, user["id"])
    try:
        ov = open_vault(conn, personal["id"], password)
    except kdbx.WrongKey:
        wrong_password(conn, user["id"], fails)
        raise HTTPException(403, "Wrong master password.")
    except Exception:
        conn.rollback()
        attempt_ok(conn, user["id"])
        raise
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
        except (kdbx.WrongKey, InvalidTag, ValueError, KeyError):
            # a stale key (the password changed) or a sealed box that isn't for this key: forget it
            forget(conn, r["vault_id"], s.user_id)
            failed.append(r["name"])
            continue
        except Exception:
            # anything else (a file that can't be read right now, …) says nothing about the key: keep it, so the
            # vault opens again once that's fixed (review 2026-10: it used to be forgotten for good)
            logger.exception("A remembered vault couldn't be opened; its key is kept")
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
    if ov.matches(new_password):
        raise HTTPException(422, "Choose a new password — not the one-time password from your admin.")
    kdf = kdf_for(conn, "chosen")
    private, public = keyring.new_keypair()
    with editing(conn, ov):              # the new file and the key ring wrapped with its key commit together
        save(conn, ov, user["id"], new_password=new_password, kdf=kdf)
        conn.execute("UPDATE users SET status = 'active', public_key = ?, private_key_wrapped = ? WHERE id = ?",
                     (public, keyring.wrap_private(private, ov.db.transformed, user["id"]), user["id"]))
        db.audit(conn, "user_activated", user["id"], personal["id"])
    s.private_key = private
    s.must_change = False
    ensure_household(conn, s)


def change_master(conn, s: sessions.Session, user: dict, current: str, new: str) -> None:
    personal = personal_of(conn, user["id"])
    ov = sessions.get_open(personal["id"])
    check_master_password(conn, user["id"], ov, current, "Your current master password isn't right.")
    msg = passwords.check_master(new, settings.get("min_master_password_length"))
    if msg:
        raise HTTPException(422, msg)
    kdf = kdf_for(conn, "chosen")
    # the new file and the private key wrapped with its key commit together, or neither does (review 2026-10)
    with editing(conn, ov):
        save(conn, ov, user["id"], new_password=new, kdf=kdf)
        conn.execute("UPDATE users SET private_key_wrapped = ?, master_epoch = master_epoch + 1 WHERE id = ?",
                     (keyring.wrap_private(s.private_key, ov.db.transformed, user["id"]), user["id"]))
        conn.execute("DELETE FROM quick_unlock WHERE user_id = ?", (user["id"],))     # they hold the old password
        # people it's shared with (same password) must ask for the new one
        for r in conn.execute("SELECT user_id FROM vault_members WHERE vault_id = ? AND user_id != ?",
                              (personal["id"], user["id"])).fetchall():
            forget(conn, personal["id"], r["user_id"])
            db.audit(conn, "password_changed", r["user_id"], personal["id"], user["id"])
        db.audit(conn, "password_changed", user["id"], personal["id"])
    sessions.detach_all_but(personal["id"], user["id"])
    sessions.end_user(user["id"], except_token=s.token)
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
        storage.delete_vault_files(conn, personal["id"])
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
    if v["kind"] == "personal" and not role_of(conn, vault_id, user_id):
        # security review 2026-10: a Personal vault opens with its owner's master password, so sharing it
        # meant giving that password away. People added before this stay (their role can still change).
        raise HTTPException(409, "Personal vaults can't be shared — create a shared vault and move the entries into it.")
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


def _promote_owner(conn, vault_id: str) -> str | None:
    """A shared vault whose owner leaves (an admin reset or turned them off) gets a new owner: the longest-standing
    manager, else the longest-standing member (review 2026-10: it used to be left without one)."""
    r = conn.execute("SELECT user_id FROM vault_members WHERE vault_id = ? ORDER BY CASE role WHEN 'manager' THEN 0 "
                     "WHEN 'editor' THEN 1 ELSE 2 END, added_at, user_id LIMIT 1", (vault_id,)).fetchone()
    if r is None:
        return None
    conn.execute("UPDATE vault_members SET role = 'owner' WHERE vault_id = ? AND user_id = ?", (vault_id, r["user_id"]))
    conn.execute("UPDATE vaults SET owner_user_id = ? WHERE id = ?", (r["user_id"], vault_id))
    db.audit(conn, "transferred", r["user_id"], vault_id, None)
    return r["user_id"]


def remove_member(conn, vault_id: str, user_id: str, actor: str | None, audit_action: str | None = "unshared") -> None:
    """Remove someone; random-password vaults are re-keyed (now if open, else on the next member's unlock);
    same-password vaults are flagged so the owner changes the password (SPEC §5.3)."""
    with editing(conn, sessions.get_open(vault_id)):
        v = vault_row(conn, vault_id)
        role = role_of(conn, vault_id, user_id)
        if v is None or role is None:
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
            conn.execute("DELETE FROM recent_items WHERE vault_id = ?", (vault_id,))
            storage.delete_vault_files(conn, vault_id)
            return
        if role == "owner" and v["kind"] == "shared":
            _promote_owner(conn, vault_id)
        if v["password_mode"] == "random":
            conn.execute("UPDATE vaults SET rekey_pending = 1 WHERE id = ?", (vault_id,))
            ov = sessions.get_open(vault_id)
            if ov is not None:
                rekey(conn, ov, actor)
        else:
            conn.execute("UPDATE vaults SET password_change_suggested = 1 WHERE id = ?", (vault_id,))


def rekey(conn, ov: sessions.OpenVault, actor: str | None) -> None:
    """New random password, re-sealed to every member (SPEC §5.4). The kept older versions are re-encrypted with
    it, so they can still be restored (review 2026-10: a re-key used to wipe them)."""
    with editing(conn, ov):
        pw = passwords.random_vault_password()
        save(conn, ov, actor, new_password=pw, kdf=kdbx.kdf_params(kdbx.LIGHT_KDF), keep_history=True)
        members = [r["user_id"] for r in conn.execute("SELECT user_id FROM vault_members WHERE vault_id = ?",
                                                      (ov.vault_id,))]
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
    kdf = kdf_for(conn, "chosen")
    with editing(conn, ov):              # the new file and the forgotten keys commit together
        save(conn, ov, s.user_id, new_password=new, kdf=kdf)
        others = [r["user_id"] for r in conn.execute("SELECT user_id FROM vault_members WHERE vault_id = ? AND "
                                                     "user_id != ?", (vault_id, s.user_id))]
        conn.execute("DELETE FROM vault_keys WHERE vault_id = ?", (vault_id,))
        if remember_it:
            remember(conn, vault_id, s.user_id, new, ov.key_epoch)
        for uid in others:
            db.audit(conn, "password_changed", uid, vault_id, s.user_id)
        conn.execute("UPDATE vaults SET password_change_suggested = 0 WHERE id = ?", (vault_id,))
        db.audit(conn, "password_changed", s.user_id, vault_id)
    sessions.detach_all_but(vault_id, s.user_id)


def open_with_password(conn, s: sessions.Session, vault_id: str, password: str, remember_it: bool) -> None:
    if role_of(conn, vault_id, s.user_id) is None:
        raise HTTPException(404, "That vault doesn't exist.")
    fails = reserve_attempt(conn, s.user_id)
    try:
        ov = open_vault(conn, vault_id, password)
    except kdbx.WrongKey:
        wrong_password(conn, s.user_id, fails)
        raise HTTPException(403, "That isn't the vault's password.")
    except Exception:
        conn.rollback()
        attempt_ok(conn, s.user_id)
        raise
    attempt_ok(conn, s.user_id)
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
