"""App settings (Admin → App settings, SPEC §3.1). Stored in `app_settings` as
JSON, validated, read through a short cache so changes apply without a restart."""
import json
import re
import threading
import time
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

import os

from . import config, db

DEFAULTS = {
    "files_path": "/" + os.path.normpath(config.SHARE_DIR).lstrip("/"),
    "max_upload_mb": 50,
    "share_folder_quota_gb": 0,
    "who_can_create_groups": "everyone",
    "edit_window_minutes": 15,
    "message_retention_days": 0,
    "keep_files_on_retention": True,
    "retention_includes_personal": False,
    "notify_preview": "full",
    "blocked_extensions": "exe,bat,cmd,com,scr,msi,ps1,vbs,js,jar,apk,html,htm,svg",
    "photo_max_px": 2000,
    "voice_max_seconds": 300,
    "show_presence": True,
    "notification_reply": True,
    "who_can_announce": "admins",
    "children_can_message_each_other": False,
    "export_max_mb": 500,
}

META = {
    "files_path": {"label": "Chat files folder"},
    "max_upload_mb": {"label": "Largest file (MB)", "min": 1, "max": 1024},
    "share_folder_quota_gb": {"label": "Limit for the chat folder in /share (GB, 0 = no limit)", "min": 0, "max": 10000},
    "who_can_create_groups": {"label": "Who can create groups", "choices": {"everyone": "Everyone", "admins": "Admins only"}},
    "edit_window_minutes": {"label": "Messages can be edited for (minutes, 0 = never)", "min": 0, "max": 1440},
    "message_retention_days": {"label": "Delete messages older than (days, 0 = keep forever)", "min": 0, "max": 36500},
    "keep_files_on_retention": {"label": "Keep the files of messages deleted by age"},
    "retention_includes_personal": {"label": "Also delete old notes in personal rooms"},
    "notify_preview": {"label": "Notification preview (the most anyone's phone shows)",
                       "choices": {"full": "Sender and text", "sender": "Sender only", "none": "Nothing"}},
    "blocked_extensions": {"label": "Refused file types (comma-separated extensions)"},
    "photo_max_px": {"label": "Make photos smaller than (pixels on the longest side, 0 = never)", "min": 0, "max": 10000},
    "voice_max_seconds": {"label": "Longest voice message (seconds)", "min": 10, "max": 900},
    "show_presence": {"label": "Show home / away from Home Assistant"},
    "notification_reply": {"label": "Offer Reply and Mark as read on phone notifications"},
    "who_can_announce": {"label": "Who can post announcements",
                         "choices": {"admins": "Admins", "admins_and_group_admins": "Admins, and group admins in their groups"}},
    "children_can_message_each_other": {"label": "Children can start direct chats with each other"},
    "export_max_mb": {"label": "Largest chat download, files included (MB)", "min": 10, "max": 10000},
}


class AppSettings(BaseModel):
    model_config = ConfigDict(extra="forbid")
    files_path: str = Field(min_length=1, max_length=400)          # checked by files.clean_files_path
    max_upload_mb: int = Field(ge=1, le=1024)
    share_folder_quota_gb: int = Field(ge=0, le=10000)
    who_can_create_groups: Literal["everyone", "admins"]
    edit_window_minutes: int = Field(ge=0, le=1440)
    message_retention_days: int = Field(ge=0, le=36500)
    keep_files_on_retention: bool
    retention_includes_personal: bool
    notify_preview: Literal["full", "sender", "none"]
    blocked_extensions: str = Field(max_length=1000)
    photo_max_px: int = Field(ge=0, le=10000)
    voice_max_seconds: int = Field(ge=10, le=900)
    show_presence: bool
    notification_reply: bool
    who_can_announce: Literal["admins", "admins_and_group_admins"]
    children_can_message_each_other: bool
    export_max_mb: int = Field(ge=10, le=10000)

    @field_validator("blocked_extensions")
    @classmethod
    def _exts(cls, v: str) -> str:
        parts = [p.strip().lower().lstrip(".") for p in v.split(",") if p.strip()]
        for p in parts:
            if not re.fullmatch(r"[a-z0-9]{1,10}", p):
                raise ValueError(f"“{p}” isn't a file extension (letters and digits only).")
        return ",".join(dict.fromkeys(parts))


_cache: tuple[float, dict] | None = None
_lock = threading.Lock()


def invalidate() -> None:
    global _cache
    with _lock:
        _cache = None


def all_values(conn=None) -> dict:
    global _cache
    with _lock:
        if _cache and time.monotonic() - _cache[0] < 5:
            return dict(_cache[1])

    def read(c):
        out = dict(DEFAULTS)
        for r in c.execute("SELECT key, value FROM app_settings"):
            if r["key"] in DEFAULTS:
                try:
                    v = json.loads(r["value"])
                except ValueError:
                    continue
                if type(v) is type(DEFAULTS[r["key"]]):
                    out[r["key"]] = v
        return out
    if conn is not None:
        values = read(conn)
    else:
        with db.get_conn() as c:
            values = read(c)
    with _lock:
        _cache = (time.monotonic(), values)
    return dict(values)


def get(key: str, conn=None):
    return all_values(conn)[key]


def blocked_extensions(conn=None) -> set:
    return {p for p in get("blocked_extensions", conn).split(",") if p}


def update(values: dict, by: str | None = None) -> dict:
    merged = dict(all_values(), **values)
    checked = AppSettings(**merged).model_dump()
    with db.get_conn() as conn:
        for k, v in checked.items():
            db.set_setting(conn, k, json.dumps(v), by)
    invalidate()
    return checked


def payload() -> dict:
    return {"values": all_values(), "defaults": DEFAULTS, "meta": META}
