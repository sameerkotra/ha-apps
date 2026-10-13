"""Kids mode (§13.13): a device locked to the photo quiz.

The browser sends a random per-device id (`X-Device-Id`). Starting kids mode
also sets an HttpOnly cookie with that id, so a locked browser stays locked even
when a request leaves the header out (the page's own script can't read or clear
the cookie). While that device has a row in `kid_sessions`, every API route
except the quiz, its photos and the unlock routes answers 423 Locked — enforced
here, not just hidden in the page. A change (anything but GET) that carries no
device id at all is refused too while the person has kids mode on anywhere: the
app's own pages always send one. Unlocking: the parent's kids-mode PIN (PBKDF2) or, with no PIN, a
grown-up sum made by the server. Five wrong tries block unlocking for five
minutes; an admin can end kids mode for any device.
"""
import hashlib
import hmac
import json
import re
import secrets
from datetime import datetime, timedelta

from . import config, db

DEVICE_RE = re.compile(r"^[A-Za-z0-9_-]{16,64}$")
MAX_FAILS = 5
BLOCK_MINUTES = 5
PBKDF2_ROUNDS = 200_000
TIME_LIMITS = (0, 10, 15, 30, 60)
MODES = ("who", "call", "find")

# Routes a locked device may still use (method, regex on the path)
_OPEN = [("GET", re.compile(r"^/api/kidmode$")), ("POST", re.compile(r"^/api/kidmode/(challenge|unlock)$")),
         ("POST", re.compile(r"^/api/quiz/(next|answer)$")), ("GET", re.compile(r"^/api/media/[^/]+/file$")),
         ("GET", re.compile(r"^/api/health$"))]
_QUIZ = re.compile(r"^/api/quiz/")


COOKIE = "family_tree_kid"
COOKIE_DAYS = 180
_INGRESS_PATH = re.compile(r"^/[A-Za-z0-9_./-]{0,200}$")


def device_id(headers) -> str | None:
    d = (headers.get("x-device-id") or "").strip()
    return d if DEVICE_RE.match(d) else None


def request_device(request) -> str | None:
    """This browser's device: the kids-mode cookie when there is one (set when kids mode started here),
    else the X-Device-Id header."""
    c = (request.cookies.get(COOKIE) or "").strip()
    return c if DEVICE_RE.match(c) else device_id(request.headers)


def cookie_path(request) -> str:
    """The app's own path under Home Assistant (X-Ingress-Path), so the cookie goes to this app only."""
    p = (request.headers.get("x-ingress-path") or "").rstrip("/")
    return (p + "/") if p and _INGRESS_PATH.match(p) and ".." not in p else "/"


def set_cookie(response, request, device: str) -> None:
    response.set_cookie(COOKIE, device, max_age=COOKIE_DAYS * 86400, path=cookie_path(request),
                        httponly=True, samesite="strict")


def clear_cookie(response, request) -> None:
    response.delete_cookie(COOKIE, path=cookie_path(request), httponly=True, samesite="strict")


def session(conn, device: str | None) -> dict | None:
    if not device:
        return None
    row = conn.execute("SELECT * FROM kid_sessions WHERE device_id = ?", (device,)).fetchone()
    if not row:
        return None
    s = dict(row)
    s["modes"] = json.loads(s["modes"])
    return s


def time_up(s: dict, now: datetime | None = None) -> bool:
    if not s.get("ends_at"):
        return False
    return (now or config.utcnow()) >= datetime.fromisoformat(s["ends_at"])


def check_blocking(method: str, path: str, device: str | None, user_id: str | None = None) -> tuple[int, str] | None:
    """(status, message) when this request must be refused, else None. Opens its own connection.
    `user_id` matters only without a device: a change from someone with kids mode on somewhere."""
    if not path.startswith("/api/") or not (device or (user_id and method not in ("GET", "HEAD"))):
        return None
    from . import features
    if not features.on("quiz"):          # quiz switched off: kids mode can't hold a device either
        return None
    with db.get_conn() as conn:
        if device:
            s = session(conn, device)
        else:
            s = conn.execute("SELECT 1 FROM kid_sessions WHERE user_id = ? LIMIT 1", (user_id,)).fetchone()
            if s and not any(m == method and rx.match(path) for m, rx in _OPEN):
                return 423, "Kids mode is on for one of your devices, and this request didn't say which device it came from."
            return None
    if not s:
        return None
    if not any(m == method and rx.match(path) for m, rx in _OPEN):
        return 423, "Kids mode is on for this device. Ask a grown-up to unlock it."
    if _QUIZ.match(path) and time_up(s):
        return 423, "Time's up! Ask a grown-up."
    return None


# ---------- the PIN ----------
def hash_pin(pin: str) -> str:
    salt = secrets.token_hex(16)
    dk = hashlib.pbkdf2_hmac("sha256", pin.encode(), bytes.fromhex(salt), PBKDF2_ROUNDS)
    return f"pbkdf2_sha256${PBKDF2_ROUNDS}${salt}${dk.hex()}"


def check_pin(pin: str, stored: str | None) -> bool:
    try:
        algo, rounds, salt, want = (stored or "").split("$")
        if algo != "pbkdf2_sha256":
            return False
        dk = hashlib.pbkdf2_hmac("sha256", pin.encode(), bytes.fromhex(salt), int(rounds))
    except (ValueError, TypeError):
        return False
    return hmac.compare_digest(dk.hex(), want)


def valid_pin(pin) -> bool:
    return isinstance(pin, str) and bool(re.fullmatch(r"\d{4,6}", pin))


# ---------- the grown-up question ----------
def new_question() -> tuple[str, str]:
    """("47 + 38", "85") — easy for a grown-up, hard for a five-year-old."""
    r = secrets.SystemRandom()
    if r.random() < 0.5:
        a, b = r.randint(23, 89), r.randint(23, 89)
        return f"{a} + {b}", str(a + b)
    a, b = r.randint(6, 12), r.randint(6, 12)
    return f"{a} × {b}", str(a * b)


def blocked_until(s: dict) -> str | None:
    b = s.get("unlock_blocked_until")
    if b and config.utcnow() < datetime.fromisoformat(b):
        return b
    return None


def block_after_fail(conn, s: dict) -> dict:
    fails = s["failed_unlocks"] + 1
    until = None
    if fails >= MAX_FAILS:
        until = (config.utcnow() + timedelta(minutes=BLOCK_MINUTES)).isoformat()
        fails = 0
    conn.execute("UPDATE kid_sessions SET failed_unlocks = ?, unlock_blocked_until = ?, challenge = NULL WHERE device_id = ?",
                 (fails, until, s["device_id"]))
    return {"triesLeft": MAX_FAILS - fails if not until else 0, "blockedUntil": until}
