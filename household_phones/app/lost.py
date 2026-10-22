"""Lost mode (spec/SPEC.md §15.2): a phone that's missing rings Find-my-phone style every N minutes, shows a persistent
"If found, please call <contact> — <message>" notification with its screen turned on, asks for its location more often
(`request_location_update`), and posts its latest place into a Household Chat chat — only when its owner shares their
place (Share my last place), as a zone name, plus coordinates only with the owner's "Exact location in Lost mode".

Who starts it: the owner; an admin when the owner allows Lost mode for others (for a child's phone, admins). It ends
when the owner (or whoever may start it) turns it off, at its end time, when the phone is unlocked by its owner — the
Device locked sensor going off after it started, or, without that sensor, the Interactive sensor going on after it
started (not within two minutes of the app waking the screen itself) —, or with "I found it" on the notification
(which asks for the phone to be unlocked first, Android 12 and newer).

Nothing runs during the owner's do-not-monitor hours unless the owner started it. Coordinates are never stored: they
are read from Home Assistant at the moment of posting and posted directly (not through the bus's outbox).
"""
import json
import logging
import secrets

from . import actions, config, db, dnm, monitor, phones, settings

logger = logging.getLogger("lost")

LOST_TAG = "household_phones_lost_{}"
WAKE_GRACE_S = 120                         # the screen coming on this soon after the app woke it isn't the owner
LOCKED_KEYS = ("device_locked", "keyguard_locked")
INTERACTIVE_KEYS = ("interactive", "is_interactive")
MAX_DAYS = 7
_posted: dict = {}                         # phone id -> the last place text posted (memory only)


def _changed_at(st: dict | None) -> float | None:
    if not st:
        return None
    return config.parse_iso(st.get("last_changed")) or config.parse_iso(st.get("last_updated"))


def unlock_source(pid: str) -> str | None:
    """How the end "unlocked by its owner" is seen: 'locked' (Device locked sensor), 'interactive', or None."""
    if phones.enabled_any(pid, LOCKED_KEYS):
        return "locked"
    if phones.enabled_any(pid, INTERACTIVE_KEYS):
        return "interactive"
    return None


def ends_now(p, now: float) -> str | None:
    """Why Lost mode ends now (None: it goes on)."""
    if p["lost_until"] is not None and now >= p["lost_until"]:
        return "its end time"
    since = p["lost_since"] or now
    if phones.enabled_any(p["id"], LOCKED_KEYS):
        _, st = phones.state_any(p["id"], LOCKED_KEYS)
        t = _changed_at(st)
        if st and str(st.get("state")).lower() in ("off", "false") and t and t > since + 5:
            return "the phone was unlocked"
        return None
    _, st = phones.state_any(p["id"], INTERACTIVE_KEYS)
    t = _changed_at(st)
    if st and str(st.get("state")).lower() in ("on", "true") and t and t > since + 5 and (
            p["lost_wake_at"] is None or t > p["lost_wake_at"] + WAKE_GRACE_S):
        return "the phone was used again (its screen came on)"
    return None


def contact_text(conn, p) -> str:
    who = p["lost_contact"] or phones.first_name(phones.owner_name(conn, p["owner_id"]))
    msg = p["lost_message"] or ""
    return f"If found, please call {who}" + (f" — {msg}" if msg else ".")


def notification(conn, p, token: str) -> dict:
    """The persistent "If found, please call …" notification, with "I found it" (unlock needed)."""
    data = {"tag": LOST_TAG.format(p["id"]), "visibility": "public",
            "actions": [{"action": f"HP_LOSTEND_{token}", "title": "I found it", "authenticationRequired": True}]}
    if phones.is_ios(p):
        data["push"] = {"interruption-level": "time-sensitive"}
    else:
        data.update(persistent=True, sticky="true", importance="high", priority="high", ttl=0)
    return {"title": "📱 This phone is lost", "message": contact_text(conn, p), "data": data}


# --------------------------------------------------------------------------------------------- start and stop

