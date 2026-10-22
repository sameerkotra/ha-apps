"""Phone actions (spec/SPEC.md §15.1): Find my phone, Screen on, Flashlight, Ringer, Do Not Disturb, Volume,
Brightness, Screen timeout, Open an app and Open a dashboard — the Home Assistant Companion app's notification
commands, sent through the phone's `notify.mobile_app_…` service (ha_source.notify).

- **Who:** the phone's owner, always; admins, for the actions the owner allows others to use (`act_allow`, defaults
  Find my phone and Screen on; for a child's phone admins choose); Home Assistant automations without a person only
  ring the phone (Find my phone) when the owner allows "from Home Assistant automations" (`act_from_ha`).
- **Never:** on a device no admin added; while its owner pauses being watched (others); during the owner's
  do-not-monitor hours, except Find my phone pressed by the owner; on an iPhone, except Find my phone (a critical
  alert); when the household's Phone actions setting is off.
- **Rate limits:** Find my phone once per 30 s and N an hour per phone; other actions once per 5 s each and N an hour.
  Commands don't count toward the Companion app's 500 notifications a day; Find my phone's alarm notification and
  spoken message do.
- **Logged:** every use, with who, how, when and the result (`actions` table and the phone's history), which the
  owner sees.

Find my phone rings on the alarm stream (it sounds on silent and vibrate) after turning the alarm volume to its
maximum and, when the phone says Do Not Disturb is on, asking it to turn that off; "Found it" on the notification (or
the App setting's time) puts the volume and Do Not Disturb back from what the phone's sensors said before.
"""
import json
import logging
import re
import secrets

from . import config, db, dnm, monitor, phones, settings

logger = logging.getLogger("actions")

PERM_DND = "Do Not Disturb access (Android's \"Do Not Disturb access\" for Home Assistant)"
PERM_OVERLAY = "permission to display over other apps"
PERM_SETTINGS = "permission to modify system settings"

# id -> label, icon, works on iPhones, the one-time phone permission it may need, seconds between two uses
ACTIONS = {
    "find": {"label": "Find my phone", "icon": "🔔", "ios": True, "needs": PERM_DND, "gap": 30},
    "screen_on": {"label": "Screen on", "icon": "💡", "ios": False, "needs": None, "gap": 5},
    "flashlight": {"label": "Flashlight", "icon": "🔦", "ios": False, "needs": None, "gap": 5},
    "ringer": {"label": "Ringer", "icon": "🔈", "ios": False, "needs": PERM_DND, "gap": 5},
    "dnd": {"label": "Do Not Disturb", "icon": "⛔", "ios": False, "needs": PERM_DND, "gap": 5},
    "volume": {"label": "Volume", "icon": "🔊", "ios": False, "needs": PERM_DND, "gap": 5},
    "brightness": {"label": "Brightness", "icon": "☀️", "ios": False, "needs": PERM_SETTINGS, "gap": 5},
    "screen_timeout": {"label": "Screen timeout", "icon": "⏲️", "ios": False, "needs": PERM_SETTINGS, "gap": 5},
    "launch_app": {"label": "Open an app", "icon": "📲", "ios": False, "needs": PERM_OVERLAY, "gap": 5},
    "dashboard": {"label": "Open a dashboard", "icon": "🏠", "ios": False, "needs": PERM_OVERLAY, "gap": 5},
}
LOST_LABEL = "Lost mode"
CONSENT_IDS = tuple(ACTIONS) + ("lost",)          # what an owner may let others use
DEFAULT_ALLOWED = ("find", "screen_on")
STREAMS = (("alarm_stream", "Alarm"), ("ring_stream", "Ring"), ("notification_stream", "Notifications"),
           ("music_stream", "Media"), ("call_stream", "Calls"), ("system_stream", "System"))
DND_MODES = (("priority_only", "On (priority only)"), ("alarms_only", "Alarms only"),
             ("total_silence", "Total silence"), ("off", "Off"))
