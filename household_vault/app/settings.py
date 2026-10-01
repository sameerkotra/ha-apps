"""App settings (Admin → App settings). Stored in `app_settings`; validated;
apply without a restart."""
from pydantic import BaseModel, ConfigDict, Field

from . import db

DEFAULTS = {"clipboard_clear_seconds": 30, "min_master_password_length": 12, "allow_breach_check": False,
            "session_max_hours": 12, "reminder_titles": True,
            "personal_copies": False}
LABELS = {"clipboard_clear_seconds": "Clear the clipboard after (seconds, 0 = never)",
          "min_master_password_length": "Shortest master password",
          "allow_breach_check": "Allow the breach check (needs internet)",
          "session_max_hours": "Longest unlocked session (hours)",
          "reminder_titles": "Name the item in expiry reminders (its name is then kept readable, like the date)",
          "personal_copies": "Keep one file per person in /data/copies (all their passwords, locked with their master "
                             "password; rewritten after every change)"}


class AppSettings(BaseModel):
    model_config = ConfigDict(extra="forbid")
    clipboard_clear_seconds: int = Field(ge=0, le=600)
    min_master_password_length: int = Field(ge=8, le=64)
    allow_breach_check: bool
    session_max_hours: int = Field(ge=1, le=72)
    reminder_titles: bool
    personal_copies: bool


def all_values(conn=None) -> dict:
    def read(c):
        out = dict(DEFAULTS)
        for r in c.execute("SELECT key, value FROM app_settings WHERE key IN (%s)" % ",".join("?" * len(DEFAULTS)),
                           list(DEFAULTS)):
            d = DEFAULTS[r["key"]]
            v = r["value"]
            out[r["key"]] = (v == "1") if isinstance(d, bool) else int(v)
        return out
    if conn is not None:
        return read(conn)
    with db.get_conn() as c:
        return read(c)


def get(key: str):
    return all_values()[key]


def update(values: dict) -> dict:
    merged = dict(all_values(), **values)
    checked = AppSettings(**merged).model_dump()
    with db.get_conn() as conn:
        for k, v in checked.items():
            db.set_setting(conn, k, "1" if v is True else "0" if v is False else str(v))
    return checked
