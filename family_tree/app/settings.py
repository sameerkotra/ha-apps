"""App settings (Admin → App settings), stored in the `app_settings` table.

They live in the database, are changed by admins in the app, and apply at
once: every reader calls `get(key)` at the moment it needs the value (every
upload, every purge). Reads go through a small in-memory cache that is dropped
on every write, and on a backup restore (`invalidate()`).

Only `admin_users` stays in the app's Configuration tab (someone has to be an
admin before this page can be opened). Values in options.json other than
`admin_users` are never read.

The feature switches (`feature_<name>`, features.py) are App settings too.
"""
import json
import logging
import os
import threading

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, ValidationInfo, create_model, field_validator

from . import config, db, features

logger = logging.getLogger("settings")

# The same defaults and ranges the app options used to have.
TILES_DEFAULT = "https://tile.openstreetmap.org/{z}/{x}/{y}.png"
NOMINATIM_DEFAULT = "https://nominatim.openstreetmap.org"
DEFAULTS = {"trash_days": 90, "max_upload_mb": 20, "media_path": config.MEDIA_PATH_DEFAULT,
            "relationship_language": "en", "name_order": "given_first",
            "default_phone_code": "+1", "tithi_rule": "aparahna",
            "map_tiles_url": TILES_DEFAULT, "nominatim_url": NOMINATIM_DEFAULT,
            **{f.key: f.default for f in features.FEATURES}}
LABELS = {"trash_days": "Days in trash", "max_upload_mb": "Largest upload (MB)", "media_path": "Photo folder",
          "relationship_language": "Relationship names", "name_order": "Name order",
          "default_phone_code": "Default phone code", "tithi_rule": "Tithi day",
          "map_tiles_url": "Map tiles address", "nominatim_url": "Place search address",
          **{f.key: f.label for f in features.FEATURES}}
RANGES = {"trash_days": (7, 3650), "max_upload_mb": (1, 100)}
META = {k: {"restartRequired": False} for k in DEFAULTS}


class _BaseSettings(BaseModel):
    model_config = ConfigDict(extra="forbid")
    # strict: no "90", 90.0, true or NaN — whole numbers only
    trash_days: int = Field(ge=7, le=3650, strict=True)
    max_upload_mb: int = Field(ge=1, le=100, strict=True)
    media_path: str = Field(min_length=1, max_length=400, strict=True)
    relationship_language: Literal["en", "te", "hi"]            # §13.1
    name_order: Literal["given_first", "surname_first"]         # §13.7
    default_phone_code: str = Field(pattern=r"^\+[1-9][0-9]{0,3}$", strict=True)     # §13.11
    tithi_rule: Literal["aparahna", "sunrise"]                                      # §13.12
    map_tiles_url: str = Field(min_length=10, max_length=300, strict=True)
    nominatim_url: str = Field(min_length=10, max_length=300, strict=True)

    @field_validator("map_tiles_url")
    @classmethod
    def _tiles(cls, v: str) -> str:
        v = clean_url(v)
        if any(k not in v for k in ("{z}", "{x}", "{y}")):       # (all() is this module's own function)
            raise ValueError("The map tiles address needs {z}, {x} and {y}, like "
                             "https://tile.openstreetmap.org/{z}/{x}/{y}.png.")
        return v

    @field_validator("nominatim_url")
    @classmethod
    def _nominatim(cls, v: str) -> str:
        return clean_url(v).rstrip("/")

    @field_validator("media_path")
    @classmethod
    def _media_path(cls, v: str, info: ValidationInfo) -> str:
        return clean_media_path(v, stored=bool(info.context and info.context.get("stored")))


# one strict true/false per feature switch (features.py)
AppSettings = create_model("AppSettings", __base__=_BaseSettings,
                           **{f.key: (bool, Field(strict=True)) for f in features.FEATURES})


