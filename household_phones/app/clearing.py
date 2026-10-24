"""Clearing the Last notification sensors in Home Assistant (spec/SPEC.md §6.2).

The Companion app's *Last notification* and *Last removed notification* sensors hold a message's text in Home
Assistant until the next notification replaces it. With the App setting *Clear the Last notification sensors after
reading them* (on by default), right after this app reads one of a monitored phone's — forwarded or not — it replaces
the sensor's value in Home Assistant with "cleared" and no text (POST /api/states; the phone's next notification
overwrites it as usual). A sweep (a minute after start, then hourly) clears any value still holding a message (one
restored after a restart, or from before the setting was on).

Our own overwrite is recognised by its state and a `cleared_by` attribute: it is never a check-in or a notification.
If the phone later sends the very notification that was cleared (some phones resend their sensors), it is cleared
again and not forwarded twice: a keyed hash of what was cleared is held in memory (never the text).
"""
import hashlib
import hmac
import logging
import queue
import secrets
import threading

from . import db, settings

logger = logging.getLogger("clearing")

CLEARED = "cleared"
BY = "Household Phones"
SUFFIXES = ("_last_notification", "_last_removed_notification")
KEEP_ATTRS = ("friendly_name", "icon")
NOT_A_MESSAGE = ("unavailable", "unknown", None, "")

_KEY = secrets.token_bytes(32)
_cleared: dict = {}                     # entity id -> keyed hash of the value cleared away
_lock = threading.Lock()
_queue: "queue.Queue" = queue.Queue(maxsize=500)
_worker = {"thread": None}


def is_text_sensor(entity_id) -> bool:
    return isinstance(entity_id, str) and entity_id.startswith("sensor.") and entity_id.endswith(SUFFIXES)


def is_ours(st) -> bool:
    """This app's own "cleared" value (not a report from the phone)."""
    return (isinstance(st, dict) and st.get("state") == CLEARED
            and isinstance(st.get("attributes"), dict) and st["attributes"].get("cleared_by") == BY)


def holds_message(st) -> bool:
    return isinstance(st, dict) and not is_ours(st) and st.get("state") not in NOT_A_MESSAGE


def _hash(entity_id: str, st: dict) -> bytes:
    a = st.get("attributes") if isinstance(st.get("attributes"), dict) else {}
    parts = [entity_id, str(st.get("state"))] + [str(a.get(k)) for k in ("post_time", "package", "android.title",
                                                                           "android.text")]
    return hmac.new(_KEY, "\0".join(parts).encode(), hashlib.sha256).digest()


def was_cleared(entity_id: str, st: dict) -> bool:
    """Is this the very value cleared away earlier (the phone sending it again)? Only a notification with its
    post time can be told apart from a new one with the same words."""
    a = st.get("attributes") if isinstance(st.get("attributes"), dict) else {}
    if not a.get("post_time"):
        return False
    with _lock:
        h = _cleared.get(entity_id)
    return h is not None and hmac.compare_digest(h, _hash(entity_id, st))


def wanted(pid: str) -> bool:
    if not settings.get("clear_notification_sensor"):
        return False
    with db.get_conn() as conn:
        p = conn.execute("SELECT monitored, retired FROM phones WHERE id = ?", (pid,)).fetchone()
    return bool(p and p["monitored"] and not p["retired"])


def payload(st: dict) -> dict:
    a = st.get("attributes") if isinstance(st.get("attributes"), dict) else {}
    attrs = {k: a[k] for k in KEEP_ATTRS if k in a}
    attrs["cleared_by"] = BY
    return {"state": CLEARED, "attributes": attrs}


def clear(entity_id: str, st: dict, source=None) -> bool:
    """Overwrite one sensor's value in Home Assistant (blocking)."""
    from . import ha_source
    with _lock:
        _cleared[entity_id] = _hash(entity_id, st)
        if len(_cleared) > 500:
            _cleared.pop(next(iter(_cleared)))
    ok = (source or ha_source.SOURCE).set_state(entity_id, payload(st))
    if not ok:
        logger.warning("Couldn't clear %s in Home Assistant.", entity_id)
    return ok


def submit(pid: str, entity_id: str, st: dict) -> None:
    """From the WebSocket thread: clear it on a worker (no network on the event thread)."""
    if not is_text_sensor(entity_id) or not holds_message(st):
        return
    from . import forwarding
    item = (pid, entity_id, {"state": st.get("state"), "attributes": dict(st.get("attributes") or {})})
    if forwarding.SYNC["on"]:
        _handle(*item)
        return
    try:
        _queue.put_nowait(item)
    except queue.Full:
        logger.warning("Too many notifications at once; one sensor wasn't cleared (the hourly sweep will).")
    t = _worker["thread"]
    if t is None or not t.is_alive():
        t = threading.Thread(target=_run, daemon=True, name="phones-clearing")
        _worker["thread"] = t
        t.start()


def _handle(pid: str, entity_id: str, st: dict) -> None:
    if wanted(pid):
        clear(entity_id, st)


def _run() -> None:
    while True:
        pid, eid, st = _queue.get()
        try:
            _handle(pid, eid, st)
        except Exception as e:                                    # noqa: BLE001 — never the text in the log
            logger.warning("Clearing a notification sensor failed (%s).", type(e).__name__)
        del st


def sweep(source=None) -> int:
    """Clear every monitored phone's notification sensor that still holds a message. Returns how many."""
    from . import ha_source, phones
    if not settings.get("clear_notification_sensor"):
        return 0
    src = source or ha_source.SOURCE
    states = src.states(max_age=30)
    if not states:
        return 0
    with phones.LIVE.lock:
        owner = dict(phones.LIVE.entity_phone)
    n = 0
    for st in states:
        eid = st.get("entity_id") if isinstance(st, dict) else None
        pid = owner.get(eid)
        if pid is None or not is_text_sensor(eid) or not holds_message(st) or not wanted(pid):
            continue
        n += 1 if clear(eid, st, src) else 0
    return n
