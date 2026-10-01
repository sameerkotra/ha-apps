"""Personal copies in /data (App setting `personal_copies`, off by default; SPEC §12.10).

When on, each active person has one KeePass file, `/data/copies/<HA user name>.kdbx`, holding
everything they can open — Personal at the top, each other vault as a folder, like *Download all my
passwords* — **locked with their master password**. It's rewritten a few seconds after anything in
those vaults is saved (or someone's access changes), so Home Assistant's backups of the app always
hold a copy each person can open in KeePassXC/KeePassDX/KeePass with just their master password.

The server only knows a master password while that person's Personal vault is open (they're
unlocked), so a change made while someone is away marks their copy *stale*; it's rewritten the next
time they unlock. Vaults they have to type a password for (a same-password vault they didn't
remember) aren't included — the copy never holds anything its owner couldn't open anyway.
Disabling or resetting a person, or turning the setting off, deletes the files.
"""
import json
import logging
import os
import re
import threading

from . import config, db, kdbx, sessions, settings

logger = logging.getLogger("copies")
DELAY_S = float(os.environ.get("COPIES_DELAY_S", "10"))       # coalesce bursts of saves

_lock = threading.Lock()
_pending: set = set()
_timer: threading.Timer | None = None


def copy_dir() -> str:
    return os.path.join(config.DATA_DIR, "copies")


def enabled(conn=None) -> bool:
    return bool(settings.all_values(conn)["personal_copies"])


def _file_name(conn, user) -> str:
    base = re.sub(r"[^A-Za-z0-9._-]+", "-", (user["username"] or user["name"] or user["id"])).strip("-.")[:60] or "user"
    taken = conn.execute("SELECT 1 FROM user_copies WHERE file = ? AND user_id != ?", (base + ".kdbx", user["id"])).fetchone()
    return f"{base}-{user['id'][:8]}.kdbx" if taken else base + ".kdbx"


# ---------- when things change ----------
def mark_stale(conn, user_ids) -> None:
    for uid in set(user_ids):
        conn.execute("UPDATE user_copies SET stale = 1 WHERE user_id = ?", (uid,))


def people_of(conn, vault_id: str) -> list:
    """Whose copy holds this vault: its owner (Personal / Emergency) and everyone who has it remembered."""
    ids = {r["user_id"] for r in conn.execute("SELECT user_id FROM vault_keys WHERE vault_id = ?", (vault_id,))}
    v = conn.execute("SELECT kind, owner_user_id FROM vaults WHERE id = ?", (vault_id,)).fetchone()
    if v and v["kind"] == "personal" and v["owner_user_id"]:
        ids.add(v["owner_user_id"])
    return sorted(ids)


def vault_changed(conn, vault_id: str) -> None:
    """A vault was saved (items, password, re-key): refresh the copies that hold it."""
    if not enabled(conn):
        return
    ids = people_of(conn, vault_id)
    mark_stale(conn, ids)
    schedule(ids)


def access_changed(conn, user_id: str) -> None:
    """Someone joined, left or was removed from a vault, or unlocked: refresh their copy."""
    if not enabled(conn):
        return
    mark_stale(conn, [user_id])
    schedule([user_id])


def schedule(user_ids) -> None:
    global _timer
    ids = set(user_ids)
    if not ids:
        return
    with _lock:
        _pending.update(ids)
        if _timer is None:
            _timer = threading.Timer(DELAY_S, _run)
            _timer.daemon = True
            _timer.start()


def _run() -> None:
    global _timer
    with _lock:
        ids, _timer = set(_pending), None
        _pending.clear()
    for uid in sorted(ids):
        try:
            rebuild(uid)
        except Exception:
            logger.exception("Couldn't write a personal copy")


def flush() -> None:
    """Tests: do what's scheduled now."""
    global _timer
    with _lock:
        if _timer is not None:
            _timer.cancel()
            _timer = None
    _run()