def clean_media_path(v: str, stored: bool = False) -> str:
    """The old schema's `match(^/share/.+)`, plus: absolute, no . or .. parts,
    no control characters, normalised (no double or trailing slashes).
    ALLOW_ANY_MEDIA_PATH (tests, local development) lifts the /share rule; so
    does `stored` (a value already in the database is kept as it is, and
    media.check() reports it as offline instead of silently using another folder)."""
    v = v.strip()
    if any(ord(c) < 32 or ord(c) == 127 for c in v):
        raise ValueError("The photo folder can't contain control characters.")
    if not v.startswith("/"):
        raise ValueError("The photo folder must be a full path starting with /share/, like /share/family_tree.")
    if any(part in (".", "..") for part in v.split("/")):
        raise ValueError("The photo folder can't contain . or .. parts.")
    v = "/" + os.path.normpath(v).lstrip("/")
    if not (stored or config.ALLOW_ANY_MEDIA_PATH) and not (v.startswith("/share/") and len(v) > len("/share/")):
        raise ValueError("The photo folder must be a folder inside /share (not /share itself), "
                         "like /share/family_tree or /share/nas/family_tree.")
    if v in ("/", "/share"):
        raise ValueError("The photo folder must be a folder inside /share (not /share itself), like /share/family_tree.")
    return v


def clean_url(v: str) -> str:
    """http(s) only, no spaces, quotes or angle brackets (it ends up in a CSP header)."""
    from urllib.parse import urlsplit
    v = v.strip()
    if any(c in v for c in " \t\r\n\"'<>;\\") or any(ord(c) < 32 for c in v):
        raise ValueError("That address has characters an address can't have.")
    parts = urlsplit(v.replace("{", "").replace("}", ""))
    if parts.scheme not in ("http", "https") or not parts.netloc or "@" in parts.netloc:
        raise ValueError("The address must start with https:// (or http://) and name a server.")
    return v


def origin(url: str) -> str:
    """scheme://host[:port] of a URL (what a CSP source needs)."""
    from urllib.parse import urlsplit
    p = urlsplit(url.replace("{s}", "SUBDOMAIN").replace("{z}", "0").replace("{x}", "0").replace("{y}", "0"))
    host = p.netloc.replace("SUBDOMAIN.", "*.").replace("SUBDOMAIN", "*")   # {s}.tile.example → *.tile.example
    return f"{p.scheme}://{host}"


class SettingsError(ValueError):
    """A readable message for a 422."""


_lock = threading.Lock()
_cache: dict | None = None


def invalidate() -> None:
    global _cache
    with _lock:
        _cache = None


def _message(e: ValidationError) -> str:
    msgs = []
    for err in e.errors():
        key = str(err["loc"][0]) if err.get("loc") else ""
        label = LABELS.get(key, key)
        if err["type"] == "extra_forbidden":
            msgs.append(f"Unknown setting: {key}.")
        elif key in RANGES:
            lo, hi = RANGES[key]
            msgs.append(f"{label} must be a whole number from {lo} to {hi}.")
        elif err["type"] == "value_error":
            msgs.append(str(err.get("ctx", {}).get("error") or err.get("msg")))
        elif key == "media_path":
            msgs.append(f"{label} must be a path like /share/family_tree.")
        elif key == "relationship_language":
            msgs.append(f"{label} must be en (English), te (Telugu) or hi (Hindi).")
        elif key == "default_phone_code":
            msgs.append(f"{label} must look like +1 or +91.")
        elif key == "tithi_rule":
            msgs.append(f"{label} must be aparahna or sunrise.")
        elif key == "name_order":
            msgs.append(f"{label} must be given_first or surname_first.")
        elif key in features.KEYS:
            msgs.append(f"{label} must be true or false (on or off).")
        elif key in ("map_tiles_url", "nominatim_url"):
            msgs.append(f"{label} must be a web address like https://….")
        else:
            msgs.append(f"{label}: {err.get('msg')}")
    return " ".join(msgs) or "Invalid settings."


