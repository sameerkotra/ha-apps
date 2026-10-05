"""App settings (Admin → App settings, SPEC §4). Stored in `app_settings` as JSON, validated, read
through a short cache so changes apply without a restart. Built on the shared registry
(common/python/settings_core.py): a new setting needs only its `Setting(...)` line; the page draws it.

`docs_path` is kept here but edited on Admin → Documents folder (§5.6), where it is checked first; the
settings page doesn't show it and PUT /api/admin/settings refuses it.
"""
import re

from . import config, db
from .common import settings_core
from .common.settings_core import Group, Setting, SettingsError  # noqa: F401  (SettingsError: the routes' name)

GROUPS = [
    Group("people", "People and sharing"),
    Group("documents", "Documents"),
    Group("index", "Finding files"),
    Group("extras", "Activity, Home Assistant and imports"),
    Group("ai", "AI", "Optional. Nothing is sent anywhere until this is turned on and set up."),
    Group("backup", "Backups"),
]

# AI (§17.6): the same three providers and settings as the other household apps with AI
PROVIDERS = ("ollama", "openai", "anthropic")
PROVIDER_LABELS = {"ollama": "Ollama (on your network)", "openai": "OpenAI-compatible", "anthropic": "Anthropic Claude"}
DEFAULT_URLS = {"ollama": "", "openai": "https://api.openai.com/v1", "anthropic": "https://api.anthropic.com"}


def _provider(v):
    if v not in PROVIDERS:
        raise ValueError("must be one of " + ", ".join(PROVIDERS))
    return v


def _url(v):
    v = v.rstrip("/")
    if v and not re.match(r"^https?://[^\s/]+\S*$", v, re.I):
        raise ValueError("must start with http:// or https://")
    return v


def _one_line(v):
    if any(c in v for c in "\r\n\x00"):
        raise ValueError("must be on one line")
    return v

SETTINGS = [
    Setting("docs_path", config.DEFAULT_DOCS_PATH, "Documents folder", group="documents", hidden=True,
            min_length=7, max_length=400,
            help="Where everyone's documents are kept, inside /share. Chosen on Admin → Documents folder."),
    Setting("folder_names", "display", "Name of each person's folder", group="people",
            choices=[("display", "Their Home Assistant display name (“Priya”)"),
                     ("username", "Their Home Assistant user name (“priya”)")],
            help="Only used when someone's folder is first made. Renaming someone in Home Assistant doesn't "
                 "rename their folder; an admin can, on Admin → People."),
    Setting("new_people_access", True, "New people get access", group="people",
            help="People added to Home Assistant can use Household Docs straight away. Off: an admin turns "
                 "each new person on, on Admin → People."),
    Setting("everyone_shares", True, "People may share with Everyone", group="people"),
    Setting("everyone_default_role", "viewer", "Role picked first when sharing with Everyone", group="people",
            choices=[("viewer", "Can view"), ("editor", "Can edit")]),
    Setting("versions_kept", 30, "Versions kept per document", group="documents", min=5, max=200,
            help="Earlier copies under History. The oldest are deleted past this number."),
    Setting("trash_days", 30, "Days before Trash is emptied", group="documents", min=1, max=365, unit="days"),
    Setting("max_doc_mb", 5, "Largest document the editor opens", group="documents", min=1, max=25, unit="MB",
            help="Bigger notes and checklists can still be downloaded."),
    Setting("upload_mb", 100, "Largest upload", group="documents", min=1, max=2048, unit="MB",
            help="The biggest file someone can upload (one file at a time). Bigger files can still be copied into "
                 "a folder over Samba or the File editor."),
    Setting("quota_gb", 0, "Limit per person for their own folder", group="documents", min=0, max=10000,
            unit="GB", help="0 = no limit. Saving past it is refused with a message."),
    Setting("secret_hint", True, "Warn when a document looks like it holds a password", group="documents",
            help="A light check in the browser (nothing is sent) notices things like “password:”, a card "
                 "number or an authenticator code and suggests Household Vault instead."),
    Setting("scan_minutes", 10, "Look for changes made outside the app every", group="index", min=2, max=120,
            unit="minutes",
            help="Files added, changed, renamed or deleted over Samba, the File editor or another app are found "
                 "by this scan (and whenever a folder or document is opened)."),
    Setting("content_index_mb", 1, "Largest text file whose words are searchable", group="index", min=0, max=20,
            unit="MB", help="0 = search names only."),
    Setting("regex_search", True, "Allow regular-expression search", group="index",
            help="Patterns like ^invoice-\\d{4} in names and text on the Search page. They run in a separate worker "
                 "that is stopped after 5 seconds, one at a time per person."),
    Setting("pdf_index_mb", 20, "Largest PDF whose text is searchable", group="index", min=0, max=64, unit="MB",
            help="Text in PDFs (up to 500 pages each) is read in the background so search finds it. 0 = PDFs are found "
                 "by name only. Password-protected PDFs are skipped; scans without text can be read by AI."),
    Setting("activity_days", 90, "Keep activity for", group="extras", min=7, max=730, unit="days",
            help="🕑 Activity lists what changed in the last this-many days (never anything a person can't open)."),
    Setting("ha_sensors", True, "Items can be shown in Home Assistant", group="extras",
            help="Owners and editors can put a checklist's open items, a sheet's cell or a folder's file count on a "
                 "Home Assistant dashboard (⋯ → Show in Home Assistant). Off: every such sensor is removed."),
    Setting("keep_import", True, "Import from Google Keep", group="extras",
            help="People can bring their Google Keep notes in from a Google Takeout .zip."),
    Setting("ai_enabled", False, "AI features", group="ai",
            help="Off: nothing is ever sent to an AI model and the AI buttons are hidden. On: people can ask the model "
                 "chosen here to read text in scans, summarise, make checklists and sheets, and answer questions "
                 "about a folder — each time after a dialog that names the provider."),
    Setting("ai_provider", "ollama", "Provider", group="ai", type=str, validators=[_provider],
            choices=list(PROVIDER_LABELS.items()), show_if="ai_enabled"),
    Setting("ai_url", "", "Address", group="ai", kind="url", max_length=500, validators=[_url], show_if="ai_enabled",
            help="Ollama: your server, like http://homeassistant.local:11434. Others: leave empty for the provider's usual "
                 "address."),
    Setting("ai_api_key", "", "Access key", group="ai", secret=True, max_length=1000, validators=[_one_line],
            show_if="ai_enabled", placeholder="Not set (Anthropic Claude and most online services need one)"),
    Setting("ai_model", "", "Text model", group="ai", max_length=200, validators=[_one_line], show_if="ai_enabled",
            help="Summaries, checklists, sheets from text, questions about a folder."),
    Setting("ai_vision_model", "", "Vision model", group="ai", max_length=200, validators=[_one_line],
            show_if="ai_enabled",
            help="Reads text in photos and scanned PDFs and tables in photos. Empty: the text model is used (it must "
                 "understand images)."),
    Setting("ai_monthly_tokens", 0, "Most tokens a month", group="ai", min=0, max=1_000_000_000, unit="tokens",
            show_if="ai_enabled", help="0 = no limit. Past it, AI buttons say the month's limit is used up."),
    Setting("ai_price_in", 0.0, "Price per million input tokens", group="ai", min=0, max=1000, page={"step": 0.01},
            show_if="ai_enabled", help="Only for the cost estimate on Admin → AI usage. Leave 0 for your own model."),
    Setting("ai_price_out", 0.0, "Price per million output tokens", group="ai", min=0, max=1000, page={"step": 0.01},
            show_if="ai_enabled"),
    Setting("backup_files", False, "Backup includes the documents themselves", group="backup",
            help="Off: Admin → Backup downloads the database only (sharing, the index, settings); the documents "
                 "are in Home Assistant's backups when Share is ticked. On: the zip also holds every file in the "
                 "documents folder, which can be large."),
]


