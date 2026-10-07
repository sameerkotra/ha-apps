"""App settings (Admin → App settings, SPEC §3.1). Stored in `app_settings` as
JSON, validated, read through a short cache so changes apply without a restart.
Built on the shared registry (common/python/settings_core.py)."""
import os
import re

from pydantic import ValidationError

from . import config, db
from .common import settings_core
from .common.settings_core import Group, Setting, SettingsError  # noqa: F401  (SettingsError: the route's name)


def _exts(v: str) -> str:
    parts = [p.strip().lower().lstrip(".") for p in v.split(",") if p.strip()]
    for p in parts:
        if not re.fullmatch(r"[a-z0-9]{1,10}", p):
            raise ValueError(f"“{p}” isn't a file extension (letters and digits only).")
    return ",".join(dict.fromkeys(parts))


def _one_line(v: str) -> str:
    v = (v or "").strip()
    if any(c in v for c in "\r\n"):
        raise ValueError("One line.")
    return v


def _stun(v: str) -> str:
    v = (v or "").strip()
    if v and not re.fullmatch(r"stuns?:[A-Za-z0-9.\-]+(:\d{1,5})?", v):
        raise ValueError("A STUN address looks like stun:stun.cloudflare.com:3478")
    return v


def _turn(v: str) -> str:
    parts = [p.strip() for p in (v or "").split(",") if p.strip()]
    for p in parts:
        if not re.fullmatch(r"turns?:[A-Za-z0-9.\-]+(:\d{1,5})?(\?transport=(udp|tcp))?", p):
            raise ValueError("A TURN address looks like turn:home.example.com:3478 or turns:home.example.com:443")
    return ",".join(parts)


def _prepare(partial: dict, current: dict, flags: dict) -> dict:
    """A blank secret keeps the saved one; clear_<key> removes it."""
    for key in SECRETS:
        if key in partial and not (partial[key] or "").strip():
            partial.pop(key)
        if flags.get(f"clear_{key}"):
            partial[key] = ""
    return partial


def _check(merged: dict, partial: dict, current: dict) -> None:
    if merged["calls_relay"] == "cloudflare" and not (merged["calls_cf_key_id"] and merged["calls_cf_api_token"]):
        raise SettingsError("calls_relay: the Cloudflare relay needs its TURN key id and API token.")
    if merged["calls_relay"] == "turn" and not (merged["calls_turn_url"] and merged["calls_turn_secret"]):
        raise SettingsError("calls_relay: your own relay needs its address and shared secret.")


SECRETS = ("calls_cf_api_token", "calls_turn_secret")

GROUPS = [
    Group("files", "Files"),
    Group("messages", "Chats and messages"),
    Group("retention", "Old messages"),
    Group("notifications", "Notifications"),
    Group("calls", "Voice calls", "One-to-one calls in direct chats. The sound goes straight between the two "
          "phones. At home nothing needs setting up; for calls away from home add an address lookup (STUN) and, "
          "for networks that block direct connections (common on mobile data), a call relay."),
    Group("assistant", "Household Assistant", "The Household Assistant app answers questions from what the household "
          "apps know. Chat tells it only which chats have unread messages and how many — never what anyone wrote."),
]