def _chat(body) -> dict:
    from . import app_messages
    c = body if isinstance(body, dict) else {"target": "my_room"}
    if c.get("target", "my_room") == "my_room":
        return {"target": "my_room", "chatName": "📌 My room"}
    if c.get("target") != "chat" or not isinstance(c.get("chatId"), str) or not app_messages.ID_RE.match(c["chatId"]):
        raise actions.Refused(422, "Choose a chat for the place, or your My room.")
    return {"target": "chat", "chatId": c["chatId"], "chatName": " ".join(str(c.get("chatName") or "").split())[:120] or "a chat"}


def start(pid: str, user: dict, body: dict | None, *, now: float | None = None, source=None) -> None:
    """{until?: epoch seconds, ringMin?, locMin?, chat?: {target: my_room | chat, chatId, chatName}, keep?: bool}.
    Raises actions.Refused. Blocking."""
    now = config.clock() if now is None else now
    body = body if isinstance(body, dict) else {}
    if set(body) - {"until", "ringMin", "locMin", "chat", "keep"}:
        raise actions.Refused(422, "Send until, ringMin, locMin, chat or keep.")
    until = body.get("until")
    if until is not None and (not isinstance(until, (int, float)) or isinstance(until, bool) or until <= now
                              or until - now > MAX_DAYS * 86400):
        raise actions.Refused(422, f"An end time within {MAX_DAYS} days, or none.")
    ring = body.get("ringMin", settings.get("lost_ring_min"))
    loc = body.get("locMin", settings.get("lost_location_min"))
    for v, lo, hi, what in ((ring, 2, 120, "Ring every"), (loc, 1, 60, "Ask for the location every")):
        if not isinstance(v, int) or isinstance(v, bool) or not lo <= v <= hi:
            raise actions.Refused(422, f"{what}: {lo}–{hi} minutes.")
    chat = _chat(body.get("chat"))
    chat.update(asUser=user["id"], keep=body.get("keep") is True)
    with db.get_conn() as conn:
        p = phones.get(conn, pid)
        actions.check(conn, p, user, "lost", now)
        if p["lost_since"]:
            raise actions.Refused(409, "Lost mode is already on.")
        conn.execute("UPDATE phones SET lost_since = ?, lost_by = ?, lost_by_name = ?, lost_until = ?, lost_ring_min = ?, "
                     "lost_loc_min = ?, lost_chat = ?, lost_token = ?, lost_last_ring = NULL, lost_last_loc = NULL, "
                     "lost_wake_at = NULL, lost_error = NULL WHERE id = ?",
                     (now, user["id"], user["name"][:80], until, ring, loc, json.dumps(chat), secrets.token_hex(6), pid))
        detail = f" (rings every {ring} min, location every {loc} min" + (
            f", until {config.local(until).strftime('%a %H:%M')}" if until else "") + ")"
        actions.log(conn, pid, "lost_start", detail, user, user["name"], "app", True, "on", now)
        name = phones.display_name(conn, p)
        share = bool(p["share_place"])
    _posted.pop(pid, None)
    _post(pid, chat, f"🚨 Lost mode · {name}", f"Lost mode is on for {name} (started by {user['name']}). "
          + ("Its place is posted here as it changes." if share else
             "Its owner doesn't share their place, so it isn't posted."), now)
    round_(pid, now, source)


def may_stop(conn, p, user: dict) -> bool:
    if p["owner_id"] and user["id"] == p["owner_id"]:
        return True
    if p["lost_by"] == user["id"]:
        return True
    return actions.permitted(conn, p, user, "lost")[0]