RINGER_MODES = (("normal", "Ring"), ("vibrate", "Vibrate"), ("silent", "Silent"))
TIMEOUTS = (15, 30, 60, 120, 300, 600)
PACKAGE_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_]*(\.[A-Za-z0-9_]+)+$")
PATH_RE = re.compile(r"^/[A-Za-z0-9_\-/]{1,120}$")
VIA = ("app", "assistant", "home_assistant", "lost", "bedtime", "phone")
VIA_TEXT = {"assistant": " (Household Assistant)", "home_assistant": " (Home Assistant)", "lost": " (Lost mode)",
            "bedtime": " (bedtime)", "phone": " (on the phone)"}

# The phone's own sensors these read (unique ids vary between Companion app versions: any of them)
DND_KEYS = ("dnd_sensor", "do_not_disturb_sensor", "do_not_disturb")
ALARM_VOLUME_KEYS = ("volume_alarm", "volume_level_alarm")
FIND_TAG = "household_phones_find_{}"


class Refused(Exception):
    """Not allowed or not possible now: the HTTP status and the reason in words."""

    def __init__(self, status: int, text: str):
        super().__init__(text)
        self.status, self.text = status, text


def label(action: str) -> str:
    return LOST_LABEL if action == "lost" else ACTIONS.get(action, {}).get("label", action)


# --------------------------------------------------------------------------------------------- consent

def allowed_list(p) -> list[str]:
    """The actions the owner lets others use on this phone (NULL: the defaults)."""
    if p["act_allow"] is None:
        return list(DEFAULT_ALLOWED)
    try:
        v = json.loads(p["act_allow"])
    except ValueError:
        return list(DEFAULT_ALLOWED)
    return [a for a in v if a in CONSENT_IDS] if isinstance(v, list) else list(DEFAULT_ALLOWED)


def owner_is_child(conn, p) -> bool:
    if not p["owner_id"]:
        return False
    from . import auth
    r = conn.execute("SELECT * FROM users WHERE id = ?", (p["owner_id"],)).fetchone()
    return bool(r) and auth.user_dict(r)["is_child"]


def may_edit_consent(conn, p, user: dict) -> bool:
    """The owner chooses; for a child's phone the admins do (and not the child)."""
    if owner_is_child(conn, p):
        return bool(user.get("is_admin"))
    return bool(p["owner_id"]) and p["owner_id"] == user["id"]


def permitted(conn, p, user: dict | None, action: str) -> tuple[bool, str | None]:
    """May this person (None: a Home Assistant automation without one) use `action` on this phone? (ok, why not)."""
    lbl = label(action)
    if user is None:
        if action == "find" and p["act_from_ha"]:
            return True, None
        return False, "The phone's owner hasn't allowed Home Assistant automations to ring it."
    if p["owner_id"] and user["id"] == p["owner_id"]:
        return True, None
    if not user.get("is_admin"):
        return False, "Only the phone's owner and admins can use phone actions."
    if action == "lost" and owner_is_child(conn, p):
        return True, None                     # children's phones: admins decide
    if action in allowed_list(p):
        return True, None
    owner = phones.first_name(phones.owner_name(conn, p["owner_id"]))
    if owner_is_child(conn, p):
        return False, f"{lbl} isn't allowed on {owner}'s phone — allow it under Phone actions on its page."
    return False, f"{owner} hasn't allowed others to use {lbl} on this phone."


