"""App settings (Admin → App settings, SPEC §8): stored in the database and
read at the moment they are used, so a change applies without a restart.

`admin_users` is deliberately NOT here — it stays on the app's Configuration
tab, because it is how the first admin is known. Holidays are their own table
(db `holidays`, edited on the same page through /api/admin/holidays).

Values are JSON in `app_settings`, one row per key that has been set; a key
without a row uses DEFAULTS. Reads go through a small cache that is dropped on
every write and whenever the database file is (re)initialised.
"""
import logging
import re

from . import config, db, games
from .common import settings_core
from .common.settings_core import Group, Setting, SettingsError  # noqa: F401  (SettingsError: the routes' name)

logger = logging.getLogger("settings")

KEEP_SCORES_CHOICES = (0, 1, 2, 5)          # years; 0 = forever
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


def _games(v):
    bad = [g for g in v if g not in games.GAMES]
    if bad:
        raise ValueError("unknown game(s): " + ", ".join(bad))
    return [g for g in games.GAME_IDS if g in v]


def _look(v):
    if v not in games.LOOKS:
        raise ValueError("must be one of " + ", ".join(games.LOOKS))
    return v


def _days(v):
    if any(d < 1 or d > 7 for d in v):
        raise ValueError("days are 1 (Monday) to 7 (Sunday)")
    return sorted(set(v))


def _ids(v):
    out = []
    for x in v:
        x = x.strip()
        if not x or len(x) > 200:
            raise ValueError("must be a list of user ids")
        if x not in out:
            out.append(x)
    return out


def _keep(v):
    if v not in KEEP_SCORES_CHOICES:
        raise ValueError("must be 0 (forever), 1, 2 or 5 years")
    return v


GROUPS = [
    Group("games", "Games", "A game that's off is hidden; its scores are kept."),
    Group("looks", "Looks and scores"),
    Group("children", "Children"),
    Group("ha", "Home Assistant"),
    Group("ai", "AI levels", "More levels for every game, made by an AI model: your own (Ollama) or a service. Only the "
                             "game's rules and recent levels are sent, never anything about the people here. Every level "
                             "is checked before anyone plays it."),
]

SETTINGS = [
    # game ids switched off (new games start on)
    Setting("disabled_games", [], "Games", group="games", type=list[str], max_length=100, validators=[_games]),
    Setting("default_look", games.DEFAULT_LOOK, "Default look", group="looks", type=str, validators=[_look],
            choices=list(games.LOOKS.items()), help="For everyone who hasn't picked their own on Settings."),
    Setting("brick_powerups", True, "Brick Breaker power-ups", group="games",
            help="Off: only Classic play (no wider paddle, slower ball, extra ball or extra life)."),
    Setting("leaderboard", True, "Leaderboard", group="looks", help="Off: only personal bests are shown."),
    # ISO weekdays, Monday = 1
    Setting("school_days", [1, 2, 3, 4, 5], "School days", group="children", type=list[int], max_length=7,
            validators=[_days],
            help="Days that use school-day limits. Every other day, and every holiday below, uses weekend limits."),
    Setting("limit_warnings", False, "Limit warnings to parents", group="children",
            help="A phone notice when a child has 5 minutes left today, with “Add 15 minutes”."),
    # HA user ids of the admins to tell
    Setting("limit_warning_admins", [], "Who gets limit warnings", group="children", type=list[str], max_length=50,
            validators=[_ids], help="None ticked = every admin."),
    Setting("notify_records", False, "Notify new records", group="ha",
            help="A phone notification to the household when someone sets a new record. Each person can opt out on "
                 "Settings."),
    # a phone notification for a "play with someone" invite (SPEC §13)
    Setting("notify_invites", True, "Invites by phone notification", group="ha",
            help="A phone notification when someone invites another person to play together (with Join and Not now). "
                 "The invite also shows on the Games page. Each person can opt out on Settings."),
    Setting("ha_sensors", False, "Home Assistant sensors", group="ha",
            help="Publishes sensor.household_arcade_<game>_record, sensor.household_arcade_<person>_played_today and "
                 "binary_sensor.household_arcade_<person>_playing."),
    Setting("keep_scores_years", 0, "Keep scores for", group="looks", type=int, validators=[_keep],
            choices=[(0, "Forever"), (1, "1 year"), (2, "2 years"), (5, "5 years")],
            help="Older games are removed; each person's best per game and mode is always kept."),
    # the daily challenge (SPEC "Daily challenge"): off = hidden and refused
    Setting("show_daily_challenges", False, "Show daily challenges", group="games",
            help="Off by default. On: Home shows three games with the same puzzle for everyone each day, one ranked try "
                 "each. Turning it off hides them and keeps the scores."),
    # hints per Sudoku puzzle (unlimited in Practice)
    Setting("sudoku_hints", 3, "Sudoku hints per puzzle", group="games", min=0, max=20,
            help="How many hints a ranked Sudoku allows (0 = none). Practice always has as many as you like. Each hint "
                 "adds 30 seconds."),
    # AI-made levels (SPEC §11): the same AI settings as Finance Dashboard's
    Setting("ai_levels_enabled", False, "AI levels", group="ai", help="Off: games play their built-in levels only."),
    Setting("ai_provider", "ollama", "Provider", group="ai", type=str, validators=[_provider],
            choices=list(PROVIDER_LABELS.items())),
    Setting("ai_url", "", "Address", group="ai", kind="url", max_length=500, validators=[_url]),
    Setting("ai_model", "", "Model", group="ai", max_length=200, validators=[_one_line],
            placeholder="e.g. a model you have pulled"),
    Setting("ai_api_key", "", "Access key", group="ai", secret=True, max_length=1000, validators=[_one_line],
            placeholder="Not set (only Anthropic Claude and some services need one)"),
    Setting("ai_max_output_tokens", 4000, "Most the model may write", group="ai", min=256, max=64000, unit="tokens"),
    Setting("ai_levels_auto", True, "Build ahead automatically", group="ai",
            help="When someone nears the last level, the next ones are built in the background."),
    Setting("ai_levels_ahead", 2, "Start building this many levels before the end", group="ai", min=1, max=5),
    Setting("ai_levels_batch", 5, "Levels per build", group="ai", min=1, max=20),
    Setting("ai_levels_daily_limit", 20, "Most levels built a day", group="ai", min=0, max=500,
            help="0 = no limit."),
    Setting("ai_levels_review", False, "Check new levels before they're played", group="ai",
            help="New levels wait on Admin → Levels until an admin approves them."),
    Setting("ai_price_in", 0.0, "Price per million input tokens", group="ai", min=0, max=1000, page={"step": 0.01},
            help="Prices are only for the cost estimate on AI usage — copy them from your provider's price list. "
                 "Leave 0 for your own model."),
    Setting("ai_price_out", 0.0, "Price per million output tokens", group="ai", min=0, max=1000, page={"step": 0.01}),
]