def stop(pid: str, user: dict | None, why: str, *, now: float | None = None, source=None) -> bool:
    """Turn Lost mode off (user None: the phone or the app ended it). Raises actions.Refused. Blocking."""
    now = config.clock() if now is None else now
    with db.get_conn() as conn:
        p = phones.get(conn, pid) or conn.execute("SELECT * FROM phones WHERE id = ?", (pid,)).fetchone()
        if p is None or not p["lost_since"]:
            return False
        if user is not None and not may_stop(conn, p, user):
            raise actions.Refused(403, "Only the owner, or whoever may start Lost mode on this phone, can end it.")
        conn.execute("UPDATE phones SET lost_since = NULL, lost_by = NULL, lost_by_name = NULL, lost_until = NULL, "
                     "lost_ring_min = NULL, lost_loc_min = NULL, lost_chat = NULL, lost_token = NULL, "
                     "lost_last_ring = NULL, lost_last_loc = NULL, lost_wake_at = NULL, lost_error = NULL WHERE id = ?",
                     (pid,))
        name = phones.display_name(conn, p)
    try:
        chat = json.loads(p["lost_chat"]) if p["lost_chat"] else None
    except ValueError:
        chat = None
    res = actions.send(p, [{"message": "clear_notification", "data": {"tag": LOST_TAG.format(pid)}}], source)
    actions.restore(pid, "Lost mode ended", now=now, source=source, by_name="Household Phones")
    who = user["name"] if user else "the phone"
    with db.get_conn() as conn:
        actions.log(conn, pid, "lost_end", f" ({why})", user, who if user else "Household Phones",
                    "app" if user else "phone", res.ok, "off", now)
    if chat:
        _post(pid, chat, f"🚨 Lost mode · {name}", f"Lost mode ended for {name}: {why}.", now)
    _posted.pop(pid, None)
    return True


# --------------------------------------------------------------------------------------------- each round

def place_text(p, source=None) -> str | None:
    """The phone's latest place as posted: its zone; with the owner's Exact location in Lost mode also its address
    and coordinates, read from Home Assistant now and never kept. None when the owner doesn't share their place."""
    if not p["share_place"]:
        return None
    zone = phones.zone(p["id"])
    text = f"📍 {zone}" if zone else "📍 Somewhere outside the household's zones"
    if not p["lost_exact"]:
        return text
    from . import ha_source
    states = (source or ha_source.SOURCE).states(max_age=0) or []
    tracker = phones.entity_id(p["id"], "device_tracker")
    addr = phones.value_any(p["id"], ("geocoded_location",))
    for st in states:
        if isinstance(st, dict) and st.get("entity_id") == tracker:
            a = st.get("attributes") or {}
            lat, lon = a.get("latitude"), a.get("longitude")
            if isinstance(lat, (int, float)) and isinstance(lon, (int, float)):
                acc = a.get("gps_accuracy")
                text += f"\n{lat:.5f}, {lon:.5f}" + (f" (±{int(acc)} m)" if isinstance(acc, (int, float)) else "")
                text += f"\nhttps://www.openstreetmap.org/?mlat={lat:.5f}&mlon={lon:.5f}#map=17/{lat:.5f}/{lon:.5f}"
            break
    if addr and addr != "(not kept)":
        text += f"\n{addr}"
    return text


def _post(pid: str, chat: dict | None, label: str, text: str, now: float) -> bool:
    from . import app_messages
    if not chat or not chat.get("asUser"):
        return False
    data = {"requested_by": chat["asUser"], "label": f"{label} · {config.hhmm(now)}"[:80], "text": text[:4000],
            "disappear_after": None if chat.get("keep") else "1d"}
    if chat.get("target") == "chat":
        data["chat_id"] = chat["chatId"]
    else:
        data["my_room"] = True
    return app_messages.post_direct(pid, data, kind="lost")