def check(conn, p, user: dict | None, action: str, now: float) -> None:
    """Raise Refused unless `action` may run on this phone now (everything but the rate limit)."""
    if p is None:
        raise Refused(404, "No such phone.")
    if not monitor.runs(p):
        raise Refused(409, "This device isn't monitored, so no actions can be used on it.")
    if not settings.get("actions_allowed"):
        raise Refused(403, "Phone actions are turned off in this household (an admin's App setting).")
    if action != "lost" and action not in ACTIONS:
        raise Refused(422, "Unknown action.")
    if action != "lost" and phones.is_ios(p) and not ACTIONS[action]["ios"]:
        raise Refused(409, f"{label(action)} isn't possible on an iPhone: the Companion app for iOS has no such "
                           "command. Find my phone works there (as a critical alert).")
    ok, why = permitted(conn, p, user, action)
    if not ok:
        raise Refused(403, why)
    is_owner = user is not None and user["id"] == p["owner_id"]
    if monitor.watching_paused(p, now) and not is_owner:
        raise Refused(409, f"Paused by {phones.first_name(phones.owner_name(conn, p['owner_id']))} "
                           f"{monitor.until_text(p)}.".replace("  ", " "))
    on = dnm.active(p, now)
    if on and not (is_owner and action in ("find", "lost")):
        from . import checknow
        raise Refused(409, checknow.not_monitored_text(conn, p, on, user))


def rate(conn, p, action: str, now: float) -> None:
    """Raise Refused (429) when the action was used too recently or too often on this phone (people's own uses;
    Lost mode's and bedtime's own rounds don't count)."""
    gap = ACTIONS.get(action, {}).get("gap", 5)
    mine = "via NOT IN ('lost', 'bedtime', 'phone')"
    last = conn.execute(f"SELECT MAX(at) FROM actions WHERE phone_id = ? AND action = ? AND {mine}",
                        (p["id"], action)).fetchone()[0]
    if last is not None and now - last < gap:
        raise Refused(429, f"{label(action)} was used {max(1, int(now - last))} s ago — at most once every {gap} "
                           "seconds.")
    if action == "find":
        n = conn.execute(f"SELECT COUNT(*) FROM actions WHERE phone_id = ? AND action = 'find' AND at > ? AND {mine}",
                         (p["id"], now - 3600)).fetchone()[0]
        limit = settings.get("find_per_hour")
    else:
        n = conn.execute(f"SELECT COUNT(*) FROM actions WHERE phone_id = ? AND action NOT IN ('find', 'find_end', "
                         f"'lost_start', 'lost_end') AND at > ? AND {mine}", (p["id"], now - 3600)).fetchone()[0]
        limit = settings.get("actions_per_hour")
    if n >= limit:
        raise Refused(429, f"{n} times in the last hour on this phone — the most allowed. Try again later.")


# --------------------------------------------------------------------------------------------- payloads

def clean(action: str, body: dict) -> tuple[dict, str]:
    """The page's parameters for an action → (params, words for the log). Refused 422 when they don't fit."""
    body = body if isinstance(body, dict) else {}
    if action in ("find", "screen_on"):
        return {}, ""
    if action == "flashlight":
        if not isinstance(body.get("on"), bool):
            raise Refused(422, "Turn the flashlight on or off.")
        return {"on": body["on"]}, " on" if body["on"] else " off"
    if action == "ringer":
        if body.get("mode") not in dict(RINGER_MODES):
            raise Refused(422, "Ringer: normal, vibrate or silent.")
        return {"mode": body["mode"]}, f": {dict(RINGER_MODES)[body['mode']].lower()}"
    if action == "dnd":
        if body.get("mode") not in dict(DND_MODES):
            raise Refused(422, "Do Not Disturb: off, priority only, alarms only or total silence.")
        return {"mode": body["mode"]}, f": {dict(DND_MODES)[body['mode']].lower()}"
    if action == "volume":
        lvl = body.get("level")
        if body.get("stream") not in dict(STREAMS):
            raise Refused(422, "Choose which volume.")
        if not isinstance(lvl, int) or isinstance(lvl, bool) or not 0 <= lvl <= 100:
            raise Refused(422, "A volume level from 0 (the phone uses its maximum for anything above it).")
        return {"stream": body["stream"], "level": lvl}, f": {dict(STREAMS)[body['stream']].lower()} {lvl}"
    if action == "brightness":
        lvl = body.get("level")
        if not isinstance(lvl, int) or isinstance(lvl, bool) or not 0 <= lvl <= 255:
            raise Refused(422, "Brightness is 0–255.")
        return {"level": lvl}, f": {lvl}/255"
    if action == "screen_timeout":
        sec = body.get("seconds")
        if sec not in TIMEOUTS or isinstance(sec, bool):
            raise Refused(422, "Choose one of the listed times.")
        return {"seconds": sec}, f": {sec} s" if sec < 60 else f": {sec // 60} min"
    if action == "launch_app":
        pkg = str(body.get("package") or "").strip()
        if not PACKAGE_RE.match(pkg) or len(pkg) > 150:
            raise Refused(422, "An Android package name, like com.spotify.music.")
        return {"package": pkg}, f": {pkg}"
    if action == "dashboard":
        path = str(body.get("path") or "").strip()
        if not PATH_RE.match(path) or "//" in path:
            raise Refused(422, "A dashboard path like /lovelace/0 or /dashboard-kitchen/home.")
        return {"path": path}, f": {path}"
    raise Refused(422, "Unknown action.")


