"""Forwarding a phone's messages into Household Chat (spec/SPEC.md §6).

Each `state_changed` of a phone's Last notification sensor is handled on its own, from the event's new state (never
by reading the sensor again), on a small worker thread: `decide()` (a pure function the tests drive) applies the
owner's switch, the household's switch, the loop guard, the owner's apps and filter, Android 15's redaction, group
summaries and long texts; duplicates within 2 minutes are dropped; quiet hours hold (in memory) or drop.

**The text is never stored**: not in the database, the logs, backups or sensors. It goes straight to Household Chat
in `chat.message.post` (app_messages.post_forward, sent without the bus's database outbox) as a disappearing message.
Only the time and the target chat of each forward are kept (`forwards`), for Check now's "last forward" and the
owner's log. Duplicates are recognised by a keyed hash held in memory for 2 minutes.
"""
import hashlib
import hmac
import json
import logging
import queue
import re
import secrets
import threading
from dataclasses import dataclass

from . import alerts, config, db, phones, settings

logger = logging.getLogger("forwarding")

# Android 15+ gives notification readers without RECEIVE_SENSITIVE_NOTIFICATIONS this instead of a one-time code's
# text (§6.1). A phase-1 check on a real phone confirms the exact words; any of these (case-insensitive, anywhere in
# the text) counts, and so does an empty text under a title.
REDACTED_TEXTS = ("sensitive notification content hidden", "sensitive content hidden",
                  "notification content hidden", "content hidden")
LOOP_GUARD = ("io.homeassistant.companion.android",)       # the Companion app (Household Chat's notifications too)
APP_NAMES = {"com.google.android.apps.messaging": "Messages", "com.samsung.android.messaging": "Samsung Messages",
             "com.whatsapp": "WhatsApp", "com.whatsapp.w4b": "WhatsApp Business",
             "org.thoughtcrime.securesms": "Signal", "org.telegram.messenger": "Telegram",
             "com.android.mms": "Messages", "com.oneplus.mms": "Messages", "com.motorola.messaging": "Messages"}
CODE_WORDS = r"(?:code|otp|one[- ]time|passcode|pass code|pin|verification|verify|security code|2fa|auth)"
CODE_RE = re.compile(rf"(?i)\b{CODE_WORDS}\b[^\n]{{0,40}}?(?<![\d-])\d{{4,8}}(?![\d-])|(?<![\d-])\d{{4,8}}(?![\d-])[^\n]{{0,40}}?\b{CODE_WORDS}\b")
SUMMARY_RE = re.compile(r"(?i)^\s*(\d{1,4})\s+(new\s+)?(messages|conversations|notifications)\s*$")
MAX_TEXT = 4000                                            # the contract's limit
STATE_LIMIT = 255                                          # Home Assistant's state length: a longer text is cut
DUP_WINDOW = 120
DISAPPEAR = ("1h", "1d", "7d", "30d")


@dataclass
class Forward:
    kind: str           # message / redacted / summary
    label: str
    text: str
    app: str


def app_name(package: str) -> str:
    if package in APP_NAMES:
        return APP_NAMES[package]
    tail = (package or "app").rsplit(".", 1)[-1]
    return tail[:1].upper() + tail[1:]


def apps_of(p) -> list[str]:
    try:
        v = json.loads(p["fwd_apps"]) if p["fwd_apps"] else None
    except ValueError:
        v = None
    return v if isinstance(v, list) else list(settings.get("forward_apps"))


def words_of(p) -> list[str]:
    try:
        v = json.loads(p["fwd_words"] or "[]")
    except ValueError:
        v = []
    return [w for w in v if isinstance(w, str) and w.strip()]


def is_redacted(title: str, text: str) -> bool:
    low = (text or "").strip().lower()
    if any(r in low for r in REDACTED_TEXTS):
        return True
    return not low and bool((title or "").strip())


def matches(p, title: str, text: str) -> bool:
    f = p["fwd_filter"]
    whole = f"{title}\n{text}"
    if f == "all":
        return bool(p["fwd_all_confirmed"])
    if f == "words":
        low = whole.lower()
        return any(w.lower() in low for w in words_of(p))
    return bool(CODE_RE.search(whole))