def round_(pid: str, now: float | None = None, source=None) -> str:
    """One Lost-mode round for a phone: end it, or ring / ask for the location / post the place when due."""
    now = config.clock() if now is None else now
    with db.get_conn() as conn:
        p = conn.execute("SELECT * FROM phones WHERE id = ?", (pid,)).fetchone()
        if p is None or not p["lost_since"]:
            return "off"
    if not monitor.runs(p):
        stop(pid, None, "the phone is no longer monitored", now=now, source=source)
        return "ended"
    why = ends_now(p, now)
    if why:
        stop(pid, None, why, now=now, source=source)
        return "ended"
    if not settings.get("actions_allowed"):
        return "off in this household"
    if dnm.active(p, now) and p["lost_by"] != p["owner_id"]:
        return "do-not-monitor hours"
    ios = phones.is_ios(p)
    ring_due = p["lost_last_ring"] is None or now - p["lost_last_ring"] >= (p["lost_ring_min"] or 10) * 60
    loc_due = p["lost_last_loc"] is None or now - p["lost_last_loc"] >= (p["lost_loc_min"] or 5) * 60
    payloads, sets = [], {}
    with db.get_conn() as conn:
        if ring_due:
            if p["lost_last_ring"] is None:
                payloads.append(notification(conn, p, p["lost_token"]))
            token = p["find_token"] or secrets.token_hex(6)
            dnd_now = phones.value_any(pid, actions.DND_KEYS)
            if not p["find_token"] and not ios:
                level, _ = actions.alarm_volume(pid)
                sets["find_restore"] = json.dumps({"volume": level, "dnd": dnd_now if dnd_now and dnd_now != "off" else None})
            payloads += actions.find_payloads(pid, "", token, ios, dnd_now=dnd_now, speak=False,
                                              text=f"This phone is lost. {contact_text(conn, p)}")
            if not ios:
                payloads.append({"message": "command_screen_on"})
                sets["lost_wake_at"] = now
            sets.update(find_token=token, find_at=now, lost_last_ring=now)
        if loc_due:
            payloads.append({"message": "request_location_update"})
            sets["lost_last_loc"] = now
        if sets:
            conn.execute("UPDATE phones SET " + ", ".join(f"{k} = ?" for k in sets) + " WHERE id = ?",
                         (*sets.values(), pid))
        name = phones.display_name(conn, p)
    if payloads:
        res = actions.send(p, payloads, source)
        with db.get_conn() as conn:
            conn.execute("UPDATE phones SET lost_error = ? WHERE id = ?", (None if res.ok else res.reason, pid))
    try:
        chat = json.loads(p["lost_chat"]) if p["lost_chat"] else None
    except ValueError:
        chat = None
    text = place_text(p, source)
    if text and chat and _posted.get(pid) != text:
        if _post(pid, chat, f"🚨 Lost mode · {name}", text, now):
            _posted[pid] = text
    return "ran"


def tick(now: float | None = None, source=None) -> int:
    now = config.clock() if now is None else now
    with db.get_conn() as conn:
        ids = [r["id"] for r in conn.execute("SELECT id FROM phones WHERE lost_since IS NOT NULL")]
    for pid in ids:
        try:
            round_(pid, now, source)
        except Exception:                                      # noqa: BLE001 — the next round tries again
            logger.exception("Lost mode round for %s failed", pid)
    return len(ids)


# --------------------------------------------------------------------------------------------- what the pages show

def view(conn, p, viewer: dict, now: float) -> dict:
    try:
        chat = json.loads(p["lost_chat"]) if p["lost_chat"] else None
    except ValueError:
        chat = None
    out = {"on": bool(p["lost_since"]), "defaults": {"ringMin": settings.get("lost_ring_min"),
                                                      "locMin": settings.get("lost_location_min")},
           "sharePlace": bool(p["share_place"]), "exact": bool(p["lost_exact"]), "contactText": contact_text(conn, p),
           "unlock": unlock_source(p["id"]), "ios": phones.is_ios(p), "mayStop": False}
    if p["lost_since"]:
        out.update(since=p["lost_since"], sinceText=config.local(p["lost_since"]).strftime("%a %H:%M"),
                   byName=p["lost_by_name"], until=p["lost_until"],
                   untilText=config.local(p["lost_until"]).strftime("%a %H:%M") if p["lost_until"] else None,
                   ringMin=p["lost_ring_min"], locMin=p["lost_loc_min"], chatName=(chat or {}).get("chatName"),
                   keep=bool((chat or {}).get("keep")), error=p["lost_error"], mayStop=may_stop(conn, p, viewer),
                   paused=bool(dnm.active(p, now)) and p["lost_by"] != p["owner_id"])
    return out
