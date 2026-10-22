"""What the pages show about a phone (spec/SPEC.md §3 My phone, §5 Admin mode, §9 transparency): the summary row,
the owner's page (checklist, what admins see, the log, forwarding), the admin's page (every sensor, history).

A phone whose owner paused being watched shows admins only "Paused by <owner> until …", and one in its owner's
do-not-monitor hours only "Not monitored (until 07:00)": no status, battery, network or sensors (§9). A device no admin
has added shows only that it isn't monitored.
"""
import json

from . import checknow, config, dnm, forwarding, monitor, phones, settings


def ago(ts: float | None, now: float) -> str | None:
    if not ts:
        return None
    m = int(max(0, now - ts) // 60)
    if m < 1:
        return "just now"
    if m < 120:
        return f"{m} min ago"
    if m < 48 * 60:
        return f"{m // 60} h ago"
    return f"{m // 1440} days ago"


def summary(conn, p, now: float, *, viewer: dict) -> dict:
    """One phone as a list row / card."""
    mine = viewer["id"] == p["owner_id"]
    owner = phones.owner_name(conn, p["owner_id"])
    monitored = monitor.runs(p)
    out = {"id": p["id"], "name": phones.display_name(conn, p), "owner": owner, "ownerId": p["owner_id"],
           "mine": mine, "kind": p["kind"], "monitored": monitored, "model": p["model"],
           "manufacturer": p["manufacturer"], "ios": phones.is_ios(p), "inRegistry": bool(p["in_registry"]),
           "forwarding": bool(p["fwd_on"]) and monitored, "welcome": bool(p["welcome"]) and mine and monitored,
           "dnmText": dnm.summary(dnm.windows_of(p)) if monitored and dnm.applies() else None}
    if not monitored:
        out.update(hidden=True, status="unknown", label="Not monitored", paused=None)
        return out
    if monitor.watching_paused(p, now) and not mine:
        out.update(hidden=True, status="paused", label=f"Paused by {phones.first_name(owner)} {monitor.until_text(p)}".strip(),
                   paused={"kind": "watching", "untilText": monitor.until_text(p)})
        return out
    on = dnm.active(p, now)
    if on and not mine:                       # do-not-monitor hours: admins see only this line (§9)
        out.update(hidden=True, status="dnm", label=f"Not monitored (until {on['untilText']})", paused=None,
                   dnm={"untilText": on["untilText"]})
        return out
    d = monitor.display(p, now)
    level, chg = phones.battery(p["id"]), phones.charging(p["id"])
    if level is None:
        level, chg = p["battery"], (None if p["charging"] is None else bool(p["charging"]))
    out.update(hidden=False, status=d["status"], label=d["label"], reason=d["reason"], reasonText=d["reasonText"],
               paused=d["paused"], lastCheckin=p["last_checkin"], lastCheckinText=ago(p["last_checkin"], now),
               battery=level, charging=chg, network=phones.network(p["id"]), lastReboot=phones.last_reboot(p["id"]) or p["last_reboot"],
               appVersion=p["app_version"], osVersion=p["os_version"], osName=p["os_name"],
               intervalMin=p["interval_min"] or settings.get("interval_min"))
    return out


def checklist(conn, p, now: float) -> list[dict]:
    """The owner's setup checklist (§3, §6.1): ok True / False (⚠️) / None (can't tell from here)."""
    items = []
    signed_out = p["request_error_kind"] == "signed_out"
    items.append({"id": "app", "ok": bool(p["in_registry"]) and not signed_out,
                  "title": "Companion app installed and signed in",
                  "help": "Install Home Assistant from the Play Store (or App Store) and sign in to this Home Assistant."})
    remote = None
    if p["request_error_kind"] == "cant_check":
        remote = False
    elif p["answers_requests"]:
        remote = True
    items.append({"id": "remote", "ok": remote, "title": "Remote access working",
                  "help": "The \"please report\" request goes through the Companion app's push service, on Wi-Fi or "
                          "mobile data: Home Assistant must be reachable from outside (Home Assistant Cloud / Nabu "
                          "Casa, or your own remote address), as for the Companion app itself."
                          + (f" Last error: {p['request_error']}" if remote is False and p["request_error"] else "")})
    missing = [label for key, label in phones.NEEDED if phones.sensor_enabled(p["id"], key) is False]
    if p["fwd_on"] and phones.sensor_enabled(p["id"], "last_notification") is False:
        missing.append("Last notification")
    unknown = not phones.entities(p["id"])
    items.append({"id": "sensors", "ok": None if unknown else not missing,
                  "title": "The sensors this app uses are on" + (f" — turn on: {', '.join(missing)}" if missing else ""),
                  "help": "Companion app → Settings → Companion app → Manage sensors: Battery level, Battery state, "
                          "Charging, Last reboot" + (", Last notification (for forwarding)" if p["fwd_on"] else "") + "."})
    if not phones.is_ios(p):
        changing = any(phones.sensor_enabled(p["id"], k) for k, _ in phones.ALWAYS_CHANGING)
        items.append({"id": "changing", "ok": True if (changing or p["answers_requests"]) else (None if unknown else False),
                      "title": "A sensor that changes with every report is on",
                      "help": "So a phone where nothing changed still answers a \"please report\" (the Companion app only "
                              "sends sensors whose value changed): turn on Battery power or Last update trigger in "
                              "Manage sensors. Once the phone has answered a request, this is ticked."})
        items.append({"id": "battery", "ok": False if p["status_reason"] == "answers_only" else None,
                      "title": "Battery use for the Home Assistant app: Unrestricted",
                      "help": "Android: long-press the Home Assistant app → App info → Battery → Unrestricted, so its own "
                              "reports and the requests aren't delayed."})
    if p["fwd_on"]:
        last_msg = conn.execute("SELECT MAX(at) FROM forwards WHERE phone_id = ? AND kind = 'message'", (p["id"],)).fetchone()[0]
        hidden = bool(p["redacted_at"]) and (last_msg is None or last_msg < p["redacted_at"])
        items.append({"id": "android15", "ok": False if hidden else None,
                      "title": "Android 15+: the Home Assistant app may read one-time codes",
                      "help": "Android hides one-time codes from apps that read notifications. Fix it once from a computer "
                              "with ADB: adb shell cmd appops set --user 0 io.homeassistant.companion.android "
                              "RECEIVE_SENSITIVE_NOTIFICATIONS allow — or turn off Enhanced notifications in Android's "
                              "notification settings (that also removes suggested replies). It may need repeating after "
                              "an Android or app update; this is checked again after each hidden code.",
                      "adb": "adb shell cmd appops set --user 0 io.homeassistant.companion.android "
                             "RECEIVE_SENSITIVE_NOTIFICATIONS allow"})
        items.append({"id": "notification_access", "ok": None,
                      "title": "Notification access and the messaging apps allowed",
                      "help": "Companion app → Manage sensors → Last notification: grant notification access, and add your "
                              "messaging apps (Google Messages, Samsung Messages…) to its Allow list."})
        if p["fwd_error"]:
            items.append({"id": "chat", "ok": False, "title": "Household Chat takes the forwards", "help": p["fwd_error"]})
    return items


def admins_see(p) -> list[str]:
    """§9: exactly what admins can see about this phone."""
    if p["pause_kind"] == "watching":
        return ["While you pause being watched: only \"Paused by you\" and until when — nothing else."]
    hours = dnm.summary(dnm.windows_of(p)) if dnm.applies() else None
    out = ["Its status and last check-in (Online, Late, Offline…)", "Battery level and whether it's charging",
           "Wi-Fi network name or mobile data", "Last restart", "The phone's model, Android/iOS version and Companion app "
           "version", "Every sensor the Companion app reports, except notification texts",
           "Its history: status changes, restarts, Check now and pauses",
           "Your last place (Home Assistant zone)" if p["share_place"] else "Not your place (Share my last place is off)"]
    if p["fwd_on"]:
        out.append("That it forwards messages and into which chat — never the messages, unless they're in that chat")
    if hours:
        out.append(f"That you set do-not-monitor hours (\"{hours}\") — they can't change them. During them: only "
                   "\"Not monitored (until …)\" — no status, battery, network or sensors")
    elif dnm.applies():
        out.append("If you set do-not-monitor hours: that they exist and, during them, only \"Not monitored (until …)\"")
    return out


def history(conn, pid: str, limit: int = 200) -> list[dict]:
    return [{"at": r["at"], "atText": config.local(r["at"]).strftime("%a %d %b %H:%M"), "kind": r["kind"],
             "text": r["text"], "battery": r["battery"]}
            for r in conn.execute("SELECT * FROM history WHERE phone_id = ? ORDER BY at DESC, id DESC LIMIT ?", (pid, limit))]


def forwarding_view(conn, p) -> dict:
    last = conn.execute("SELECT at, chat_name FROM forwards WHERE phone_id = ? ORDER BY at DESC LIMIT 1", (p["id"],)).fetchone()
    try:
        members = len(json.loads(p["fwd_members"] or "[]"))
    except ValueError:
        members = 0
    return {"on": bool(p["fwd_on"]), "allowed": bool(settings.get("forwarding_allowed")), "why": forwarding.allowed(p),
            "target": p["fwd_target"], "chatId": p["fwd_chat_id"], "chatName": p["fwd_chat_name"],
            "chatKind": p["fwd_chat_kind"], "members": members + 1 if p["fwd_target"] == "chat" and p["fwd_members"] else None,
            "disappear": p["fwd_disappear"] or settings.get("forward_disappear"), "apps": forwarding.apps_of(p),
            "filter": p["fwd_filter"], "words": forwarding.words_of(p), "allConfirmed": bool(p["fwd_all_confirmed"]),
            "quietStart": p["fwd_quiet_start"], "quietEnd": p["fwd_quiet_end"], "quietMode": p["fwd_quiet_mode"],
            "error": p["fwd_error"], "chats": settings.get("forward_chats"), "groups": settings.get("forward_groups"),
            "groupFilter": settings.get("forward_group_filter"),
            "last": {"at": last["at"], "atText": config.hhmm(last["at"]), "chat": last["chat_name"]} if last else None,
            "appNames": {a: forwarding.app_name(a) for a in forwarding.apps_of(p)}}


def owner_page(conn, p, now: float, viewer: dict) -> dict:
    out = summary(conn, p, now, viewer=viewer)
    out.update(checklist=checklist(conn, p, now), adminsSee=admins_see(p), log=checknow.log(conn, p["id"]),
               alertsLog=[h for h in history(conn, p["id"], 100) if h["kind"] == "alert"][:30],
               dnm=dnm_view(p), welcome=bool(p["welcome"]), sharePlace=bool(p["share_place"]),
               forwarding=forwarding_view(conn, p), stopRequested=bool(p["stop_requested_at"]),
               pause=pause_view(p, now), replaceable=replaceable(conn, p))
    return out


def dnm_view(p) -> dict:
    """The owner's do-not-monitor hours editor: the windows, whether they may set them, the household's limit."""
    out = dnm.view(p)
    on = dnm.active(p, config.clock())
    out["active"] = {"untilText": on["untilText"], "pauseForwarding": on["pauseForwarding"]} if on else None
    return out


def pause_view(p, now: float) -> dict | None:
    if not p["pause_kind"] or not (monitor.watching_paused(p, now) or monitor.alerts_paused(p, now)):
        return None
    return {"kind": p["pause_kind"], "until": p["pause_until"], "untilOnline": bool(p["pause_until_online"]),
            "untilText": monitor.until_text(p), "reason": p["pause_reason"]}


def replaceable(conn, p) -> list[dict]:
    """The owner's other (older) phones this one could replace."""
    if not p["owner_id"]:
        return []
    rows = conn.execute("SELECT * FROM phones WHERE owner_id = ? AND id != ? AND retired = 0 AND first_seen < ? "
                        "ORDER BY first_seen", (p["owner_id"], p["id"], p["first_seen"])).fetchall()
    return [{"id": r["id"], "name": phones.display_name(conn, r)} for r in rows]


def admin_page(conn, p, now: float, viewer: dict) -> dict:
    out = summary(conn, p, now, viewer=viewer)
    out.update(haName=p["ha_name"], ownerByAdmin=bool(p["owner_by_admin"]), notifyService=p["notify_service"],
               defaultService=phones.LIVE.default_service.get(p["id"]), firstSeen=p["first_seen"],
               intervalOwn=p["interval_min"], forwardingTarget=None, sharePlace=bool(p["share_place"]),
               stopRequested=bool(p["stop_requested_at"]), pause=pause_view(p, now))
    if p["fwd_on"]:
        out["forwardingTarget"] = forwarding.target_name(p)
    out["offlineAlert"] = monitor.spell_view(p, now)
    if out.get("hidden"):
        out["offlineAlert"]["counting"] = None           # nothing about its status while hidden (§9)
        keep = ("pause", "found", "owner") if out["monitored"] else ()
        out.update(sensors=None, history=[h for h in history(conn, p["id"]) if h["kind"] in keep] if keep else [])
        return out
    out.update(sensors=phones.sensors(p["id"], bool(p["share_place"])), history=history(conn, p["id"]),
               checkinDetails=monitor.diagnostics(p, now),
               checks=checknow.log(conn, p["id"], 20), zone=phones.zone(p["id"]) if p["share_place"] else None)
    return out
