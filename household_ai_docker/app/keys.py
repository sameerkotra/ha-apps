"""Access keys (SPEC.md §4), used only while *Require an access key* is on.

A key is 32 random bytes (base64url), shown once and stored as its SHA-256 hash with a label, so a backup carries
no usable key and restoring one keeps the keys working. Ten failures in a minute from one address are answered
429 for a minute.
"""
import base64
import hashlib
import json
import secrets
import time
import uuid

from . import config, db, schedule

FAILS_PER_MINUTE = 10
MAX_KEYS = 100


def _hash(key: str) -> str:
    return hashlib.sha256(key.encode("utf-8")).hexdigest()


def new_key() -> str:
    return base64.urlsafe_b64encode(secrets.token_bytes(32)).rstrip(b"=").decode("ascii")


def create(label: str, models: list[str] | None, by: str) -> tuple[str, str]:
    """(id, key). The key is returned once and never again."""
    key = new_key()
    kid = "key_" + uuid.uuid4().hex[:12]
    models = sorted({schedule.norm_model(m) for m in models}) if models else None
    with db.get_conn() as conn:
        n = conn.execute("SELECT COUNT(*) FROM model_keys WHERE revoked_at IS NULL").fetchone()[0]
        if n >= MAX_KEYS:
            raise ValueError(f"At most {MAX_KEYS} keys; revoke one first.")
        conn.execute("INSERT INTO model_keys (id, label, key_hash, models, created_at, created_by) "
                     "VALUES (?, ?, ?, ?, ?, ?)",
                     (kid, label, _hash(key), json.dumps(models) if models else None,
                      config.utcnow().isoformat(timespec="seconds"), by))
    return kid, key


def revoke(kid: str) -> bool:
    with db.get_conn() as conn:
        cur = conn.execute("UPDATE model_keys SET revoked_at = ? WHERE id = ? AND revoked_at IS NULL",
                           (config.utcnow().isoformat(timespec="seconds"), kid))
        return cur.rowcount > 0


def rows() -> list[dict]:
    with db.get_conn() as conn:
        rs = conn.execute("SELECT id, label, models, created_at, created_by, last_used_at, revoked_at "
                          "FROM model_keys ORDER BY created_at").fetchall()
    return [{"id": r[0], "label": r[1], "models": json.loads(r[2]) if r[2] else None, "createdAt": r[3],
             "createdBy": r[4], "lastUsedAt": r[5], "revokedAt": r[6]} for r in rs]


def check(key: str) -> dict | None:
    """The key's row ({id, models}) if it is a live key, else None. Records its use."""
    if not key or len(key) > 200:
        return None
    h = _hash(key)
    with db.get_conn() as conn:
        r = conn.execute("SELECT id, models FROM model_keys WHERE key_hash = ? AND revoked_at IS NULL",
                         (h,)).fetchone()
        if r is None:
            return None
        conn.execute("UPDATE model_keys SET last_used_at = ? WHERE id = ?",
                     (config.utcnow().isoformat(timespec="seconds"), r[0]))
    return {"id": r[0], "models": json.loads(r[1]) if r[1] else None}


def labels() -> dict:
    with db.get_conn() as conn:
        return {r[0]: r[1] for r in conn.execute("SELECT id, label FROM model_keys").fetchall()}


class Failures:
    """Wrong or missing keys per address: more than FAILS_PER_MINUTE in a minute → blocked for a minute."""

    def __init__(self, clock=time.monotonic):
        self.clock = clock
        self.seen: dict[str, list[float]] = {}
        self.blocked: dict[str, float] = {}

    def is_blocked(self, address: str) -> bool:
        until = self.blocked.get(address)
        if until is None:
            return False
        if self.clock() >= until:
            del self.blocked[address]
            return False
        return True

    def fail(self, address: str) -> None:
        now = self.clock()
        recent = [t for t in self.seen.get(address, []) if now - t < 60] + [now]
        self.seen[address] = recent
        if len(recent) >= FAILS_PER_MINUTE:
            self.blocked[address] = now + 60
            self.seen[address] = []
        if len(self.seen) > 1000:                       # forget old addresses
            self.seen = {a: ts for a, ts in self.seen.items() if ts and now - ts[-1] < 60}


FAILURES = Failures()
