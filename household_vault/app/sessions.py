"""Unlocked sessions and open vaults — held in memory only (SPEC §4, §7).

A session belongs to one HA user and one browser tab: a random token kept in
the page's memory. Each open vault exists once in memory (`OpenVault`) and is
shared by every session that may use it; when the last such session locks or
times out, the vault is closed and its password and contents are dropped.
Nothing here is ever written to disk; an app restart locks everyone.
"""
import hmac
import logging
import secrets
import threading
import time
from dataclasses import dataclass, field

from fastapi import HTTPException

from . import db, kdbx, settings

logger = logging.getLogger("sessions")
_lock = threading.RLock()
_sessions: dict = {}
_open: dict = {}
_end_hooks: list = []                # fn(token) when a session ends (per-session caches let go of it)
MAX_SESSIONS_PER_USER = 20           # tabs and devices; the oldest is locked when a 21st unlocks


@dataclass
class OpenVault:
    """One open vault, shared by every session that may use it.

    `password` is kept only for random-password vaults (members are given it, sealed, and may look at it).
    A vault with a person-chosen password — every Personal vault — keeps only the composite key (a hash of the
    password, what opens the file), so the master password itself isn't held in memory while unlocked
    (review 2026-10)."""
    vault_id: str
    db: object                       # kdbx.Database
    password: str | None
    key_epoch: int
    lock: threading.RLock = field(default_factory=threading.RLock)
    sessions: set = field(default_factory=set)
    index: list | None = None        # search index, rebuilt lazily after changes
    health: list | None = None       # password-health facts, rebuilt lazily after changes
    composite: bytes = b""
    keep_password: bool = True

    def __post_init__(self):
        if self.password is not None and not self.composite:
            self.composite = kdbx.composite_key(self.password)
        if not self.keep_password:
            self.password = None

    def matches(self, password: str) -> bool:
        """Does `password` open this vault? (constant-time)"""
        return bool(self.composite) and hmac.compare_digest(self.composite, kdbx.composite_key(password))

    def set_password(self, password: str) -> None:
        self.composite = kdbx.composite_key(password)
        self.password = password if self.keep_password else None

    def changed(self):
        self.index = None
        self.health = None


@dataclass
class Session:
    token: str
    user_id: str
    created: float
    last: float
    private_key: bytes | None
    must_change: bool = False
    vaults: set = field(default_factory=set)


def new_session(user_id: str, private_key: bytes | None, must_change: bool = False) -> Session:
    now = time.time()
    s = Session(secrets.token_urlsafe(32), user_id, now, now, private_key, must_change)
    with _lock:
        _sessions[s.token] = s
        mine = sorted((x for x in _sessions.values() if x.user_id == user_id), key=lambda x: x.created)
    for old in mine[:max(0, len(mine) - MAX_SESSIONS_PER_USER)]:
        end(old.token)
    return s


def on_end(fn) -> None:
    """Call `fn(token)` whenever a session ends (locked, timed out, ended by an admin)."""
    _end_hooks.append(fn)


def _expired(s: Session, auto_lock_minutes: int, max_hours: int, now: float) -> bool:
    return now - s.last > auto_lock_minutes * 60 or now - s.created > max_hours * 3600


def get(token: str | None, user: dict, touch: bool = True) -> Session:
    """The caller's unlocked session, or 401 "locked"."""
    if not token:
        raise HTTPException(401, "Locked.")
    now = time.time()
    max_hours = settings.get("session_max_hours")       # read (cached) before taking the lock
    with _lock:
        s = _sessions.get(token)
        if s is None or s.user_id != user["id"]:
            raise HTTPException(401, "Locked.")
        if _expired(s, user["auto_lock_minutes"], max_hours, now):
            end(token)
            raise HTTPException(401, "Locked.")
        if touch:
            s.last = now
        return s


def end(token: str) -> None:
    with _lock:
        s = _sessions.pop(token, None)
        if s:
            for vid in list(s.vaults):
                detach(s, vid)
            s.private_key = None
    if s:
        for fn in list(_end_hooks):
            try:
                fn(token)
            except Exception:
                logger.exception("Couldn't tidy up after a session ended")


def end_user(user_id: str, except_token: str | None = None) -> int:
    with _lock:
        tokens = [t for t, s in _sessions.items() if s.user_id == user_id and t != except_token]
    for t in tokens:
        end(t)
    return len(tokens)


def end_all() -> None:
    with _lock:
        tokens = list(_sessions)
    for t in tokens:
        end(t)
    with _lock:
        for vid in list(_open):
            close(vid)


def user_sessions(user_id: str) -> list:
    with _lock:
        return [s for s in _sessions.values() if s.user_id == user_id]


def user_sessions_all() -> list:
    with _lock:
        return list(_sessions.values())


def get_open(vault_id: str) -> OpenVault | None:
    with _lock:
        return _open.get(vault_id)


def put_open(ov: OpenVault) -> OpenVault:
    with _lock:
        existing = _open.get(ov.vault_id)
        if existing is not None:
            return existing
        _open[ov.vault_id] = ov
        return ov


def attach(s: Session, ov: OpenVault) -> None:
    with _lock:
        ov.sessions.add(s.token)
        s.vaults.add(ov.vault_id)


def detach(s: Session, vault_id: str) -> None:
    with _lock:
        s.vaults.discard(vault_id)
        ov = _open.get(vault_id)
        if ov is not None:
            ov.sessions.discard(s.token)
            if not ov.sessions:
                close(vault_id)


def detach_user(user_id: str, vault_id: str, except_token: str | None = None) -> None:
    for s in user_sessions(user_id):
        if s.token != except_token:
            detach(s, vault_id)


def detach_all_but(vault_id: str, keep_user: str) -> None:
    """Everyone except `keep_user` loses the open vault (e.g. its password changed)."""
    with _lock:
        ov = _open.get(vault_id)
        tokens = list(ov.sessions) if ov else []
    for t in tokens:
        s = _sessions.get(t)
        if s and s.user_id != keep_user:
            detach(s, vault_id)


def close(vault_id: str) -> None:
    with _lock:
        ov = _open.pop(vault_id, None)
        if ov is not None:
            ov.password = None
            ov.composite = b""
            ov.db = None
            ov.index = None
            ov.health = None
            for t in list(ov.sessions):
                s = _sessions.get(t)
                if s:
                    s.vaults.discard(vault_id)


def expire_idle() -> int:
    """Housekeeping: end sessions past their idle or maximum time."""
    now = time.time()
    with _lock:
        items = list(_sessions.items())
    if not items:
        return 0
    max_hours = settings.get("session_max_hours")
    with db.get_conn() as conn:
        locks = {r["id"]: r["auto_lock_minutes"] for r in conn.execute("SELECT id, auto_lock_minutes FROM users")}
    n = 0
    for t, s in items:
        if _expired(s, locks.get(s.user_id, 5), max_hours, now):
            end(t)
            n += 1
    return n


def stats() -> dict:
    with _lock:
        return {"sessions": len(_sessions), "openVaults": len(_open)}