def command(action: str, params: dict) -> dict:
    """The notify call for a simple action (spec §15.1; the Companion app's notification commands)."""
    if action == "screen_on":
        return {"message": "command_screen_on"}
    if action == "flashlight":
        return {"message": "command_flashlight", "data": {"command": "turn_on" if params["on"] else "turn_off"}}
    if action == "ringer":
        return {"message": "command_ringer_mode", "data": {"command": params["mode"]}}
    if action == "dnd":
        return {"message": "command_dnd", "data": {"command": params["mode"]}}
    if action == "volume":
        return {"message": "command_volume_level", "data": {"media_stream": params["stream"], "command": params["level"]}}
    if action == "brightness":
        return {"message": "command_screen_brightness_level", "data": {"command": params["level"]}}
    if action == "screen_timeout":
        return {"message": "command_screen_off_timeout", "data": {"command": params["seconds"] * 1000}}
    if action == "launch_app":
        return {"message": "command_launch_app", "data": {"package_name": params["package"]}}
    if action == "dashboard":
        return {"message": "command_webview", "data": {"command": params["path"]}}
    raise ValueError(action)


def alarm_volume(pid: str) -> tuple[int | None, int]:
    """(the alarm volume now, its maximum) from the phone's Volume level sensors (None: not reported)."""
    _, st = phones.state_any(pid, ALARM_VOLUME_KEYS)
    level = None
    mx = 100                                   # the phone uses its maximum for anything above it
    if st:
        try:
            level = int(float(st.get("state")))
        except (TypeError, ValueError):
            level = None
        try:
            mx = int((st.get("attributes") or {}).get("max") or 100)
        except (TypeError, ValueError):
            mx = 100
    return level, mx


def find_payloads(pid: str, who: str, token: str, ios: bool, *, dnd_now: str | None, speak: bool = True,
                  text: str | None = None) -> list[dict]:
    """Find my phone. Android: Do Not Disturb off (only when it's on), the alarm volume to its maximum, a high-priority
    notification on the alarm stream (it sounds on silent and vibrate) with "Found it", and a spoken message at the
    maximum alarm volume (the phone puts that volume back itself). iPhone: one critical alert (it sounds on mute and
    in Do Not Disturb)."""
    title = "📱 Find my phone"
    msg = text or f"{who} is looking for this phone. Tap Found it to stop."
    found = {"action": f"HP_FOUND_{token}", "title": "Found it"}
    if ios:
        return [{"title": title, "message": msg,
                 "data": {"push": {"sound": {"name": "default", "critical": 1, "volume": 1.0}},
                          "tag": FIND_TAG.format(pid), "actions": [found]}}]
    _, mx = alarm_volume(pid)
    out = []
    if dnd_now and dnd_now != "off":
        out.append({"message": "command_dnd", "data": {"command": "off"}})
    out.append({"message": "command_volume_level", "data": {"media_stream": "alarm_stream", "command": mx}})
    out.append({"title": title, "message": msg,
                "data": {"ttl": 0, "priority": "high", "channel": "alarm_stream", "importance": "max",
                         "tag": FIND_TAG.format(pid), "persistent": True, "sticky": "true", "visibility": "public",
                         "actions": [found]}})
    if speak:
        out.append({"message": "TTS", "data": {"tts_text": "This phone is being looked for.",
                                               "media_stream": "alarm_stream_max", "ttl": 0, "priority": "high"}})
    return out