REGISTRY = settings_core.Registry(
    SETTINGS, groups=GROUPS, connect=db.get_conn, model_config={"str_strip_whitespace": True, "strict": True},
    unknown_message=lambda keys: "Unknown setting(s): " + ", ".join(sorted(map(str, keys))),
    not_object_message="Send an object of settings to change.", now=config.now_iso, generation=db.generation,
    fallback_on_error=True, logger=logger)

AppSettings = REGISTRY.model
DEFAULTS: dict = REGISTRY.defaults
KEYS = REGISTRY.keys
LABELS = REGISTRY.labels
META: dict = REGISTRY.meta()
# Never sent to the browser: the page is told only whether it is set.
SECRETS = set(REGISTRY.secrets)


def validate(values: dict) -> dict:
    return REGISTRY.validate(values)


def invalidate() -> None:
    REGISTRY.invalidate()


def all() -> dict:  # noqa: A001
    return REGISTRY.all()


def get(key: str):
    return REGISTRY.get(key)


def update(partial, user: dict | None) -> tuple[dict, set[str]]:
    """Validate the merged result and write only the keys that changed."""
    who = (user.get("username") or user.get("id")) if user else None
    _, changed = REGISTRY.update(partial, who)
    return all(), set(changed)


def public(values: dict) -> dict:
    """Values for the browser: secrets blanked."""
    return REGISTRY.public(values)


def payload() -> dict:
    return REGISTRY.payload(
        providers=[{"id": p, "label": PROVIDER_LABELS[p], "defaultUrl": DEFAULT_URLS[p]} for p in PROVIDERS])


def ai_url() -> str:
    """The address to use: the one set, else the provider's usual one (Ollama has none)."""
    return (get("ai_url") or DEFAULT_URLS.get(get("ai_provider"), "")).rstrip("/")


def ai_problem() -> str | None:
    """Why AI levels can't be built right now, or None."""
    v = all()
    if not v["ai_levels_enabled"]:
        return "AI levels are off (Admin → App settings)."
    if not v["ai_model"]:
        return "No AI model is set (Admin → App settings)."
    if not ai_url():
        return "Ollama needs its address (Admin → App settings)."
    if v["ai_provider"] == "anthropic" and not v["ai_api_key"]:
        return "Anthropic Claude needs an access key (Admin → App settings)."
    return None


# ---------------------------------------------------------------------------
# derived values
# ---------------------------------------------------------------------------

def game_enabled(game: str) -> bool:
    return games.exists(game) and game not in get("disabled_games")


def enabled_games() -> list[str]:
    off = set(get("disabled_games"))
    return [g for g in games.GAME_IDS if g not in off]


def modes_for(game: str) -> list[dict]:
    """The modes shown for a game: Brick Breaker's power-ups mode is hidden
    while the power-ups setting is off."""
    modes = [dict(m) for m in games.GAMES[game]["modes"]]
    if game == "brick" and not get("brick_powerups"):
        modes = [m for m in modes if m["id"] != "powerups"]
    return modes


def mode_allowed(game: str, mode: str) -> bool:
    return any(m["id"] == mode for m in modes_for(game))
