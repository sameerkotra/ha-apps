"""One-tap phone setup and Test everything (spec/SPEC.md §15.7), for the owner on My phone.

- **Set up this phone:** a link (and the same link as a QR code, drawn in the page by static/qr.js — no outside
  service) that opens this app's My phone in the Home Assistant app: `homeassistant://navigate/<the app's sidebar
  page>` (the Companion app's own deep link, Android and iOS); and the My Home Assistant link
  `https://my.home-assistant.io/redirect/supervisor_ingress/?addon=<slug>` for a browser. The Companion app has no
  deep links to its own settings or sensor pages, so those steps are written out.
- **Test everything:** the setup checklist, a Check now, a test forward into the owner's 📌 My room (when forwarding
  is on) and a test notification to the phone → a pass / fail list (the Check now's result follows on the page).
"""
from . import actions, app_messages, checknow, config, db, phones, settings, sidebar

TEST_GAP_S = 30
MY_LINK = "https://my.home-assistant.io/redirect/supervisor_ingress/?addon={}"

ANDROID_STEPS = [
    "Open the Home Assistant app on the phone → Settings → Companion app → Manage sensors.",
    "Turn on: Battery level, Battery state, Charging, Last reboot and Last update trigger (check-ins); Interactive "
    "and Device locked (Lost mode, bedtime); Storage, Battery health and Current version (the Health card); Last "
    "notification (only for forwarding).",
    "Android settings → Apps → Home Assistant → Battery → Unrestricted.",
    "For Find my phone, Ringer, Volume and Do Not Disturb: Android settings → Notifications → Do Not Disturb → Apps "
    "(or \"Do Not Disturb access\") → Home Assistant → Allow. The phone also asks the first time one is used.",
    "For Open an app and Open a dashboard: Android settings → Apps → Home Assistant → Display over other apps → Allow.",
    "For Brightness and Screen timeout: Android settings → Apps → Home Assistant → Modify system settings → Allow.",
]
IOS_STEPS = [
    "Open the Home Assistant app on the iPhone → Settings → Companion App → Sensors: turn on Battery and Storage.",
    "For Find my phone: iPhone Settings → Notifications → Home Assistant → allow Critical Alerts.",
]


def setup_view(p) -> dict:
    page = sidebar.PAGE if isinstance(sidebar.PAGE, str) and sidebar.PAGE.startswith("/") else None
    slug = page[1:] if page else config.SLUG
    return {"appLink": f"homeassistant://navigate{page}" if page else None, "myLink": MY_LINK.format(slug),
            "steps": IOS_STEPS if phones.is_ios(p) else ANDROID_STEPS}


def test_everything(pid: str, user: dict, *, now: float | None = None, source=None) -> dict:
    """{items: [{id, title, ok: True | False | None, detail}], check: Check now's id or None}. Raises
    actions.Refused (429 when run again within 30 seconds)."""
    from . import views
    now = config.clock() if now is None else now
    with db.get_conn() as conn:
        p = phones.get(conn, pid)
        if p is None or p["owner_id"] != user["id"]:
            raise actions.Refused(403, "Only the phone's owner runs Test everything.")
        last = conn.execute("SELECT MAX(at) FROM actions WHERE phone_id = ? AND action = 'test'", (pid,)).fetchone()[0]
        if last is not None and now - last < TEST_GAP_S:
            raise actions.Refused(429, "Test everything ran a moment ago — wait half a minute.")
        items = [{"id": f"checklist:{c['id']}", "title": c["title"], "ok": c["ok"],
                  "detail": None if c["ok"] else c["help"]} for c in views.checklist(conn, p, now)]
        name = phones.display_name(conn, p)
    cid = None
    try:
        cid = checknow.start(pid, user, source=source, now=now)
        items.append({"id": "check", "title": "Check now", "ok": None, "detail": "Asking the phone…"})
    except Exception as e:                                      # noqa: BLE001 — shown as a failed step
        items.append({"id": "check", "title": "Check now", "ok": False,
                      "detail": getattr(e, "detail", None) or str(e) or "It couldn't be started."})
    if p["fwd_on"] and settings.get("forwarding_allowed"):
        ok = app_messages.chat_ready() or app_messages.POST["fn"] is not None
        if ok:
            ok = app_messages.post_forward(pid, {"requested_by": user["id"], "my_room": True,
                                                 "label": f"📱 {name} · test · {config.hhmm(now)}",
                                                 "text": "A test forward from Test everything. Your forwarded "
                                                         "messages look like this.", "disappear_after": "1h"})
        items.append({"id": "forward", "title": "A test forward into your 📌 My room", "ok": bool(ok),
                      "detail": "Sent — look in Household Chat." if ok else
                      "Household Chat isn't available (not installed, not running, or app messages are off)."})
    else:
        items.append({"id": "forward", "title": "A test forward", "ok": None,
                      "detail": "Forwarding is off for this phone: nothing to test."})
    res = actions.send(p, [{"title": "📱 Household Phones", "message": "Test everything: this phone gets "
                            "notifications from Household Phones."}], source)
    items.append({"id": "notification", "title": "A test notification on this phone", "ok": res.ok,
                  "detail": "Sent — it should show on the phone now." if res.ok else res.reason})
    passed = sum(1 for i in items if i["ok"] is True)
    failed = sum(1 for i in items if i["ok"] is False)
    with db.get_conn() as conn:
        actions.log(conn, pid, "test", "", user, user["name"], "app", failed == 0,
                    f"{passed} passed, {failed} to fix", now)
    return {"items": items, "check": cid, "passed": passed, "failed": failed}
