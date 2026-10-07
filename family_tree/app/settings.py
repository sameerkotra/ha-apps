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

from typing import Literal

from pydantic import ValidationError, ValidationInfo

from . import config, db, features
from .common import settings_core
from .common.settings_core import Group, Setting, SettingsError  # noqa: F401  (SettingsError: the routes' name)

logger = logging.getLogger("settings")

# The same defaults and ranges the app options used to have.
TILES_DEFAULT = "https://tile.openstreetmap.org/{z}/{x}/{y}.png"
NOMINATIM_DEFAULT = "https://nominatim.openstreetmap.org"


def _tiles(v: str) -> str:
    v = clean_url(v)
    if any(k not in v for k in ("{z}", "{x}", "{y}")):
        raise ValueError("The map tiles address needs {z}, {x} and {y}, like "
                         "https://tile.openstreetmap.org/{z}/{x}/{y}.png.")
    return v


def _nominatim(v: str) -> str:
    return clean_url(v).rstrip("/")


def _media_path(v: str, info: ValidationInfo) -> str:
    return clean_media_path(v, stored=bool(info.context and info.context.get("stored")))


_FEATURE_GROUPS = {"general": "features", "regional": "features_regional", "internet": "features_internet"}

GROUPS = [
    Group("general", "App settings"),
    Group("map", "Places map", "Defaults: OpenStreetMap's tile server and Nominatim. Point them at your own servers "
                               "if you have them."),
    Group("features", "Features", "Turn parts of the app on or off for everyone. Turning something off only hides it "
                                  "— nothing is deleted, and turning it back on shows everything again. Background "
                                  "work for a module that's off (reminders, map look-ups, the inbox) stops."),
    Group("features_regional", "Region- and culture-specific features (off for a new install)"),
    Group("features_internet", "Features that use the internet (off for a new install)"),
    Group("assistant", "Household Assistant", "The Household Assistant app answers questions from what the household "
          "apps know. Family Tree tells it the birthdays and anniversaries coming up."),
]

SETTINGS = [
    # strict: no "90", 90.0, true or NaN — whole numbers only
    Setting("trash_days", 90, "Days in trash", group="general", min=7, max=3650, strict=True, unit="days",
            help="How long deleted people, families and photos can be restored before they're removed for good."),
    Setting("max_upload_mb", 20, "Largest upload (MB)", group="general", min=1, max=100, strict=True, unit="MB",
            help="The biggest photo or document anyone can upload."),
    Setting("media_path", config.MEDIA_PATH_DEFAULT, "Photo folder", group="general", kind="text", strict=True,
            min_length=1, max_length=400, validators=[_media_path],
            help="Where photos and documents are kept, inside /share — for example /share/nas/family_tree on network "
                 "storage. Changing it doesn't move any files: copy the whole old folder, including "
                 ".family_tree_store, to the new place first."),
    Setting("relationship_language", "en", "Relationship names", group="general",                          # §13.1
            choices=[("en", "English"), ("te", "Telugu (తెలుగు)"), ("hi", "Hindi (हिन्दी)")],
            show_if="feature_kin_names",
            help="The household's language for relationship names: “uncle”, or “Babai — your father's younger "
                 "brother”. Each person can choose their own in Settings."),
    Setting("name_order", "given_first", "Name order", group="general",                                    # §13.7
            choices=[("given_first", "First name first (Asha Sharma)"), ("surname_first", "Surname first (Sharma Asha)")],
            help="How full names are written for everyone. Any person can have their own order (Edit → More details)."),
    Setting("default_phone_code", "+1", "Default phone code", group="general", strict=True,               # §13.11
            pattern=r"^\+[1-9][0-9]{0,3}$", show_if="feature_contacts", page={"maxLength": 5},
            help="Contact details: used when a phone number is typed without a country code, like +1 or +44."),
    Setting("tithi_rule", "aparahna", "Tithi day", group="general",                                      # §13.12
            choices=[("aparahna", "Aparahna — the tithi covers the afternoon (usual for shraddha)"),
                     ("sunrise", "Sunrise — the tithi at sunrise")],
            show_if="feature_tithi",
            help="Tithi: which day a death-anniversary tithi is kept on, when a tithi spans two days."),
    Setting("map_tiles_url", TILES_DEFAULT, "Map tiles address", group="map", kind="url", strict=True,
            min_length=10, max_length=300, validators=[_tiles], show_if="feature_map"),
    Setting("nominatim_url", NOMINATIM_DEFAULT, "Place search address", group="map", kind="url", strict=True,
            min_length=10, max_length=300, validators=[_nominatim], show_if="feature_map"),
    Setting("assistant_answers", True, "Answer the Household Assistant", group="assistant", strict=True,
            help="Lets the Household Assistant tell people the birthdays and anniversaries coming up, as Upcoming "
                 "shows them. Each person can turn it off on Settings."),
    # one strict true/false per feature switch (features.py)
    *[Setting(f.key, f.default, f.label, group=_FEATURE_GROUPS[f.kind], strict=True, help=f.help)
      for f in features.FEATURES],
]
RANGES = {s.key: (s.min, s.max) for s in SETTINGS if s.min is not None}


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


def _prepare(partial: dict, current: dict, flags: dict) -> dict:
    """Only the keys being changed are held to today's rules (a stored photo folder outside /share stays)."""
    return {k: REGISTRY.validate_one(k, v) for k, v in partial.items()}


def _check(merged: dict, partial: dict, current: dict) -> None:
    if merged["relationship_language"] != "en" and not merged["feature_kin_names"] and "relationship_language" in partial:
        # the household language is part of that module (and would switch it back on in a restored copy)
        raise SettingsError("Turn on Indian relationship names (Features) to choose Telugu or Hindi.")


REGISTRY = settings_core.Registry(
    SETTINGS, groups=GROUPS, connect=db.get_conn, format_error=lambda e: _message(e),
    unknown_message=lambda keys: f"Unknown setting: {', '.join(sorted(map(str, keys)))}.",
    not_object_message="Send the settings as a JSON object.", prepare=_prepare, check=_check,
    context={"stored": True}, load_context={"stored": True}, now=config.now_iso,
    log=lambda *a: None, logger=logger)

AppSettings = REGISTRY.model
DEFAULTS = REGISTRY.defaults
LABELS = REGISTRY.labels
META = REGISTRY.meta()


def validate_one(key: str, value, stored: bool = False):
    """The validated value for one key, or SettingsError."""
    return REGISTRY.validate_one(key, value, context={"stored": stored})


def invalidate() -> None:
    REGISTRY.invalidate()


def get(key: str):
    return REGISTRY.get(key)


def all() -> dict:      # noqa: A001  (the design's name)
    return REGISTRY.all()


def public() -> dict:
    """The GET/PUT /api/admin/settings payload."""
    return REGISTRY.payload(features=features.public())


def update(partial: dict, user: dict | None) -> dict:
    """Validate the merged result, write only the changed keys, return all values."""
    values, changed = REGISTRY.update(partial, user["id"] if user else None)
    if changed:
        logger.info("App settings changed by %s: %s", (user or {}).get("name") or "?", {k: values[k] for k in changed})
    return REGISTRY.all()


def rows(conn) -> list:
    """All app_settings rows (to carry them across a restore)."""
    return [tuple(r) for r in conn.execute("SELECT key, value, updated_at, updated_by FROM app_settings")]


def put_rows(conn, saved: list) -> None:
    conn.executemany("INSERT OR REPLACE INTO app_settings (key, value, updated_at, updated_by) VALUES (?, ?, ?, ?)", saved)
