"""Feature switches: Admin → App settings → Features.

Every optional module of the app has a switch, stored as the App setting
`feature_<name>` (a JSON true/false in `app_settings`, read live through
settings.get(), so a change applies at once, no restart).

Defaults for a new install: general modules on; region- or culture-specific
modules (Indian relationship names, Telugu/Hindi script names, the Hindu
lunar calendar, Indian ceremonies) and anything that talks to the internet
(the places map) off.

An existing database (for example a restored backup) gets a switch turned on
when the module already holds data and no value was ever stored: `db._migrate`
runs `INSERT OR IGNORE … WHERE EXISTS (…)` with the `exists` query below, so
nothing a household already uses disappears. A stored value always wins.

Off means hidden, never deleted: the navigation, buttons, form fields and
badges skip the module, background jobs skip it, its API routes answer a
friendly 404 "<Feature> is turned off", and it is left out of exports where it
would otherwise show. Turning the switch back on shows everything again.
"""
from dataclasses import dataclass

from fastapi import Depends


@dataclass(frozen=True)
class Feature:
    name: str                  # stored as the App setting "feature_<name>"
    label: str                 # "<label> is turned off"
    default: bool              # for a new install
    kind: str                  # "general" | "regional" | "internet"
    help: str                  # what it is / what switching it off hides
    exists: str | None = None  # SQL (a SELECT 1 …): data that turns it on in an existing database

    @property
    def key(self) -> str:
        return f"feature_{self.name}"


CEREMONY_TYPES = ("namakaranam", "annaprasana", "aksharabhyasam", "upanayanam", "seemantham", "shashtipoorthi",
                  "sahasra_chandra", "ceremony", "nischitartham", "gruhapravesham")

FEATURES = [
    Feature("reminders", "Reminders", True, "general",
            "A daily message to people's phones (through Home Assistant) about birthdays, anniversaries and other "
            "days: the 🔔 switches, Settings → Reminders, phones and notify services in Admin → Users, and the daily "
            "reminder run.",
            "SELECT 1 FROM people WHERE remind = 1 UNION ALL SELECT 1 FROM reminder_prefs WHERE enabled = 1 "
            "UNION ALL SELECT 1 FROM user_notify"),
    Feature("milestones", "Milestones", True, "general",
            "🎉 Special birthdays and wedding anniversaries (first birthday, 60th, golden wedding…) with extra "
            "notice in Upcoming and reminders, and the list in Settings → Milestones."),
    Feature("sides", "Father's and mother's side", True, "general",
            "Colour the tree by father's side and mother's side, and the side filters in the tree, People and "
            "Relatives."),
    Feature("photo_tagging", "Face tagging", True, "general",
            "Boxes around faces on photos (Tag a face), using a box as a profile photo, and tagged faces in the quiz. "
            "Profile photos already cropped to a face keep their crop.",
            "SELECT 1 FROM media_regions"),
    Feature("photo_fixes", "Photo fixes", True, "general",
            "Rotate, straighten, crop and auto-contrast for scanned photos (🛠 Fix photo). Photos already fixed keep "
            "showing the fixed version.",
            "SELECT 1 FROM media WHERE edit IS NOT NULL"),
    Feature("inbox", "Inbox folder", True, "general",
            "Import photos and PDFs dropped into the photo folder's inbox/ subfolder every 2 minutes, and the "
            "Unsorted queue for them.",
            "SELECT 1 FROM media WHERE unsorted = 1 OR orig_name IS NOT NULL"),
    Feature("custom_fields", "Custom fields", True, "general",
            "Extra details an admin defines (native village, clan, star sign…): the Details block on person pages, "
            "the People filter, fields on tree cards and in exports.",
            "SELECT 1 FROM custom_fields"),
    Feature("sources", "Sources", True, "general",
            "Where information came from: the 📎 buttons on facts, the Sources page, and sources in exports.",
            "SELECT 1 FROM sources UNION ALL SELECT 1 FROM citations"),
    Feature("contacts", "Contact details", True, "general",
            "Phone numbers, emails and addresses of living relatives, the Address book with its vCard download, "
            "and contact details in exports.",
            "SELECT 1 FROM contacts"),
    Feature("duplicates", "Duplicate finder", True, "general",
            "Find people who may have been entered twice, compare them and merge them.",
            "SELECT 1 FROM not_duplicates UNION ALL SELECT 1 FROM people WHERE merged_into IS NOT NULL"),
    Feature("quiz", "Photo quiz", True, "general",
            "The “Who is this?” photo quiz, kids mode (a device locked to the quiz) and the kids-mode PIN.",
            "SELECT 1 FROM quiz_stats UNION ALL SELECT 1 FROM kid_sessions "
            "UNION ALL SELECT 1 FROM users WHERE kid_pin_hash IS NOT NULL"),
    Feature("printing", "Wall chart and family book", True, "general",
            "Printable wall charts (also tiled across A4 sheets, or as SVG) and the family book."),
    Feature("export", "Website export", True, "general",
            "The Export page: the whole tree or a branch as a family website (a zip that opens offline), with "
            "saved choices.",
            "SELECT 1 FROM export_presets UNION ALL SELECT 1 FROM users WHERE export_last IS NOT NULL"),
    Feature("map", "Places map", False, "internet",
            "A map of where the family was born, married, lived and died. Uses the internet: each place name (only "
            "the place — no names or dates) is looked up on OpenStreetMap Nominatim, and map pictures are downloaded "
            "from the tile server by the browser.",
            "SELECT 1 FROM place_geo"),
    Feature("kin_names", "Indian relationship names", False, "regional",
            "Relationship names in Telugu or Hindi (“Babai — your father's younger brother”), the household and "
            "personal language choice, the family's own words, and Telugu/Hindi labels for ceremonies and milestones.",
            "SELECT 1 FROM kin_terms UNION ALL SELECT 1 FROM users WHERE kin_lang IN ('te','hi') "
            "UNION ALL SELECT 1 FROM app_settings WHERE key = 'relationship_language' AND value <> '\"en\"'"),
    Feature("script_names", "Names in Telugu/Hindi script", False, "regional",
            "A second spelling of each name in Telugu or Hindi script (with Suggest from English), and showing names "
            "in script on cards and pages.",
            "SELECT 1 FROM people WHERE COALESCE(given_local, '') <> '' OR COALESCE(surname_local, '') <> '' "
            "UNION ALL SELECT 1 FROM users WHERE name_display IN ('script','both')"),
    Feature("tithi", "Tithi (Hindu lunar calendar)", False, "regional",
            "Tithi death anniversaries (shraddha) and janma tithis worked out offline for Home Assistant's location, "
            "the Tithi dates page, tithi reminders, and the 1000-full-moons milestone (Sahasra Chandra Darshanam).",
            "SELECT 1 FROM event_tithi"),
    Feature("ceremonies", "Indian ceremonies", False, "regional",
            "Ceremony event types such as Namakaranam, Annaprasana, Upanayanam, Seemantham, Shashtipoorthi, "
            "Nischitartham and Gruhapravesham.",
            "SELECT 1 FROM events WHERE type IN (" + ", ".join(f"'{t}'" for t in CEREMONY_TYPES) + ")"),
]
BY_NAME = {f.name: f for f in FEATURES}
KEYS = [f.key for f in FEATURES]


