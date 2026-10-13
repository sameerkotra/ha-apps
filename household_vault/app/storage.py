"""Encrypted vault files on disk: /data/vaults/<vault_id>/<version>.kdbx (SPEC §5.1, §6).

Every save is a new version, written to a temporary file and renamed, then
recorded in the DB. Kept: the newest KEEP_VERSIONS, plus the newest version of
each of the last KEEP_DAYS days. On a password change every older version is
purged; a re-key re-encrypts the kept ones with the new password instead.

Files are only ever removed (or replaced) **after** the transaction that drops
their rows has committed (db.Connection.after_commit): until then the previous
version is still the one the database points to, and it must stay readable.
"""
import datetime
import hashlib
import logging
import os
import shutil

from . import config, db

logger = logging.getLogger("storage")
KEEP_VERSIONS = 20
KEEP_DAYS = 30
MAX_FILE_BYTES = 20 * 1024 * 1024
REWRITE_SUFFIX = ".rekey"           # a re-encrypted older version, moved into place once the re-key commits


class TooBig(Exception):
    pass


def vault_dir(vault_id: str) -> str:
    return os.path.join(config.VAULT_DIR, vault_id)


def path_for(vault_id: str, version: int) -> str:
    return os.path.join(vault_dir(vault_id), f"{version}.kdbx")


def write_version(conn, vault_id: str, data: bytes, user_id: str | None, key_epoch: int) -> int:
    """Store `data` as the vault's next version; returns it. The caller's DB transaction commits it."""
    if len(data) > MAX_FILE_BYTES:
        raise TooBig("The vault is bigger than 20 MB.")
    row = conn.execute("SELECT version FROM vaults WHERE id = ?", (vault_id,)).fetchone()
    version = (row["version"] if row else 0) + 1
    d = vault_dir(vault_id)
    os.makedirs(d, exist_ok=True)
    final = path_for(vault_id, version)
    tmp = final + ".tmp"
    _write_file(tmp, data)
    os.replace(tmp, final)
    sha = hashlib.sha256(data).hexdigest()
    now = config.now_iso()
    conn.execute("INSERT INTO vault_versions (vault_id, version, size, sha256, key_epoch, created_by, created_at) "
                 "VALUES (?, ?, ?, ?, ?, ?, ?)", (vault_id, version, len(data), sha, key_epoch, user_id, now))
    conn.execute("UPDATE vaults SET version = ?, size = ?, sha256 = ?, updated_at = ?, updated_by = ? WHERE id = ?",
                 (version, len(data), sha, now, user_id, vault_id))
    prune(conn, vault_id)
    return version


def _write_file(path: str, data: bytes) -> None:
    with open(path, "wb") as f:
        f.write(data)
        f.flush()
        os.fsync(f.fileno())


def read_version(vault_id: str, version: int) -> bytes:
    with open(path_for(vault_id, version), "rb") as f:
        return f.read()


def read_current(conn, vault_id: str) -> bytes:
    row = conn.execute("SELECT version FROM vaults WHERE id = ?", (vault_id,)).fetchone()
    if not row or not row["version"]:
        raise FileNotFoundError(vault_id)
    return read_version(vault_id, row["version"])


def _remove_unlisted(conn, vault_id: str, versions: list) -> None:
    """After the commit: remove the files of versions whose rows are really gone (a rolled-back savepoint may have
    brought a row back)."""
    for v in versions:
        if conn.execute("SELECT 1 FROM vault_versions WHERE vault_id = ? AND version = ?", (vault_id, v)).fetchone():
            continue
        cur = conn.execute("SELECT version FROM vaults WHERE id = ?", (vault_id,)).fetchone()
        if cur is not None and v >= cur["version"]:
            continue                                    # never the current one (or a newer one being written)
        for path in (path_for(vault_id, v), path_for(vault_id, v) + REWRITE_SUFFIX):
            try:
                os.remove(path)
            except FileNotFoundError:
                pass


def _delete_versions(conn, vault_id: str, versions: list) -> None:
    if not versions:
        return
    for v in versions:
        conn.execute("DELETE FROM vault_versions WHERE vault_id = ? AND version = ?", (vault_id, v))
    conn.after_commit(lambda c: _remove_unlisted(c, vault_id, list(versions)))


def keep(rows: list, today: datetime.date | None = None) -> set:
    """Which versions to keep: the newest KEEP_VERSIONS, and the newest of each of the last KEEP_DAYS days
    (rows: version and created_at, newest first). So one busy afternoon — or someone deleting things one by one —
    can't push every older version out (review 2026-10)."""
    today = today or config.utcnow().date()
    out = {r["version"] for r in rows[:KEEP_VERSIONS]}
    days = set()
    for r in rows:
        t = config.parse_iso(r["created_at"])
        if t is None or (today - t.date()).days >= KEEP_DAYS or t.date() in days:
            continue
        days.add(t.date())
        out.add(r["version"])
    return out


def prune(conn, vault_id: str) -> None:
    rows = conn.execute("SELECT version, created_at FROM vault_versions WHERE vault_id = ? ORDER BY version DESC",
                        (vault_id,)).fetchall()
    kept = keep(rows)
    _delete_versions(conn, vault_id, [r["version"] for r in rows if r["version"] not in kept])