def restore_payloads(pid: str, restore: dict | None, ios: bool) -> list[dict]:
    """After Find my phone: the notification cleared, the alarm volume and Do Not Disturb put back."""
    out = [{"message": "clear_notification", "data": {"tag": FIND_TAG.format(pid)}}]
    if ios or not restore:
        return out
    if restore.get("volume") is not None:
        out.append({"message": "command_volume_level", "data": {"media_stream": "alarm_stream",
                                                                "command": int(restore["volume"])}})
    if restore.get("dnd") and restore["dnd"] != "off":
        out.append({"message": "command_dnd", "data": {"command": restore["dnd"]}})
    return out


def send(p, payloads: list[dict], source=None):
    """Send each notify call in order; stop at the first failure. → ha_source.CommandResult."""
    from . import ha_source
    service = phones.notify_service(p)
    if not service:
        return ha_source.CommandResult(False, "signed_out", "Home Assistant has no notify service for this phone (the "
                                                            "Companion app signed out?).")
    src = source or ha_source.SOURCE
    res = ha_source.CommandResult(True)
    for payload in payloads:
        res = src.notify(service, payload)
        if not res.ok:
            break
    return res


# --------------------------------------------------------------------------------------------- the log

NAMES = {"find_end": "Find my phone stopped", "lost_start": "Lost mode on", "lost_end": "Lost mode off",
         "lost_ring": "Lost mode ring", "bedtime_dnd": "Bedtime Do Not Disturb", "test": "Test everything"}


def name_of(action: str) -> str:
    return ACTIONS[action]["label"] if action in ACTIONS else NAMES.get(action, action)


def log(conn, pid: str, action: str, detail: str, user: dict | None, by_name: str, via: str, ok: bool,
        result: str, now: float) -> None:
    via = via if via in VIA else "app"
    conn.execute("INSERT INTO actions (phone_id, at, action, detail, by_user, by_name, via, ok, result) "
                 "VALUES (?,?,?,?,?,?,?,?,?)", (pid, now, action, detail[:200], user["id"] if user else None,
                                                 by_name[:80], via, int(ok), result[:300]))
    phones.history(conn, pid, "action", f"{name_of(action)}{detail} by {by_name}{VIA_TEXT.get(via, '')} — {result}", by=user["id"] if user else None,
                   now=now)


def log_view(conn, pid: str, limit: int = 50) -> list[dict]:
    return [{"at": r["at"], "atText": config.local(r["at"]).strftime("%a %d %b %H:%M"),
             "what": name_of(r["action"]) + (r["detail"] or ""), "by": r["by_name"], "via": r["via"],
             "ok": bool(r["ok"]), "result": r["result"]}
            for r in conn.execute("SELECT * FROM actions WHERE phone_id = ? ORDER BY at DESC, id DESC LIMIT ?",
                                  (pid, limit))]


# --------------------------------------------------------------------------------------------- running one