def on(name: str) -> bool:
    """Read live (App settings are cached until the next change)."""
    from . import settings
    return bool(settings.get(BY_NAME[name].key))


def states() -> dict:
    """{name: bool} for every switch (the /api/me payload)."""
    from . import settings
    values = settings.all()
    return {f.name: bool(values[f.key]) for f in FEATURES}


def public() -> list:
    """The switches as the App settings page lists them."""
    return [{"key": f.key, "name": f.name, "label": f.label, "default": f.default, "kind": f.kind, "help": f.help}
            for f in FEATURES]


class FeatureOff(Exception):
    """Raised by a switched-off module; main.py answers 404 with a friendly message."""

    def __init__(self, name: str, admin: bool = False):
        super().__init__(name)
        self.name = name
        self.admin = admin

    @property
    def message(self) -> str:
        label = BY_NAME[self.name].label
        if self.admin:
            return f"{label} is turned off. Turn it on in Admin → App settings → Features."
        return f"{label} is turned off. An admin can turn it on in Admin → App settings."


def check(name: str, user: dict | None = None) -> None:
    if not on(name):
        raise FeatureOff(name, bool(user and user.get("is_admin")))


def required(name: str):
    """A route/router dependency: 404 "<Feature> is turned off" while the switch is off."""
    from .auth import get_current_user

    async def _dep(user: dict = Depends(get_current_user)):
        check(name, user)
    return _dep


def migrate(conn, now: str) -> None:
    """Existing databases: a switch with no stored value is turned on when its module
    already has data. Older databases also carry the map / inbox switches over from the
    settings they used to be (`enable_map`, `inbox_enabled`)."""
    for old, new in (("enable_map", "feature_map"), ("inbox_enabled", "feature_inbox")):
        conn.execute("INSERT OR IGNORE INTO app_settings (key, value, updated_at, updated_by) "
                     "SELECT ?, value, updated_at, updated_by FROM app_settings WHERE key = ?", (new, old))
        conn.execute("DELETE FROM app_settings WHERE key = ?", (old,))
    for f in FEATURES:
        if f.exists:
            conn.execute("INSERT OR IGNORE INTO app_settings (key, value, updated_at, updated_by) "
                         f"SELECT ?, 'true', ?, NULL WHERE EXISTS ({f.exists})", (f.key, now))