# ---------- writing ----------
def rebuild(user_id: str) -> str:
    """Write the person's copy now if their master password is in memory. → 'written' | 'stale' | 'off' |
    'deleted'."""
    from . import items, service
    from .routers.vaults import _copy_tree
    with db.get_conn() as conn:
        if not enabled(conn):
            return "off"
        u = service.user_row(conn, user_id)
        if u is None or u["disabled"] or u["status"] != "active":
            delete(conn, user_id)
            return "deleted"
        personal = service.personal_of(conn, user_id)
        pov = sessions.get_open(personal["id"]) if personal else None
        if pov is None:                                   # not unlocked: done on their next unlock
            conn.execute("INSERT INTO user_copies (user_id, file, stale) VALUES (?, ?, 1) "
                         "ON CONFLICT(user_id) DO UPDATE SET stale = 1", (user_id, _file_name(conn, u)))
            return "stale"
        rows = conn.execute(
            "SELECT v.* FROM vaults v JOIN vault_members m ON m.vault_id = v.id AND m.user_id = ? "
            "WHERE NOT (v.kind = 'emergency' AND v.owner_user_id = ?) "
            "ORDER BY CASE v.kind WHEN 'personal' THEN 0 WHEN 'household' THEN 1 WHEN 'shared' THEN 2 ELSE 3 END, "
            "v.name COLLATE NOCASE", (user_id, user_id)).fetchall()
        remembered = {r["vault_id"] for r in conn.execute("SELECT vault_id FROM vault_keys WHERE user_id = ?", (user_id,))}
        owners = {r["id"]: r["name"] for r in conn.execute("SELECT id, name FROM users")}
        kdf = kdbx.kdf_params(service.strong_kdf(conn))
        file_name = _file_name(conn, u)
    combined = kdbx.Database.create(f"Household Vault — {u['name']}")
    combined.kdf = kdf
    included, missing = [], []
    for v in rows:
        mine = v["id"] == personal["id"]
        if not mine and v["id"] not in remembered:
            continue                                      # they'd have to type its password: never in the copy
        name = v["name"] if v["kind"] != "personal" else f"{owners.get(v['owner_user_id'], 'Someone')}'s Personal"
        ov = sessions.get_open(v["id"])
        if ov is None:
            missing.append(name)
            continue
        with ov.lock:
            if mine:
                _copy_tree(ov.db, ov.db.root_group, combined, combined.root_group, False)
            else:
                g = combined.add_group(combined.root_group, name)
                _copy_tree(ov.db, ov.db.root_group, combined, g, False)
        included.append("Personal" if mine else name)
    combined.reindex()
    items.sync_keepass_otp(combined)
    data = kdbx.save_bytes(combined, pov.password, new_salt=True)
    os.makedirs(copy_dir(), exist_ok=True)
    path = os.path.join(copy_dir(), file_name)
    with open(path + ".part", "wb") as f:
        f.write(data)
    os.replace(path + ".part", path)
    with db.get_conn() as conn:
        old = conn.execute("SELECT file FROM user_copies WHERE user_id = ?", (user_id,)).fetchone()
        if old and old["file"] != file_name:
            _remove_file(old["file"])
        conn.execute("INSERT INTO user_copies (user_id, file, built_at, vaults, missing, stale) VALUES (?, ?, ?, ?, ?, ?) "
                     "ON CONFLICT(user_id) DO UPDATE SET file = excluded.file, built_at = excluded.built_at, "
                     "vaults = excluded.vaults, missing = excluded.missing, stale = excluded.stale",
                     (user_id, file_name, config.now_iso(), json.dumps(included), json.dumps(missing), 1 if missing else 0))
    return "written"


def _remove_file(name: str | None) -> None:
    if name and "/" not in name and not name.startswith("."):
        try:
            os.remove(os.path.join(copy_dir(), name))
        except OSError:
            pass


def delete(conn, user_id: str) -> None:
    r = conn.execute("SELECT file FROM user_copies WHERE user_id = ?", (user_id,)).fetchone()
    if r:
        _remove_file(r["file"])
    conn.execute("DELETE FROM user_copies WHERE user_id = ?", (user_id,))


def delete_all(conn) -> int:
    n = 0
    for r in conn.execute("SELECT user_id FROM user_copies").fetchall():
        delete(conn, r["user_id"])
        n += 1
    return n


def setting_changed(conn, on: bool) -> None:
    if not on:
        delete_all(conn)
        return
    ids = [r["id"] for r in conn.execute("SELECT id FROM users WHERE status = 'active' AND disabled = 0")]
    for uid in ids:
        conn.execute("INSERT INTO user_copies (user_id, file, stale) VALUES (?, ?, 1) ON CONFLICT(user_id) DO UPDATE "
                     "SET stale = 1", (uid, _file_name(conn, service_user(conn, uid))))
    schedule(ids)


def service_user(conn, uid):
    from . import service
    return service.user_row(conn, uid)


# ---------- reporting ----------
def status_for(conn, user_id: str) -> dict | None:
    if not enabled(conn):
        return None
    r = conn.execute("SELECT * FROM user_copies WHERE user_id = ?", (user_id,)).fetchone()
    if r is None:
        return {"file": None, "builtAt": None, "stale": True, "vaults": [], "missing": []}
    return {"file": f"/data/copies/{r['file']}", "builtAt": r["built_at"], "stale": bool(r["stale"]),
            "vaults": json.loads(r["vaults"] or "[]"), "missing": json.loads(r["missing"] or "[]")}


def admin_status(conn) -> dict:
    rows = conn.execute("SELECT c.*, u.name FROM user_copies c JOIN users u ON u.id = c.user_id "
                        "ORDER BY u.name COLLATE NOCASE").fetchall()
    return {"enabled": enabled(conn), "folder": "/data/copies",
            "people": [{"name": r["name"], "file": r["file"], "builtAt": r["built_at"], "stale": bool(r["stale"])} for r in rows]}