def result_text(action: str, ok: bool, reason: str | None, extra: str = "") -> tuple[str, str | None]:
    """(what to show, the phone permission it may need)."""
    needs = ACTIONS.get(action, {}).get("needs")
    if not ok:
        return (reason or "Home Assistant couldn't send it."), None
    text = "Sent to the phone." + (f" {extra}" if extra else "")
    if needs:
        text += f" If nothing happens, the phone needs {needs} — open the HA app on the phone (it asks the first time)."
    return text, needs


def run(pid: str, action: str, body: dict | None, user: dict | None, *, via: str = "app", by_name: str | None = None,
        now: float | None = None, source=None) -> dict:
    """Use an action on a phone → {ok, action, label, text, needs}. Raises Refused. Blocking (Home Assistant)."""
    now = config.clock() if now is None else now
    params, detail = clean(action, body or {})
    who = by_name or (user["name"] if user else "Home Assistant")
    extra = ""
    with db.get_conn() as conn:
        p = phones.get(conn, pid)
        check(conn, p, user, action, now)
        rate(conn, p, action, now)
        ios = phones.is_ios(p)
        if action == "find":
            token = p["find_token"] or secrets.token_hex(6)
            dnd_now = phones.value_any(pid, DND_KEYS)
            payloads = find_payloads(pid, phones.first_name(who), token, ios, dnd_now=dnd_now)
            if not ios:
                if p["find_token"] and p["find_restore"]:
                    restore = json.loads(p["find_restore"])            # still ringing: keep what to put back
                else:
                    level, _ = alarm_volume(pid)
                    restore = {"volume": level, "dnd": dnd_now if dnd_now and dnd_now != "off" else None}
                conn.execute("UPDATE phones SET find_token = ?, find_at = ?, find_restore = ? WHERE id = ?",
                             (token, now, json.dumps(restore), pid))
                if restore.get("volume") is None:
                    extra = ("The alarm volume stays at its maximum afterwards: the phone doesn't report it (turn on "
                             "its Volume level sensors).")
                if dnd_now and dnd_now != "off":
                    extra += (" Do Not Disturb was on: the phone was asked to turn it off until Found it (Android 15 "
                              "and newer only allow that when Home Assistant turned it on; the alarm still sounds when "
                              "Do Not Disturb lets alarms through).")
            else:
                conn.execute("UPDATE phones SET find_token = ?, find_at = ?, find_restore = NULL WHERE id = ?",
                             (token, now, pid))
                extra = "On an iPhone it's a critical alert (it sounds on mute and in Do Not Disturb)."
        else:
            payloads = [command(action, params)]
    res = send(p, payloads, source)
    text, needs = result_text(action, res.ok, res.reason, extra.strip())
    with db.get_conn() as conn:
        log(conn, pid, action, detail, user, who, via, res.ok, "sent" if res.ok else (res.reason or "failed"), now)
    return {"ok": res.ok, "action": action, "label": label(action), "text": text, "needs": needs}


def restore(pid: str, why: str, *, now: float | None = None, source=None, by_name: str = "the phone",
            via: str = "phone") -> bool:
    """End a Find my phone: clear its notification, put the alarm volume and Do Not Disturb back. Blocking."""
    now = config.clock() if now is None else now
    with db.get_conn() as conn:
        p = conn.execute("SELECT * FROM phones WHERE id = ?", (pid,)).fetchone()
        if p is None or not p["find_token"]:
            return False
        try:
            r = json.loads(p["find_restore"]) if p["find_restore"] else None
        except ValueError:
            r = None
        conn.execute("UPDATE phones SET find_token = NULL, find_at = NULL, find_restore = NULL WHERE id = ?", (pid,))
    res = send(p, restore_payloads(pid, r, phones.is_ios(p)), source)
    bits = []
    if r and r.get("volume") is not None:
        bits.append(f"alarm volume back to {r['volume']}")
    if r and r.get("dnd"):
        bits.append("Do Not Disturb back on")
    with db.get_conn() as conn:
        log(conn, pid, "find_end", f" ({why})", None, by_name, via, res.ok,
            ("stopped" + (": " + ", ".join(bits) if bits else "")) if res.ok else (res.reason or "failed"), now)
    return True