def first_error(e) -> str:
    err = e.errors()[0]
    label = REGISTRY.labels.get(str(err["loc"][0]) if err.get("loc") else "", "")
    msg = settings_core.clean_message(err)
    return f"{label}: {msg}" if label else msg


REGISTRY = settings_core.Registry(
    SETTINGS, groups=GROUPS, connect=db.get_conn, format_error=lambda e: first_error(e),
    load_check="type", cache_ttl=5)

AppSettings = REGISTRY.model
DEFAULTS = REGISTRY.defaults


def invalidate() -> None:
    REGISTRY.invalidate()


def all_values(conn=None) -> dict:
    return REGISTRY.all(conn)


def get(key: str, conn=None):
    return REGISTRY.get(key, conn)


def update(values: dict, by: str | None = None) -> list:
    """Validate {current + values} and store them. Returns the changed keys. Raises SettingsError."""
    return REGISTRY.update(values, by)[1]


def payload(**extra) -> dict:
    return REGISTRY.payload(**extra)


def payload_extra() -> dict:
    """What the App settings page needs beyond the registry: the AI providers and their usual addresses."""
    return {"providers": [{"id": p, "label": PROVIDER_LABELS[p], "defaultUrl": DEFAULT_URLS[p]} for p in PROVIDERS]}


def ai_url(conn=None) -> str:
    """The AI address to use: the one set, else the provider's usual one (Ollama has none)."""
    return (get("ai_url", conn) or DEFAULT_URLS.get(get("ai_provider", conn), "")).rstrip("/")


def ai_problem(conn=None) -> str | None:
    """Why AI can't be used right now (None: it can). Nothing is sent while there is a problem."""
    v = all_values(conn)
    if not v["ai_enabled"]:
        return "AI is off. An admin can turn it on in Admin → App settings → AI."
    if not v["ai_model"]:
        return "No AI model is set yet (Admin → App settings → AI)."
    if not ai_url(conn):
        return "Ollama needs its address (Admin → App settings → AI)."
    if v["ai_provider"] == "anthropic" and not v["ai_api_key"]:
        return "Anthropic Claude needs an access key (Admin → App settings → AI)."
    return None