def older_versions(conn, vault_id: str) -> list:
    cur = conn.execute("SELECT version FROM vaults WHERE id = ?", (vault_id,)).fetchone()["version"]
    return [r["version"] for r in conn.execute("SELECT version FROM vault_versions WHERE vault_id = ? AND version < ? "
                                               "ORDER BY version", (vault_id, cur)).fetchall()]


def purge_older(conn, vault_id: str) -> None:
    """Keep only the current version (after a password change, SPEC §5.4) — the files go once it commits."""
    _delete_versions(conn, vault_id, older_versions(conn, vault_id))


def rewrite_version(conn, vault_id: str, version: int, data: bytes, key_epoch: int) -> None:
    """A re-key: an older version, re-encrypted with the new password. Written next to the old file and moved
    into place once the re-key has committed (until then the old password still opens what's there); a crash in
    between is finished at the next start (`recover`), told apart by the checksum in the row."""
    if len(data) > MAX_FILE_BYTES:
        raise TooBig("The vault is bigger than 20 MB.")
    side = path_for(vault_id, version) + REWRITE_SUFFIX
    _write_file(side, data)
    conn.execute("UPDATE vault_versions SET size = ?, sha256 = ?, key_epoch = ? WHERE vault_id = ? AND version = ?",
                 (len(data), hashlib.sha256(data).hexdigest(), key_epoch, vault_id, version))
    sha = hashlib.sha256(data).hexdigest()
    conn.after_commit(lambda c: _finish_rewrite(c, vault_id, version, side, sha))
    conn.on_rollback(lambda c: _remove_quietly(side))


def _finish_rewrite(conn, vault_id: str, version: int, side: str, sha: str) -> None:
    """After the commit: move the re-encrypted copy into place — only if the committed row is the one written with
    it (a savepoint rolled back since would have put the old row back)."""
    row = conn.execute("SELECT sha256 FROM vault_versions WHERE vault_id = ? AND version = ?", (vault_id, version)).fetchone()
    if row is not None and row["sha256"] == sha:
        os.replace(side, path_for(vault_id, version))
    else:
        _remove_quietly(side)


def _remove_quietly(path: str) -> None:
    try:
        os.remove(path)
    except FileNotFoundError:
        pass


def delete_vault_files(conn, vault_id: str) -> None:
    """The vault's folder goes once the transaction that deleted its row has committed."""
    conn.after_commit(lambda c: shutil.rmtree(vault_dir(vault_id), ignore_errors=True))


def forget_on_rollback(conn, vault_id: str) -> None:
    """A vault created in this transaction: its folder goes if the transaction doesn't commit."""
    conn.on_rollback(lambda c: shutil.rmtree(vault_dir(vault_id), ignore_errors=True))


def recover(conn) -> dict:
    """At start-up (nothing is being written): finish re-keys a crash interrupted, and tidy what failed changes
    left behind — re-encrypted copies that never committed, files of versions no row knows, and folders of vaults
    that were never created."""
    out = {"finished": 0, "removed": 0, "folders": 0}
    if not os.path.isdir(config.VAULT_DIR):
        return out
    vaults = {r["id"]: r["version"] for r in conn.execute("SELECT id, version FROM vaults")}
    for vid in os.listdir(config.VAULT_DIR):
        d = os.path.join(config.VAULT_DIR, vid)
        if not os.path.isdir(d):
            continue
        if vid not in vaults:
            shutil.rmtree(d, ignore_errors=True)
            out["folders"] += 1
            continue
        rows = {r["version"]: r["sha256"] for r in conn.execute(
            "SELECT version, sha256 FROM vault_versions WHERE vault_id = ?", (vid,))}
        for name in os.listdir(d):
            full = os.path.join(d, name)
            base = name[:-len(REWRITE_SUFFIX)] if name.endswith(REWRITE_SUFFIX) else name
            stem = base[:-len(".kdbx")] if base.endswith(".kdbx") else None
            if stem is None or not stem.isdigit():
                if name.endswith(".kdbx.tmp"):
                    _remove_quietly(full)
                    out["removed"] += 1
                continue
            v = int(stem)
            if name.endswith(REWRITE_SUFFIX):
                with open(full, "rb") as f:
                    sha = hashlib.sha256(f.read()).hexdigest()
                if rows.get(v) == sha:
                    os.replace(full, path_for(vid, v))
                    out["finished"] += 1
                else:
                    _remove_quietly(full)
                    out["removed"] += 1
            elif v not in rows and v != vaults[vid]:          # a save that never committed, or a purged one
                _remove_quietly(full)
                out["removed"] += 1
    if any(out.values()):
        logger.info("Vault files tidied at start-up: %s", out)
    return out


def new_vault_row(conn, kind: str, mode: str, name: str, owner: str | None) -> str:
    vid = db.new_id()
    conn.execute("INSERT INTO vaults (id, kind, password_mode, name, owner_user_id, created_at) VALUES (?, ?, ?, ?, ?, ?)",
                 (vid, kind, mode, name, owner, config.now_iso()))
    return vid