SETTINGS = [
    # checked by files.clean_files_path (the route) — the page checks the folder before saving
    Setting("files_path", "/" + os.path.normpath(config.SHARE_DIR).lstrip("/"), "Chat files folder", group="files",
            min_length=1, max_length=400,
            help="Where the files, photos and voice messages shared in chats are kept, inside /share — for example "
                 "/share/nas/household_chat on network storage. The app creates the folder and what it needs inside "
                 "it. Changing it doesn't move any files: copy the whole old folder, including .household_chat_store, "
                 "to the new place first."),
    Setting("max_upload_mb", 50, "Largest file (MB)", group="files", min=1, max=1024),
    Setting("share_folder_quota_gb", 0, "Limit for the chat folder in /share (GB, 0 = no limit)", group="files",
            min=0, max=10000),
    Setting("who_can_create_groups", "everyone", "Who can create groups", group="messages",
            choices=[("everyone", "Everyone"), ("admins", "Admins only")]),
    Setting("edit_window_minutes", 15, "Messages can be edited for (minutes, 0 = never)", group="messages",
            min=0, max=1440),
    Setting("message_retention_days", 0, "Delete messages older than (days, 0 = keep forever)", group="retention",
            min=0, max=36500),
    Setting("keep_files_on_retention", True, "Keep the files of messages deleted by age", group="retention"),
    Setting("retention_includes_personal", False, "Also delete old notes in personal rooms", group="retention"),
    Setting("notify_preview", "full", "Notification preview (the most anyone's phone shows)", group="notifications",
            choices=[("full", "Sender and text"), ("sender", "Sender only"), ("none", "Nothing")]),
    Setting("blocked_extensions", "exe,bat,cmd,com,scr,msi,ps1,vbs,js,jar,apk,html,htm,svg",
            "Refused file types (comma-separated extensions)", group="files", max_length=1000, validators=[_exts]),
    Setting("photo_max_px", 2000, "Make photos smaller than (pixels on the longest side, 0 = never)", group="files",
            min=0, max=10000),
    Setting("voice_max_seconds", 300, "Longest voice message (seconds)", group="files", min=10, max=900),
    Setting("show_presence", True, "Show home / away from Home Assistant", group="messages"),
    Setting("notification_reply", True, "Offer Reply and Mark as read on phone notifications", group="notifications"),
    Setting("who_can_announce", "admins", "Who can post announcements", group="messages",
            choices=[("admins", "Admins"), ("admins_and_group_admins", "Admins, and group admins in their groups")]),
    Setting("children_can_message_each_other", False, "Children can start direct chats with each other",
            group="messages"),
    Setting("calls_enabled", False, "Voice calls", group="calls",
            help="Show a 📞 Call button in direct chats. Calls need the app opened over https (the microphone)."),
    Setting("calls_ring_seconds", 30, "Ring for (seconds)", group="calls", min=15, max=60, show_if="calls_enabled"),
    Setting("calls_stun", "", "Address lookup (STUN) server", group="calls", max_length=200, validators=[_stun],
            show_if="calls_enabled", placeholder="Empty: home network only",
            help="Lets phones away from home find each other. Cloudflare's is free and needs no account: "
                 "stun:stun.cloudflare.com:3478. Only the phones' network addresses go to it, never sound."),
    Setting("calls_relay", "none", "Call relay (TURN)", group="calls", show_if="calls_enabled",
            choices=[("none", "None"), ("cloudflare", "Cloudflare Realtime TURN"), ("turn", "My own TURN server")],
            help="Carries the sound when the phones can't connect directly. Cloudflare: make a TURN key under "
                 "Realtime → TURN in your Cloudflare account (free up to a large monthly allowance) and enter its key "
                 "id and API token. Own server: e.g. a coturn with a router port forwarded; enter its address and "
                 "shared secret. A Cloudflare Tunnel doesn't carry call sound, so the relay is separate."),
    Setting("calls_cf_key_id", "", "Cloudflare TURN key id", group="calls", max_length=100, validators=[_one_line],
            show_if="calls_enabled"),
    Setting("calls_cf_api_token", "", "Cloudflare TURN API token", group="calls", secret=True, max_length=200,
            validators=[_one_line], show_if="calls_enabled", page={"clearFlag": "clear_calls_cf_api_token"}),
    Setting("calls_turn_url", "", "TURN server address", group="calls", max_length=200, validators=[_turn],
            show_if="calls_enabled", placeholder="turn:home.example.com:3478",
            help="turn: or turns: (turns: on port 443 also works on networks that allow only web traffic). "
                 "Several, separated by commas."),
    Setting("calls_turn_secret", "", "TURN shared secret", group="calls", secret=True, max_length=200,
            validators=[_one_line], show_if="calls_enabled", page={"clearFlag": "clear_calls_turn_secret"},
            help="coturn's static-auth-secret: the app makes a short-lived password for each call from it."),
    Setting("assistant_answers", True, "Answer the Household Assistant", group="assistant",
            help="Lets the Household Assistant tell a person their unread counts, as they'd see them here. Each "
                 "person can still turn it off for themselves in Settings."),
    Setting("export_max_mb", 500, "Largest chat download, files included (MB)", group="files", min=10, max=10000),
]


def first_error(e: ValidationError) -> str:
    """The first problem, as "<key>: <message>"."""
    err = e.errors()[0]
    return f"{'.'.join(str(x) for x in err.get('loc', []))}: {err.get('msg')}"


def unknown_message(keys) -> str:
    return f"Unknown setting: {sorted(keys)[0]}"


REGISTRY = settings_core.Registry(
    SETTINGS, groups=GROUPS, connect=db.get_conn, format_error=first_error, unknown_message=unknown_message,
    load_check="type", cache_ttl=5, write_all=True,
    flags={f"clear_{k}": f"clear_{k}: must be true or false" for k in SECRETS}, prepare=_prepare, check=_check,
    write=lambda conn, key, value, who, now: db.set_setting(conn, key, value, who),
    log=lambda *a: None)

AppSettings = REGISTRY.model
DEFAULTS = REGISTRY.defaults
META = REGISTRY.meta()


def invalidate() -> None:
    REGISTRY.invalidate()


def all_values(conn=None) -> dict:
    return REGISTRY.all(conn)


def get(key: str, conn=None):
    return REGISTRY.get(key, conn)


def blocked_extensions(conn=None) -> set:
    return {p for p in get("blocked_extensions", conn).split(",") if p}


def update(values: dict, by: str | None = None) -> dict:
    """Validate {current values + values} and store every key. Raises SettingsError."""
    return REGISTRY.update(values, by)[0]


def payload() -> dict:
    return REGISTRY.payload()
