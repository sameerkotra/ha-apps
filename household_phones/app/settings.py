"""App settings (Admin → App settings), stored in the `app_settings` table (spec/SPEC.md §7).

Admins change them inside the app, without a restart. Per-phone choices (its own interval, kind, owner) are on the
phone's page; the owner's choices (forwarding, do-not-monitor hours, pauses) on My phone. Only `admin_users` stays on the
app's Configuration tab.
"""
import logging
import re
import threading

from . import config, db
from .common import settings_core
from .common.settings_core import Group, Setting, SettingsError  # noqa: F401  (SettingsError: the routes' name)

logger = logging.getLogger("settings")

HHMM = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")
DEFAULT_APPS = ["com.google.android.apps.messaging", "com.samsung.android.messaging"]
DISAPPEAR = (("1h", "1 hour"), ("1d", "1 day"), ("7d", "7 days"), ("30d", "30 days"))
RECIPIENTS = (("admins", "Admins"), ("owner", "The phone's owner (their other devices)"),
              ("both", "The owner and admins"))


def _time_or_blank(v: str) -> str:
    if v and not HHMM.match(v):
        raise ValueError("Use a time like 22:00 (24-hour), or leave it empty.")
    return v


def _time(v: str) -> str:
    if not HHMM.match(v or ""):
        raise ValueError("Use a time like 22:00 (24-hour).")
    return v


def _packages(v: list) -> list:
    out = []
    for p in v:
        p = p.strip()
        if not re.fullmatch(r"[A-Za-z0-9_.]{2,120}", p):
            raise ValueError(f"{p!r} isn't an app's package name (like com.google.android.apps.messaging).")
        if p not in out:
            out.append(p)
    if len(out) > 30:
        raise ValueError("At most 30 apps.")
    return out


def _names(v: list) -> list:
    out = [" ".join(x.split())[:80] for x in v if x.strip()]
    if len(out) > 30:
        raise ValueError("At most 30 groups.")
    return out


def _delivery(v: list) -> list:
    v = [x for x in dict.fromkeys(v)]
    if any(x not in ("phones", "ha", "chat") for x in v):
        raise ValueError("Choose phones, a Home Assistant notification or Household Chat.")
    if not v:
        raise ValueError("Choose at least one way to deliver alerts.")
    return v


def _interval(v: int) -> int:
    if v not in (5, 10, 15):
        raise ValueError("Choose 15, 10 or 5 minutes.")
    return v


OFFLINE_DELAYS = ((0, "As soon as it's Offline"), (30, "30 minutes"), (60, "1 hour"), (120, "2 hours"), (240, "4 hours"),
                  (360, "6 hours"), (600, "10 hours"), (720, "12 hours"), (1440, "24 hours"))


def _offline_delay(v: int) -> int:
    if v not in dict(OFFLINE_DELAYS):
        raise ValueError("Choose one of the listed times.")
    return v


def delay_label(minutes: int) -> str:
    return dict(OFFLINE_DELAYS).get(minutes, f"{minutes} minutes")


VERSION_RE = re.compile(r"^\d{1,4}(\.\d{1,4}){1,3}$")


def _version_or_blank(v: str) -> str:
    if v and not VERSION_RE.match(v):
        raise ValueError("A version like 2026.10.1, or leave it empty.")
    return v


def _one_line(v: str) -> str:
    if "\n" in v or "\r" in v:
        raise ValueError("One line only.")
    return v