def due_restores(now: float | None = None, source=None) -> int:
    """Find my phone that nobody stopped: put things back after the App setting's time (not during Lost mode)."""
    now = config.clock() if now is None else now
    after = settings.get("find_restore_min") * 60
    with db.get_conn() as conn:
        ids = [r["id"] for r in conn.execute("SELECT id FROM phones WHERE find_token IS NOT NULL AND lost_since IS NULL "
                                             "AND find_at <= ?", (now - after,))]
    for pid in ids:
        restore(pid, "time's up", now=now, source=source, by_name="Household Phones", via="phone")
    return len(ids)


def on_notification_action(event: dict) -> None:
    """`mobile_app_notification_action` from a phone: "Found it" (Find my phone) or "I found it" (Lost mode)."""
    import threading
    data = event.get("data") if isinstance(event, dict) else None
    act = str((data or {}).get("action") or "")
    m = re.match(r"^HP_(FOUND|LOSTEND)_([0-9a-f]{12})$", act)
    if not m:
        return
    kind, token = m.groups()

    def go():
        from . import lost
        col = "find_token" if kind == "FOUND" else "lost_token"
        with db.get_conn() as conn:
            r = conn.execute(f"SELECT id FROM phones WHERE {col} = ?", (token,)).fetchone()
        if r is None:
            return
        if kind == "FOUND":
            with db.get_conn() as conn:
                p = conn.execute("SELECT lost_since FROM phones WHERE id = ?", (r["id"],)).fetchone()
            if p and p["lost_since"]:
                lost.stop(r["id"], None, "Found it on the phone")
            else:
                restore(r["id"], "Found it on the phone")
        else:
            lost.stop(r["id"], None, "I found it on the phone")
    if SYNC["on"]:
        go()
    else:
        threading.Thread(target=go, daemon=True, name="phones-notification-action").start()


SYNC = {"on": False}                          # tests: handle events at once


# --------------------------------------------------------------------------------------------- what the pages show

def view(conn, p, viewer: dict, now: float) -> dict:
    """The Phone actions card: each action with whether this person may use it now, the owner's choices, the log."""
    ios = phones.is_ios(p)
    allow = allowed_list(p)
    is_owner = viewer["id"] == p["owner_id"]
    on = dnm.active(p, now)
    items = []
    for a, v in ACTIONS.items():
        ok, why = permitted(conn, p, viewer, a)
        if not ok and viewer.get("is_admin") and not is_owner:
            why = "Not allowed on this child's phone yet." if owner_is_child(conn, p) else "Not allowed by the owner."
        if ok and ios and not v["ios"]:
            ok, why = False, "Not on an iPhone."
        if ok and on and not (is_owner and a == "find"):
            ok, why = False, "Not during do-not-monitor hours."
        items.append({"id": a, "label": v["label"], "icon": v["icon"], "ios": v["ios"], "needs": v["needs"],
                      "allowedForOthers": a in allow, "may": ok and bool(settings.get("actions_allowed")), "why": why})
    lost_ok, lost_why = permitted(conn, p, viewer, "lost")
    _, mx = alarm_volume(p["id"])
    return {"on": bool(settings.get("actions_allowed")), "items": items, "ios": ios, "allow": allow,
            "consentIds": list(CONSENT_IDS), "labels": {a: label(a) for a in CONSENT_IDS},
            "defaults": list(DEFAULT_ALLOWED), "fromHa": bool(p["act_from_ha"]), "lostExact": bool(p["lost_exact"]),
            "lostContact": p["lost_contact"] or "", "lostMessage": p["lost_message"] or "",
            "canEditConsent": may_edit_consent(conn, p, viewer), "child": owner_is_child(conn, p),
            "mayLost": lost_ok and bool(settings.get("actions_allowed")), "lostWhy": lost_why,
            "streams": [{"value": v, "label": lbl} for v, lbl in STREAMS],
            "dndModes": [{"value": v, "label": lbl} for v, lbl in DND_MODES],
            "ringerModes": [{"value": v, "label": lbl} for v, lbl in RINGER_MODES],
            "timeouts": list(TIMEOUTS), "volumeMax": mx, "finding": bool(p["find_token"]),
            "log": log_view(conn, p["id"], 30)}