def allowed(p) -> str | None:
    """Why this phone doesn't forward now (None: it does)."""
    if not settings.get("forwarding_allowed"):
        return "forwarding is off in this household"
    if not p["fwd_on"]:
        return "forwarding is off for this phone"
    if not p["owner_id"]:
        return "the phone has no owner"
    if p["retired"] or p["kind"] == "ignored" or phones.is_ios(p):
        return "this device doesn't forward"
    if p["fwd_target"] == "chat" and not p["fwd_chat_id"]:
        return "no chat chosen"
    return None


def decide(p, new_state: dict, *, name: str, owner_first: str, now: float) -> tuple[Forward | None, str]:
    """(the forward, or None; why) for one new notification. Pure: no database, no network."""
    why = allowed(p)
    if why:
        return None, why
    attrs = new_state.get("attributes") if isinstance(new_state.get("attributes"), dict) else {}
    package = str(attrs.get("package") or "")
    if not package or any(package == g or package.startswith(g + ".") or package.startswith(g) for g in LOOP_GUARD):
        return None, "loop guard"
    if package not in apps_of(p):
        return None, "not a chosen app"
    title = str(attrs.get("android.title") or attrs.get("android.title.big") or "").strip()
    big = attrs.get("android.bigText")
    raw = big if isinstance(big, str) and big.strip() else (attrs.get("android.text") or "")
    text = str(raw).strip()
    from_state = False
    if not text and not title:
        text = str(new_state.get("state") or "").strip()
        from_state = True
    app = app_name(package)
    at = config.hhmm(config.parse_iso(attrs.get("post_time")) if isinstance(attrs.get("post_time"), str) else
                     (attrs.get("post_time") / 1000 if isinstance(attrs.get("post_time"), (int, float)) else now))
    label = f"📱 {name} · {app} · {at}"[:80]
    # group summaries: "3 new messages" without any message text
    summary = SUMMARY_RE.match(text) or (attrs.get("android.summaryText") and not attrs.get("android.text"))
    if summary or attrs.get("is_group_summary") is True:
        m = SUMMARY_RE.match(text)
        n = m.group(1) if m else None
        return Forward("summary", label, f"📱 {n + ' new messages' if n else 'New messages'} on {owner_first}'s phone",
                       app), "summary"
    if is_redacted(title, text):
        sender = f", from {title}" if title else ""
        return Forward("redacted", label, f"📱 A code arrived on {owner_first}'s phone ({at}, {app}{sender}) — "
                                          "Android hid it.", app), "redacted"
    if not matches(p, title, text):
        return None, "filtered"
    shortened = text.endswith(("…", "...")) or (from_state and len(text) >= STATE_LIMIT) or len(text) > MAX_TEXT - 200
    body = (f"{title}\n" if title else "") + text
    if len(body) > MAX_TEXT - 40:
        body = body[:MAX_TEXT - 41].rstrip() + "…"
    if shortened:
        body += "\n(shortened — see the phone)"
    return Forward("message", label, body, app), "ok"


# --------------------------------------------------------------------------------------------- duplicates

_KEY = secrets.token_bytes(32)          # a new key each start: the hashes mean nothing outside this process
_recent: dict = {}                      # phone id -> [(hash, at)]
_recent_lock = threading.Lock()


def duplicate(pid: str, text: str, now: float) -> bool:
    """The same text from the same phone (any app) within 2 minutes? Remembers it otherwise."""
    norm = " ".join(text.split()).lower()
    h = hmac.new(_KEY, f"{pid}\0{norm}".encode(), hashlib.sha256).digest()
    with _recent_lock:
        items = [(x, t) for x, t in _recent.get(pid, []) if now - t < DUP_WINDOW]
        if any(x == h for x, _ in items):
            _recent[pid] = items
            return True
        items.append((h, now))
        _recent[pid] = items[-50:]
    return False


# --------------------------------------------------------------------------------------------- quiet hours (memory)

_held: dict = {}                        # phone id -> [Forward] held by the owner's forwarding quiet hours