GROUPS = [
    Group("phones", "📱 Phones", "Devices come from the Home Assistant Companion app. None is monitored until an admin "
                                "adds it (All phones → Available devices); its kind, owner and own interval are on "
                                "its page."),
    Group("checkin", "⏱️ Check-in", "Each phone reports to Home Assistant by itself; the app asks only a phone that is "
                                    "overdue."),
    Group("checknow", "🔎 Check now", "A \"please respond\" sent on demand."),
    Group("alerts", "🔔 Alerts", "Each alert once per phone per cooldown; never to the phone it is about (except the "
                                "charging reminder, if you choose)."),
    Group("forwarding", "💬 Forwarding", "A phone's messages into Household Chat. Only the phone's owner can switch it on "
                                        "(My phone)."),
    Group("actions", "📲 Phone actions", "Find my phone, Lost mode and the other actions on a phone. Each phone's owner "
                                         "chooses on My phone which ones others may use (admins decide for children's "
                                         "phones); every use is in the phone's history."),
    Group("bedtime", "🧒 Children's bedtime", "Bedtime hours are set per child's phone on its page (All phones)."),
    Group("transparency", "👀 Transparency", "Watching someone's phone is sensitive: everyone sees what admins see about "
                                            "their phone, and who checked it."),
    Group("history", "🗂️ History"),
    Group("ha", "🏠 Home Assistant", "Sensors for dashboards and automations, and the Household Assistant."),
]