def validate_one(key: str, value, stored: bool = False):
    """The validated value for one key, or SettingsError."""
    if key not in DEFAULTS:
        raise SettingsError(f"Unknown setting: {key}.")
    try:
        # the defaults are fine as they are (MEDIA_PATH may point outside /share in development)
        base = dict(DEFAULTS, media_path="/share/family_tree") if key != "media_path" else dict(DEFAULTS)
        return AppSettings.model_validate(dict(base, **{key: value}),
                                          context={"stored": stored}).model_dump()[key]
    except ValidationError as e:
        raise SettingsError(_message(e))


def _load(conn) -> dict:
    values = dict(DEFAULTS)
    for r in conn.execute("SELECT key, value FROM app_settings"):
        try:
            v = json.loads(r["value"])
        except (TypeError, ValueError):
            logger.warning("Ignoring an unreadable app setting %s", r["key"])
            continue
        if r["key"] in DEFAULTS:                  # anything else (e.g. an old "_imported" row) is ignored
            try:
                values[r["key"]] = validate_one(r["key"], v, stored=True)
            except SettingsError:
                logger.warning("Ignoring an invalid stored value for %s; using the default", r["key"])
    return {"values": values}


def _snapshot() -> dict:
    global _cache
    with _lock:
        if _cache is None:
            with db.get_conn() as conn:
                _cache = _load(conn)
        return _cache


def get(key: str):
    return _snapshot()["values"][key]


def all() -> dict:      # noqa: A001  (the design's name)
    return dict(_snapshot()["values"])


def public() -> dict:
    """The GET/PUT /api/admin/settings payload."""
    snap = _snapshot()
    return {"values": dict(snap["values"]), "defaults": dict(DEFAULTS),
            "meta": {k: dict(v) for k, v in META.items()}, "features": features.public()}


def _write(conn, key: str, value, user_id: str | None) -> None:
    conn.execute("INSERT INTO app_settings (key, value, updated_at, updated_by) VALUES (?, ?, ?, ?) "
                 "ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at, "
                 "updated_by = excluded.updated_by", (key, json.dumps(value), config.now_iso(), user_id))


def update(partial: dict, user: dict | None) -> dict:
    """Validate the merged result, write only the changed keys, return all values."""
    if not isinstance(partial, dict):
        raise SettingsError("Send the settings as a JSON object.")
    unknown = [k for k in partial if k not in DEFAULTS]
    if unknown:
        raise SettingsError(f"Unknown setting: {', '.join(sorted(map(str, unknown)))}.")
    current = all()
    try:
        # only the keys being changed are held to today's rules
        checked = {k: validate_one(k, v) for k, v in partial.items()}
        merged = AppSettings.model_validate(dict(current, **checked), context={"stored": True}).model_dump()
    except ValidationError as e:
        raise SettingsError(_message(e))
    if merged["relationship_language"] != "en" and not merged["feature_kin_names"] and "relationship_language" in partial:
        # the household language is part of that module (and would switch it back on in a restored copy)
        raise SettingsError("Turn on Indian relationship names (Features) to choose Telugu or Hindi.")
    changed = {k: v for k, v in merged.items() if k in partial and v != current[k]}
    if changed:
        with db.get_conn() as conn:
            for k, v in changed.items():
                _write(conn, k, v, user["id"] if user else None)
        invalidate()
        logger.info("App settings changed by %s: %s", (user or {}).get("name") or "?", changed)
    return all()


def rows(conn) -> list:
    """All app_settings rows (to carry them across a restore)."""
    return [tuple(r) for r in conn.execute("SELECT key, value, updated_at, updated_by FROM app_settings")]


def put_rows(conn, saved: list) -> None:
    conn.executemany("INSERT OR REPLACE INTO app_settings (key, value, updated_at, updated_by) VALUES (?, ?, ?, ?)", saved)
