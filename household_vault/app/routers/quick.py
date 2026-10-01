"""Quick unlock with a passkey (SPEC §12.5).

The browser registers a passkey (fingerprint, face or device PIN) with the
WebAuthn PRF extension. The PRF output never leaves the browser: it becomes
an AES-GCM key there, which encrypts the master password; only that
ciphertext (`wrapped`) and a random PRF salt are stored here. To unlock, the
browser asks for the passkey, decrypts the master password and sends it to
POST /api/unlock as usual (with `quickUnlockId`, so failures are counted).

So the server never holds anything that opens a vault on its own, and it
doesn't need to check WebAuthn signatures: without the authenticator the
ciphertext is useless. Limits: the master password must have been typed in
the last 14 days; 5 failed quick unlocks remove that device; a master
password change removes them all.
"""
import base64
import hmac
import secrets
import time

from fastapi import APIRouter, Depends, HTTPException
from pydantic import Field

from .. import alerts, config, db, service, sessions
from ..auth import require_user
from .common import Ctx, Strict, unlocked

router = APIRouter(prefix="/api", tags=["quick-unlock"])
MAX_DAYS = 14
MAX_FAILURES = 5
MAX_DEVICES = 10
_pending: dict = {}                 # session token → (prf salt, challenge, time)


def b64u(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).decode().rstrip("=")


def typed_recently(u) -> bool:
    t = config.parse_iso(u["last_typed_unlock"])
    return t is not None and (config.utcnow() - t).total_seconds() < MAX_DAYS * 86400


def devices(conn, user_id: str) -> list:
    return [{"id": r["id"], "label": r["label"], "createdAt": r["created_at"], "lastUsed": r["last_used"]}
            for r in conn.execute("SELECT * FROM quick_unlock WHERE user_id = ? ORDER BY created_at", (user_id,))]


@router.get("/me/quick-unlock")
def list_devices(ctx: Ctx = Depends(unlocked)):
    with db.get_conn() as conn:
        return {"devices": devices(conn, ctx.user["id"])}


@router.post("/me/quick-unlock/begin")
def begin(ctx: Ctx = Depends(unlocked)):
    """A fresh PRF salt and challenge for registering this device."""
    salt, challenge = secrets.token_bytes(32), secrets.token_bytes(32)
    now = time.time()
    for t in [t for t, v in _pending.items() if now - v[2] > 600]:
        _pending.pop(t, None)
    _pending[ctx.session.token] = (b64u(salt), b64u(challenge), now)
    return {"prfSalt": b64u(salt), "challenge": b64u(challenge), "userHandle": b64u(secrets.token_bytes(16)),
            "name": ctx.user["name"]}


class Wrapped(Strict):
    iv: str = Field(min_length=8, max_length=64)
    ct: str = Field(min_length=8, max_length=4096)


class RegisterIn(Strict):
    credentialId: str = Field(min_length=8, max_length=1024)
    label: str = Field(min_length=1, max_length=60)
    wrapped: Wrapped
    password: str = Field(min_length=1, max_length=1000)


@router.post("/me/quick-unlock", status_code=201)
def register(body: RegisterIn, ctx: Ctx = Depends(unlocked)):
    pend = _pending.pop(ctx.session.token, None)
    if pend is None:
        raise HTTPException(409, "Start again — the setup took too long.")
    with db.get_conn() as conn:
        u = service.user_row(conn, ctx.user["id"])
        personal = service.personal_of(conn, ctx.user["id"])
        ov = sessions.get_open(personal["id"]) if personal else None
        if u["status"] != "active" or ov is None or not hmac.compare_digest(ov.password.encode(), body.password.encode()):
            raise HTTPException(403, "That isn't your master password.")
        if conn.execute("SELECT COUNT(*) FROM quick_unlock WHERE user_id = ?", (ctx.user["id"],)).fetchone()[0] >= MAX_DEVICES:
            raise HTTPException(409, f"You can set up quick unlock on at most {MAX_DEVICES} devices.")
        conn.execute("INSERT INTO quick_unlock (id, user_id, credential_id, label, prf_salt, wrapped, key_epoch, created_at) "
                     "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                     (db.new_id(), ctx.user["id"], body.credentialId, body.label.strip(), pend[0],
                      body.wrapped.model_dump_json(), u["master_epoch"], config.now_iso()))
        db.audit(conn, "quick_unlock_added", ctx.user["id"])
        alerts.send(conn, [ctx.user["id"]], f"Quick unlock with a passkey was set up for your Household Vault "
                    f"(“{body.label.strip()}”). If it wasn't you, remove it in Settings and change your master password.")
        return {"devices": devices(conn, ctx.user["id"])}


@router.delete("/me/quick-unlock/{qid}")
def remove(qid: str, ctx: Ctx = Depends(unlocked)):
    with db.get_conn() as conn:
        if not conn.execute("DELETE FROM quick_unlock WHERE id = ? AND user_id = ?", (qid, ctx.user["id"])).rowcount:
            raise HTTPException(404, "That device isn't set up.")
        return {"devices": devices(conn, ctx.user["id"])}


@router.get("/quick-unlock/options")
def options(user: dict = Depends(require_user)):
    """Before unlocking: which passkeys can open this person's vault, with their PRF salts and wrapped passwords
    (useless without the authenticator)."""
    import json
    with db.get_conn() as conn:
        u = service.user_row(conn, user["id"])
        rows = conn.execute("SELECT * FROM quick_unlock WHERE user_id = ? AND key_epoch = ?",
                            (user["id"], u["master_epoch"])).fetchall() if u["status"] == "active" else []
    if not rows:
        return {"available": False, "reason": None, "credentials": []}
    if not typed_recently(u):
        return {"available": False, "reason": f"Type your master password — it's needed at least every {MAX_DAYS} days.",
                "credentials": []}
    return {"available": True, "reason": None, "challenge": b64u(secrets.token_bytes(32)),
            "credentials": [{"id": r["id"], "credentialId": r["credential_id"], "prfSalt": r["prf_salt"],
                             "wrapped": json.loads(r["wrapped"])} for r in rows]}


def check_quick(conn, user_id: str, qid: str):
    """Before a quick unlock is tried: is this device still allowed?"""
    r = conn.execute("SELECT * FROM quick_unlock WHERE id = ? AND user_id = ?", (qid, user_id)).fetchone()
    u = service.user_row(conn, user_id)
    if r is None or r["key_epoch"] != u["master_epoch"]:
        raise HTTPException(409, "Quick unlock isn't set up on this device any more — type your master password.")
    if not typed_recently(u):
        raise HTTPException(409, f"Type your master password — it's needed at least every {MAX_DAYS} days.")
    return r


def quick_failed(conn, qid: str) -> None:
    r = conn.execute("SELECT failures FROM quick_unlock WHERE id = ?", (qid,)).fetchone()
    if r is None:
        return
    if r["failures"] + 1 >= MAX_FAILURES:
        conn.execute("DELETE FROM quick_unlock WHERE id = ?", (qid,))
    else:
        conn.execute("UPDATE quick_unlock SET failures = failures + 1 WHERE id = ?", (qid,))


def quick_worked(conn, qid: str) -> None:
    conn.execute("UPDATE quick_unlock SET failures = 0, last_used = ? WHERE id = ?", (config.now_iso(), qid))