SETTINGS = [
    # -- phones
    Setting("guess_kind", True, "Guess the kind of new devices", group="phones",
            help="Tablets and watches are recognised by their model and shown under Available devices; you choose the "
                 "kind when you add one to monitoring. Off: every device is shown as a phone."),
    Setting("min_app_version", "", "Minimum Home Assistant app version", group="phones",
            validators=[_version_or_blank], placeholder="2026.10.1",
            help="Optional. A phone with an older Companion app is shown as \"HA app outdated\" (All phones, its page "
                 "and its owner's checklist). Without it, a phone is outdated when another phone of the same kind of "
                 "system in the household has a newer one."),
    Setting("unused_days", 30, "\"Not used lately\" after", group="phones", min=3, max=365, unit="days",
            help="A device that hasn't checked in for this long is listed under Not used lately, with Remove or Keep. "
                 "Nothing is removed by itself."),
    # -- check-in
    Setting("interval_min", 15, "Check-in interval", group="checkin", type=int, validators=[_interval],
            choices=[(15, "15 minutes"), (10, "10 minutes"), (5, "5 minutes")],
            help="Keep the phones on the Companion app's Sensor update frequency \"Normal\" (every 15 minutes, lightest "
                 "on the battery): at 15 minutes nothing else is needed. At 10 or 5 minutes the app asks a phone on "
                 "Normal to report once or twice between its own reports; a phone set to \"Fast always\" reports every "
                 "minute (more battery) and is never asked. A phone can have its own interval (its page)."),
    Setting("ask_after_min", 2, "Ask after", group="checkin", min=1, max=30, unit="minutes overdue",
            help="How long past the interval a phone may be quiet before it is asked to report (Late)."),
    Setting("wait_reply_min", 5, "Wait for a reply", group="checkin", min=1, max=30, unit="minutes",
            help="How long to wait before asking again."),
    Setting("requests", 2, "Requests before Offline", group="checkin", min=1, max=5,
            help="A phone is Offline after this many unanswered requests: at the defaults, 27 minutes without a "
                 "check-in."),
    Setting("startup_grace_min", 12, "Start-up grace", group="checkin", min=0, max=120, unit="minutes after one interval",
            help="After Home Assistant or this app starts, no phone is marked Offline (and no offline alert goes out) "
                 "until one interval plus this much has passed."),
    Setting("lost_contact_on", True, "Recognise lost contact", group="checkin",
            help="When Home Assistant can't send the \"please report\" requests to half or more of the monitored "
                 "phones, which went quiet within a few minutes, the problem is at home (Home Assistant's internet, or "
                 "Nabu Casa / the push service): one alert to admins instead of one per phone. A phone counts only once "
                 "it has been offline for its own Alert-when-offline time. Phones that go quiet while the requests "
                 "still go through are just asleep: no lost-contact alert, their own offline alerts as usual."),
    Setting("lost_contact_window_min", 10, "…within", group="checkin", min=2, max=60, unit="minutes",
            show_if="lost_contact_on"),
    Setting("lost_contact_min_phones", 2, "…and at least", group="checkin", min=2, max=20, unit="phones",
            show_if="lost_contact_on"),
    Setting("lost_contact_quiet_start", "", "Don't check for lost contact between", group="checkin",
            validators=[_time_or_blank], placeholder="00:00", show_if="lost_contact_on",
            help="Home Assistant's time zone. Empty: always check."),
    Setting("lost_contact_quiet_end", "", "…and", group="checkin", validators=[_time_or_blank], placeholder="07:00",
            show_if="lost_contact_on"),
    # -- check now
    Setting("check_wait_s", 60, "Wait for a reply", group="checknow", min=15, max=180, unit="seconds"),
    Setting("check_per_hour", 20, "At most per phone", group="checknow", min=1, max=120, unit="an hour",
            help="Keeps a stuck button or automation from waking a phone all day. Presses within 30 seconds join the "
                 "check already running."),
    Setting("check_by_chat_members", True, "People in a phone's forwarding chat may check it", group="checknow",
            help="Besides its owner and admins. They see only the Check-now result (status, battery, network, last "
                 "forward time), not the phone's details."),
    # -- alerts
    Setting("alert_offline", True, "Offline", group="alerts",
            help="When a phone becomes Offline, with its battery level at the last check-in."),
    Setting("alert_offline_to", "admins", "…to", group="alerts", choices=RECIPIENTS, show_if="alert_offline"),
    Setting("offline_alert_after_min", 0, "Alert when offline for at least", group="alerts", type=int,
            validators=[_offline_delay], choices=OFFLINE_DELAYS, show_if="alert_offline",
            help="The household's default; each phone can have its own (its page in Admin mode). The phone is marked "
                 "Offline as usual, only the alert waits, and none goes out if it comes back sooner. The owner's "
                 "do-not-monitor hours and pauses don't count. App not reporting alerts wait the same time."),
    Setting("alert_app", True, "App not reporting", group="alerts",
            help="The phone is probably on but the Companion app can't report (signed out, a sensor off, battery "
                 "optimisation). Sent once it has lasted longer than the Offline time, and the phone's Alert-when-offline time."),
    Setting("alert_app_to", "both", "…to", group="alerts", choices=RECIPIENTS, show_if="alert_app"),
    Setting("alert_lost_contact", True, "Lost contact with all phones", group="alerts",
            help="To admins, as a Home Assistant notification (needs no internet) and on their phones."),
    Setting("alert_back", False, "Back online", group="alerts",
            help="When a phone that was Offline or App not reporting reports again. With an Alert-when-offline time, "
                 "only if its alert had gone out."),
    Setting("alert_back_to", "admins", "…to", group="alerts", choices=RECIPIENTS, show_if="alert_back"),
    Setting("alert_restart", True, "Restarted", group="alerts",
            help="With a guess: \"battery was 3%, probably ran out\" or \"probably an update\"."),
    Setting("alert_restart_to", "admins", "…to", group="alerts", choices=RECIPIENTS, show_if="alert_restart"),
    Setting("alert_battery", True, "Battery low", group="alerts", help="Once per discharge, while not charging."),
    Setting("alert_battery_to", "owner", "…to", group="alerts", choices=RECIPIENTS, show_if="alert_battery"),
    Setting("battery_low_pct", 15, "…below", group="alerts", min=1, max=60, unit="%", show_if="alert_battery"),
    Setting("alert_charge", False, "Charging reminder at night", group="alerts",
            help="At a set time, for each phone below a level and not charging: \"Asha's phone is at 25% and not "
                 "charging\"."),
    Setting("alert_charge_to", "owner", "…to", group="alerts", choices=RECIPIENTS, show_if="alert_charge"),
    Setting("charge_time", "22:00", "…at", group="alerts", validators=[_time], placeholder="22:00", show_if="alert_charge"),
    Setting("charge_pct", 30, "…below", group="alerts", min=5, max=90, unit="%", show_if="alert_charge"),
    Setting("charge_to_phone", False, "…also to the phone itself", group="alerts", show_if="alert_charge",
            help="It is still on, so it can show the reminder."),
    Setting("alert_storage", True, "Storage almost full", group="alerts",
            help="From the phone's Storage sensor (Internal storage): once when its free space drops below the level, "
                 "again only after it has recovered."),
    Setting("alert_storage_to", "owner", "…to", group="alerts", choices=RECIPIENTS, show_if="alert_storage"),
    Setting("storage_low_pct", 10, "…below", group="alerts", min=1, max=50, unit="% free", show_if="alert_storage"),
    Setting("alert_battery_health", True, "Battery health", group="alerts",
            help="From the phone's Battery health sensor: when it is worse than Good (overheated, cold, dead, over "
                 "voltage, failed), once per change."),
    Setting("alert_battery_health_to", "owner", "…to", group="alerts", choices=RECIPIENTS, show_if="alert_battery_health"),
    Setting("alert_last_place", False, "Last place in the offline alert", group="alerts",
            help="Adds the phone's last Home Assistant zone (\"last seen at Work\"), only for phones whose owner allows "
                 "it (Share my last place). Zone names only, never coordinates."),
    Setting("alert_cooldown_min", 30, "Cooldown", group="alerts", min=5, max=1440, unit="minutes per phone per alert"),
    Setting("alert_delivery", ["phones"], "Deliver alerts by", group="alerts", validators=[_delivery], type=list[str],
            choices=[("phones", "The recipients' phones"), ("ha", "A Home Assistant notification"),
                     ("chat", "Household Chat (each recipient's 📌 My room)")]),
    Setting("quiet_start", "", "Quiet hours from", group="alerts", validators=[_time_or_blank], placeholder="23:00",
            help="For every recipient who hasn't set their own (My phone). Alerts are held until the end. Empty: none."),
    Setting("quiet_end", "", "…until", group="alerts", validators=[_time_or_blank], placeholder="07:00"),
    # -- forwarding
    Setting("forwarding_allowed", True, "Forwarding allowed in this household", group="forwarding",
            help="Off: no phone forwards, whatever its owner chose."),
    Setting("forward_disappear", "1h", "Forwarded messages disappear after", group="forwarding", choices=DISAPPEAR,
            help="The default; each owner can choose for their own forwards."),
    Setting("forward_chats", "member", "Owners may forward into", group="forwarding",
            choices=[("own_room", "Their own 📌 My room only"), ("member", "Any chat they belong to"),
                     ("listed", "Their My room, or the groups listed below")]),
    Setting("forward_groups", [], "Groups owners may choose", group="forwarding", validators=[_names],
            help="Group names as Household Chat shows them, one per line.", show_if=None),
    Setting("forward_apps", list(DEFAULT_APPS), "Messaging apps forwarded by default", group="forwarding",
            validators=[_packages],
            help="Android package names. Google Messages is com.google.android.apps.messaging, Samsung Messages "
                 "com.samsung.android.messaging. Owners can change their own list."),
    Setting("forward_group_filter", "codes", "Starting filter for a group", group="forwarding",
            choices=[("codes", "Codes only"), ("all", "Everything")],
            help="Suggested when an owner picks a group, so ordinary texts aren't forwarded by accident."),
    # -- phone actions
    Setting("actions_allowed", True, "Phone actions", group="actions",
            help="Find my phone, Screen on, Flashlight, Ringer, Do Not Disturb, Volume, Brightness, Screen timeout, Open "
                 "an app or a dashboard, and Lost mode — on Android (iPhones: Find my phone, as a critical alert). Off: "
                 "none of them, for anyone."),
    Setting("find_per_hour", 20, "Find my phone at most", group="actions", min=1, max=60, unit="times per phone an hour",
            show_if="actions_allowed", help="And at most once every 30 seconds."),
    Setting("actions_per_hour", 30, "Other actions at most", group="actions", min=1, max=120,
            unit="per phone an hour", show_if="actions_allowed", help="And each at most once every 5 seconds."),
    Setting("find_restore_min", 5, "Put the alarm volume back after", group="actions", min=1, max=60, unit="minutes",
            show_if="actions_allowed",
            help="Find my phone turns the alarm volume up (and Do Not Disturb off, if it was on); \"Found it\" on the "
                 "phone puts them back at once, otherwise after this time."),
    Setting("lost_ring_min", 10, "Lost mode rings every", group="actions", min=2, max=120, unit="minutes",
            show_if="actions_allowed", help="The default; whoever starts Lost mode can choose."),
    Setting("lost_location_min", 5, "Lost mode asks for the location every", group="actions", min=1, max=60,
            unit="minutes", show_if="actions_allowed",
            help="The default. Frequent location updates use more battery (the Companion app's documentation warns "
                 "about this)."),
    # -- bedtime
    Setting("bedtime_after_min", 5, "Tell the parents when the screen is in use at bedtime for more than", group="bedtime",
            min=1, max=120, unit="minutes",
            help="From the phone's Interactive sensor. One quiet alert per night per phone, to the parents chosen on the "
                 "phone's page."),
    # -- transparency
    Setting("adults_may_pause", True, "Adults may pause being watched", group="transparency",
            help="While paused, no requests are sent, no alerts go out, and admins see only \"Paused by <name> until "
                 "18:00\". Children's phones can't be paused this way."),
    Setting("pause_max_hours", 24, "Longest pause", group="transparency", min=1, max=168, unit="hours",
            show_if="adults_may_pause"),
    Setting("dnm_allowed", True, "Owners may set do-not-monitor hours", group="transparency",
            help="Each owner can choose hours of the week when their phone isn't monitored (e.g. weekdays 22:00–07:00): "
                 "no requests, no alerts, and admins see only \"Not monitored (until 07:00)\" and that the hours exist. "
                 "Admins can't change them. Children's phones can't have them. Off: no phone's hours apply."),
    Setting("dnm_max_hours", 12, "…at most", group="transparency", min=1, max=20, unit="hours a day",
            show_if="dnm_allowed",
            help="So a phone can't be left unmonitored all day. Shown in the owner's editor."),
    # -- history
    Setting("history_days", 7, "Keep history", group="history", min=1, max=90, unit="days",
            help="Status changes, restarts, Check now and pauses on each phone's page; the log on My phone."),
    Setting("online_history_days", 30, "Keep the online history", group="history", min=1, max=30, unit="days",
            help="The coloured online line on each phone's page (24 hours, 7 days, 30 days) and in All phones: only "
                 "when its status changed, so it stays small. The line shows at most 30 days."),
    # -- Home Assistant
    Setting("ha_sensors", False, "Sensors in Home Assistant", group="ha",
            help="Publishes binary_sensor.phones_<phone>_online and sensor.phones_online_count, and listens for the "
                 "household_phones_check_now event (Check now from a script or automation)."),
    Setting("ha_find_event", False, "Ring phones from Home Assistant", group="ha",
            help="Listens for the household_phones_find event (a script, a dashboard button on a wall tablet). With the "
                 "person who pressed it: as if they pressed Find my phone here. Without one: only phones whose owner "
                 "allows \"from Home Assistant automations\"."),
    Setting("assistant_answers", True, "Answer the Household Assistant", group="ha",
            help="\"Is my phone online?\", \"check Asha's phone\": answered as the person could see it here."),
    # app-message signing (shared app_bus): the same key in every household app; empty = unsigned, as before
    Setting("bus_signing_key", "", "App-messages signing key", group="ha", secret=True, max_length=200,
            validators=[_one_line],
            help="The same key in every household app (Admin → App settings there). Without one, any app on Home "
                 "Assistant's network can send messages as any other."),
]

readable = settings_core.by_key

REGISTRY = settings_core.Registry(
    SETTINGS, groups=GROUPS, connect=db.get_conn, model_config={"str_strip_whitespace": True},
    format_error=readable, unknown_message=lambda keys: f"Unknown setting(s): {', '.join(sorted(keys))}",
    not_object_message="Expected a JSON object of settings.", load_check="isinstance",
    now=lambda: config.iso(), generation=lambda: db.generation, secret_values="omit",
    write_lock=threading.Lock(), logger=logger)

DEFAULTS = REGISTRY.defaults


def get(key: str):
    return REGISTRY.get(key)


def payload() -> dict:
    return REGISTRY.payload(REGISTRY.all())


def update(partial: dict, user: dict | None) -> list[str]:
    """Validate {current values + partial} and store what changed; the changed keys. Raises SettingsError."""
    who = user.get("id") if user else None
    return REGISTRY.update(partial, who)[1]