def release_held(now: float | None = None) -> int:
    """Send what quiet hours held, once they're over (lost if the app restarts meanwhile: nothing is stored)."""
    now = config.clock() if now is None else now
    n = 0
    with _recent_lock:
        pids = list(_held)
    for pid in pids:
        with db.get_conn() as conn:
            p = conn.execute("SELECT * FROM phones WHERE id = ?", (pid,)).fetchone()
        if p is None or allowed(p):
            with _recent_lock:
                _held.pop(pid, None)
            continue
        if alerts.in_quiet(p["fwd_quiet_start"], p["fwd_quiet_end"], config.local(now)):
            continue
        with _recent_lock:
            items = _held.pop(pid, [])
        for f in items:
            n += bool(deliver(p, f, now))
    return n


# --------------------------------------------------------------------------------------------- delivery

def target_name(p) -> str:
    return "📌 My room" if p["fwd_target"] == "my_room" else (p["fwd_chat_name"] or "a chat")


def disappear(p) -> str:
    v = p["fwd_disappear"] or settings.get("forward_disappear")
    return v if v in DISAPPEAR else "1h"


def deliver(p, f: Forward, now: float) -> bool:
    """Post it into Chat (no storage) and keep only the time and the chat."""
    from . import app_messages
    data = {"requested_by": p["owner_id"], "label": f.label, "text": f.text, "disappear_after": disappear(p)}
    if p["fwd_target"] == "my_room":
        data["my_room"] = True
    else:
        data["chat_id"] = p["fwd_chat_id"]
    ok = app_messages.post_forward(p["id"], data)
    if ok:
        with db.get_conn() as conn:
            conn.execute("INSERT INTO forwards (phone_id, at, chat_name, kind) VALUES (?,?,?,?)",
                         (p["id"], now, target_name(p), f.kind))
    return ok


def handle(pid: str, new_state: dict, now: float | None = None) -> str:
    """One notification, start to end. Returns what happened (for the tests; never the text)."""
    now = config.clock() if now is None else now
    with db.get_conn() as conn:
        p = conn.execute("SELECT * FROM phones WHERE id = ?", (pid,)).fetchone()
        if p is None:
            return "unknown phone"
        name = phones.display_name(conn, p)
        owner_first = phones.first_name(phones.owner_name(conn, p["owner_id"]))
    f, why = decide(p, new_state, name=name, owner_first=owner_first, now=now)
    if f is None:
        return why
    if f.kind == "redacted":
        with db.get_conn() as conn:
            conn.execute("UPDATE phones SET redacted_at = ? WHERE id = ?", (now, pid))
    if duplicate(pid, f.text if f.kind != "redacted" else f.label + f.text, now):
        return "duplicate"
    if alerts.in_quiet(p["fwd_quiet_start"], p["fwd_quiet_end"], config.local(now)):
        if p["fwd_quiet_mode"] == "drop":
            return "dropped (quiet hours)"
        with _recent_lock:
            _held.setdefault(pid, []).append(f)
            _held[pid] = _held[pid][-20:]
        return "held (quiet hours)"
    return "sent" if deliver(p, f, now) else "not sent"


# --------------------------------------------------------------------------------------------- the worker

_queue: "queue.Queue" = queue.Queue(maxsize=500)
_worker = {"thread": None}
SYNC = {"on": False}                    # tests: handle at once, on the caller's thread


def submit(pid: str, new_state: dict, old_text=None) -> None:
    """From the WebSocket thread: a copy of the event's new state goes to the worker (never logged)."""
    if not isinstance(new_state, dict):
        return
    item = (pid, {"state": new_state.get("state"), "attributes": dict(new_state.get("attributes") or {})},
            config.clock())
    if SYNC["on"]:
        handle(item[0], item[1], item[2])
        return
    try:
        _queue.put_nowait(item)
    except queue.Full:
        logger.warning("Too many notifications at once; one was dropped.")
    _ensure_worker()


def _ensure_worker() -> None:
    t = _worker["thread"]
    if t is None or not t.is_alive():
        t = threading.Thread(target=_run, daemon=True, name="phones-forwarding")
        _worker["thread"] = t
        t.start()


def _run() -> None:
    while True:
        pid, st, at = _queue.get()
        try:
            result = handle(pid, st, at)
            logger.info("A notification from phone %s: %s.", pid, result)
        except Exception as e:                                    # noqa: BLE001 — never the text in the log
            logger.warning("Forwarding a notification from phone %s failed (%s).", pid, type(e).__name__)
        del st


def reset() -> None:
    """Tests."""
    with _recent_lock:
        _recent.clear()
        _held.clear()
