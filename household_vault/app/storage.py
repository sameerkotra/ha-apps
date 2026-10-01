"""Encrypted vault files on disk: /data/vaults/<vault_id>/<version>.kdbx (SPEC §5.1, §6).

Every save is a new version, written to a temporary file and renamed, then
recorded in the DB. The newest KEEP_VERSIONS are kept; on a password change
or re-key every older version is purged.
"""
import hashlib
import os
import shutil

from . import config, db

KEEP_VERSIONS = 20
MAX_FILE_BYTES = 20 * 1024 * 1024


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
    with open(tmp, "wb") as f:
        f.write(data)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, final)
    sha = hashlib.sha256(data).hexdigest()
    now = config.now_iso()
    conn.execute("INSERT INTO vault_versions (vault_id, version, size, sha256, key_epoch, created_by, created_at) "
                 "VALUES (?, ?, ?, ?, ?, ?, ?)", (vault_id, version, len(data), sha, key_epoch, user_id, now))
    conn.execute("UPDATE vaults SET version = ?, size = ?, sha256 = ?, updated_at = ?, updated_by = ? WHERE id = ?",
                 (version, len(data), sha, now, user_id, vault_id))
    prune(conn, vault_id)
    return version


def read_version(vault_id: str, version: int) -> bytes:
    with open(path_for(vault_id, version), "rb") as f:
        return f.read()


def read_current(conn, vault_id: str) -> bytes:
    row = conn.execute("SELECT version FROM vaults WHERE id = ?", (vault_id,)).fetchone()
    if not row or not row["version"]:
        raise FileNotFoundError(vault_id)
    return read_version(vault_id, row["version"])


def _delete_versions(conn, vault_id: str, versions: list) -> None:
    for v in versions:
        try:
            os.remove(path_for(vault_id, v))
        except FileNotFoundError:
            pass
        conn.execute("DELETE FROM vault_versions WHERE vault_id = ? AND version = ?", (vault_id, v))


def prune(conn, vault_id: str) -> None:
    rows = conn.execute("SELECT version FROM vault_versions WHERE vault_id = ? ORDER BY version DESC",
                        (vault_id,)).fetchall()
    _delete_versions(conn, vault_id, [r["version"] for r in rows[KEEP_VERSIONS:]])


def purge_older(conn, vault_id: str) -> None:
    """Keep only the current version (after a password change or re-key, SPEC §5.4)."""
    cur = conn.execute("SELECT version FROM vaults WHERE id = ?", (vault_id,)).fetchone()["version"]
    rows = conn.execute("SELECT version FROM vault_versions WHERE vault_id = ? AND version < ?",
                        (vault_id, cur)).fetchall()
    _delete_versions(conn, vault_id, [r["version"] for r in rows])


def delete_vault_files(vault_id: str) -> None:
    shutil.rmtree(vault_dir(vault_id), ignore_errors=True)


def new_vault_row(conn, kind: str, mode: str, name: str, owner: str | None) -> str:
    vid = db.new_id()
    conn.execute("INSERT INTO vaults (id, kind, password_mode, name, owner_user_id, created_at) VALUES (?, ?, ?, ?, ?, ?)",
                 (vid, kind, mode, name, owner, config.now_iso()))
    return vid