def consent_summary(conn, p) -> str:
    """For "What admins see": which actions others may use."""
    allow = [label(a) for a in allowed_list(p)]
    text = ("Admins may use: " + ", ".join(allow)) if allow else "Admins may use no phone actions"
    if p["act_from_ha"]:
        text += "; Home Assistant automations may ring it"
    return text + " (Phone actions below; every use is in the log)"


def set_consent(conn, p, user: dict, body: dict, now: float) -> None:
    """{allow?: [ids], fromHa?: bool, lostExact?: bool, lostContact?: text, lostMessage?: text} from the owner (or, on
    a child's phone, an admin). Refused when this person may not."""
    if not may_edit_consent(conn, p, user):
        if owner_is_child(conn, p):
            raise Refused(403, "On a child's phone, admins choose this.")
        raise Refused(403, "Only the phone's owner chooses this.")
    keys = {"allow", "fromHa", "lostExact", "lostContact", "lostMessage"}
    if not isinstance(body, dict) or set(body) - keys:
        raise Refused(422, "Send allow, fromHa, lostExact, lostContact or lostMessage.")
    sets, notes = {}, []
    if "allow" in body:
        v = body["allow"]
        if not isinstance(v, list) or any(a not in CONSENT_IDS for a in v):
            raise Refused(422, "Choose from the listed actions.")
        v = [a for a in CONSENT_IDS if a in v]
        if v != allowed_list(p):
            sets["act_allow"] = json.dumps(v)
            notes.append("Others may use: " + (", ".join(label(a) for a in v) or "nothing"))
    for key, col, what in (("fromHa", "act_from_ha", "Ring from Home Assistant automations"),
                           ("lostExact", "lost_exact", "Exact location in Lost mode")):
        if key in body:
            if not isinstance(body[key], bool):
                raise Refused(422, f"{key} is true or false.")
            if int(body[key]) != p[col]:
                sets[col] = int(body[key])
                notes.append(f"{what} " + ("on" if body[key] else "off"))
    for key, col, n in (("lostContact", "lost_contact", 80), ("lostMessage", "lost_message", 200)):
        if key in body:
            v = " ".join(str(body[key] or "").split())[:n] or None
            if v != p[col]:
                sets[col] = v
                notes.append("Lost-mode text changed")
    if sets:
        conn.execute("UPDATE phones SET " + ", ".join(f"{k} = ?" for k in sets) + " WHERE id = ?",
                     (*sets.values(), p["id"]))
        phones.history(conn, p["id"], "owner", f"Phone actions ({user['name']}): " + "; ".join(dict.fromkeys(notes)) + ".",
                       by=user["id"], now=now)


# --------------------------------------------------------------------------------------------- the round

def tick(now: float | None = None, source=None) -> dict:
    """Every 30 seconds: Find my phone's time-out, Lost mode, bedtime, storage and battery health. Blocking."""
    from . import bedtime, health, lost
    now = config.clock() if now is None else now
    out = {"restored": 0, "lost": None, "bedtime": None, "health": None}
    for name, fn in (("restored", lambda: due_restores(now, source)), ("lost", lambda: lost.tick(now, source)),
                     ("bedtime", lambda: bedtime.tick(now, source)), ("health", lambda: health.tick(now))):
        try:
            out[name] = fn()
        except Exception:                                        # noqa: BLE001 — one part failing leaves the rest
            logger.exception("The %s round failed", name)
    return out
