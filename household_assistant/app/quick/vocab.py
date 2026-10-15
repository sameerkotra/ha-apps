"""The household's own names, per person (HOUSEHOLD_ASSISTANT_SPEC.md §17.5): their Todo lists, the shopping list's
items and stores, their Splitpot groups and the people in them, Finance's categories, Arcade's games — so "what's on
the Costco list" or "how much does Ravi owe" is understood. Their accounts' names (Finance), and other names the apps
give for a list item ("dahi" for Curd), and the admin's own words (learn.taught) too.

Fetched with the apps' own tools, as the person (`requested_by`), at most once every REFRESH per person and tool, in
the background after a question (never holding one up). Kept in memory only — never in the database, never logged.
The people who use the assistant (its users table) are names too.
"""
from __future__ import annotations

import logging
import threading
import time
from datetime import timedelta

from .. import catalogue, db
from ..common import app_bus as bus
from .match import Vocab

logger = logging.getLogger("quick.vocab")

REFRESH = 600.0                 # seconds between fetches of one tool's names for one person
EXPIRES = timedelta(seconds=20)
MAX_NAMES = 200


def _lists(items):
    """Todo's lists, the person's own first (a new task with no list named goes to the first)."""
    rows = [i for i in items if isinstance(i, dict) and isinstance(i.get("list"), str)]
    rows.sort(key=lambda i: 0 if i.get("kind") in ("personal", "own", "private") else 1)
    return {"lists": [i["list"] for i in rows]}


def _shopping(items):
    out = {"items": [], "stores": [], "aliases": []}
    for i in items:
        if not isinstance(i, dict):
            continue
        if isinstance(i.get("item"), str):
            out["items"].append(i["item"])
            for other in str(i.get("aliases") or "").split(","):      # other names people gave it in the app
                if other.strip():
                    out["aliases"].append(("item", " ".join(other.split())[:60], i["item"]))
        if isinstance(i.get("cheapest_at"), str):
            out["stores"].append(i["cheapest_at"])
    return out


def _splitpot(items):
    out = {"groups": [], "people": []}
    for i in items:
        if not isinstance(i, dict):
            continue
        if isinstance(i.get("group"), str):
            out["groups"].append(i["group"])
        for k in ("from", "to"):
            if isinstance(i.get(k), str) and i[k] != "Unknown":
                out["people"].append(i[k])
    return out


def _field(name, key):
    def f(items):
        return {name: [i[key] for i in items if isinstance(i, dict) and isinstance(i.get(key), str)]}
    return f


SOURCES = {
    "todo.lists": _lists,
    "receipt.shopping_list": _shopping,
    "splitpot.balances": _splitpot,
    "finance.summary": _field("categories", "category"),
    "arcade.scores": _field("games", "game"),
    "finance.balances": lambda items: {"accounts": [str(i["account"]).split(" ••")[0] for i in items
                                                    if isinstance(i, dict) and isinstance(i.get("account"), str)]},
}

_lock = threading.Lock()
_cache: dict[str, dict[str, tuple[float, dict]]] = {}      # user id -> tool -> (when, {kind: [names]})
_pending: dict[str, float] = {}                             # "uid|tool" -> when it was asked


def _clean(values) -> list[str]:
    out = []
    for v in values:
        v = " ".join(str(v).split())[:80]
        if v and v not in out:
            out.append(v)
    return out[:MAX_NAMES]


def vocab_for(conn, user: dict, tools: dict) -> Vocab:
    """The names this person's questions may use now (whatever has been fetched so far)."""
    v = Vocab()
    with _lock:
        mine = dict(_cache.get(user["id"], {}))
    for tool, (_when, kinds) in mine.items():
        if tool not in tools:                       # no longer theirs to use: its names aren't either
            continue
        for kind, names in kinds.items():
            if kind == "aliases":
                v.aliases.extend(names)
            else:
                getattr(v, kind).extend(names)
    people = [r[0] for r in conn.execute("SELECT name FROM users WHERE enabled = 1")]
    v.people = _clean(v.people + people)
    for k in ("lists", "items", "stores", "groups", "categories", "games", "accounts"):
        setattr(v, k, _clean(getattr(v, k)))
    from . import learn
    v.words, admin_aliases = learn.taught(conn)
    v.aliases = admin_aliases + v.aliases[:MAX_NAMES]
    v.app_words = {t: list((e["spec"].get("quick") or {}).get("words") or []) for t, e in tools.items()
                   if (e["spec"].get("quick") or {}).get("words")}
    return v


def refresh(user: dict, tools: dict, *, force: bool = False) -> list[str]:
    """Ask the apps for the person's names that are missing or older than REFRESH; returns the tools asked."""
    if not bus.default.started:
        return []
    now = time.monotonic()
    asked = []
    for tool in SOURCES:
        t = tools.get(tool)
        if t is None:
            continue
        key = f"{user['id']}|{tool}"
        with _lock:
            when = _cache.get(user["id"], {}).get(tool, (None, None))[0]
            if not force and when is not None and now - when < REFRESH:
                continue
            if _pending.get(key) and now - _pending[key] < EXPIRES.total_seconds():
                continue
            _pending[key] = now
        try:
            bus.send(t["app"], catalogue.CALL_KIND, {"tool": tool, "args": {}, "requested_by": user["id"],
                                                     "question": "vocab"},
                     ref=f"vocab:{user['id']}|{tool}", expires_in=EXPIRES)
            asked.append(tool)
        except (bus.BusError, ValueError) as e:
            logger.info("Couldn't ask %s for names: %s", t["app"], e)
            with _lock:
                _pending.pop(key, None)
    return asked


def _on_answer(msg, conn) -> None:
    """Answers to the name fetches (inside the bus's transaction): kept in memory, never stored."""
    ref = msg.ref[len("vocab:"):] if isinstance(msg.ref, str) else ""
    uid, _, tool = ref.partition("|")
    with _lock:
        _pending.pop(ref, None)
    if msg.kind != "ack" or tool not in SOURCES or not uid:
        return
    result = msg.result if isinstance(msg.result, dict) else {}
    items = result.get("items") if isinstance(result.get("items"), list) else []
    kinds = {k: (_clean(v) if k != "aliases" else v[:MAX_NAMES]) for k, v in SOURCES[tool](items).items()}
    with _lock:
        _cache.setdefault(uid, {})[tool] = (time.monotonic(), kinds)


bus.on_reply("vocab:", _on_answer)


def forget(user_id: str | None = None) -> None:
    """Drop the names fetched (for one person, or everyone: a restore, the tests)."""
    with _lock:
        if user_id is None:
            _cache.clear()
            _pending.clear()
        else:
            _cache.pop(user_id, None)


def set_for_tests(user_id: str, tool: str, kinds: dict) -> None:
    with _lock:
        _cache.setdefault(user_id, {})[tool] = (time.monotonic(), kinds)


def refresh_later(user: dict) -> None:
    """After a question: fetch what's stale on a thread of its own (a question never waits for it)."""
    def go():
        try:
            with db.get_conn() as conn:
                tools = catalogue.for_user(conn, user)
            refresh(user, tools)
        except Exception:                                     # noqa: BLE001 — names are a nicety, never an error
            logger.debug("Fetching names failed", exc_info=True)
    threading.Thread(target=go, name="quick-vocab", daemon=True).start()
