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


GROUPS = [
    Group("files", "Files"),
    Group("messages", "Chats and messages"),
    Group("retention", "Old messages"),
    Group("notifications", "Notifications"),
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
