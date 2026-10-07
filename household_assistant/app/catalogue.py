"""The tools the household apps offer (HOUSEHOLD_ASSISTANT_SPEC.md §4, §5): learned over the bus with
`assist.tools.list`, kept in `assist_apps` / `assist_tools`, and filtered for each person.

- `refresh(apps)` asks those apps (all that can answer, by default) for their tool list; the answers arrive on
  the bus (`on_reply`) and replace what was stored for that app. The admin's on/off for the app is kept.
- `check()` (every minute): asks an app it hasn't heard a list from, one whose version changed since (it said
  `hello` with a new one), or one last asked more than a day ago.
- `for_user(conn, user)` — the tools this person may be offered: apps the admin turned on here, that turned on
  their own *Answer the Household Assistant*, and said hello in the last 24 hours; children get only `person`
  tools, and only while *Children may ask* is on. Never anything of Household Vault's.

What an app sends is checked, not trusted: names, argument types and lengths, and at most 24 tools.
"""
from __future__ import annotations

import json
import logging
import re
from datetime import datetime, timedelta, timezone

from . import config, db, settings
from .common import app_bus as bus

logger = logging.getLogger("catalogue")

LIST_KIND = "assist.tools.list"
CALL_KIND = "assist.tool.call"
LIST_EXPIRES = timedelta(minutes=2)
STALE = timedelta(hours=24)
ACTIVE = timedelta(hours=24)
NEVER_APPS = ("household_vault",)
NEVER_AREAS = ("vault",)

_TOOL_RE = re.compile(r"^[a-z][a-z0-9_]{0,31}(\.[a-z][a-z0-9_]{0,31}){1,2}$")
_ARG_RE = re.compile(r"^[a-z][a-z0-9_]{0,31}$")
ARG_TYPES = ("string", "number", "boolean", "enum", "date", "month")
MAX_TOOLS = 24
MAX_ARGS = 8


def _iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat(timespec="seconds")


def _parse(value) -> datetime | None:
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _text(value, limit: int) -> str:
    return " ".join(str(value).split())[:limit] if isinstance(value, (str, int, float)) else ""


def clean_tool(raw) -> dict | None:
    """One tool of an app's list, reduced to what the assistant uses, or None when it isn't well formed."""
    if not isinstance(raw, dict) or not isinstance(raw.get("name"), str) or not _TOOL_RE.match(raw["name"]):
        return None
    if raw["name"].split(".", 1)[0] in NEVER_AREAS:
        return None
    args = {}
    for name, a in (raw.get("args") or {}).items() if isinstance(raw.get("args"), dict) else ():
        if not isinstance(name, str) or not _ARG_RE.match(name) or not isinstance(a, dict) or a.get("type") not in ARG_TYPES:
            return None
        arg = {"type": a["type"]}
        if a.get("required") is True:
            arg["required"] = True
        if a.get("what"):
            arg["what"] = _text(a["what"], 200)
        if a["type"] == "enum":
            values = [v for v in (a.get("values") or []) if isinstance(v, (str, int))][:20] \
                if isinstance(a.get("values"), list) else []
            if not values:
                return None
            arg["values"] = [_text(v, 40) if isinstance(v, str) else v for v in values]
        for k in ("min", "max"):
            if isinstance(a.get(k), (int, float)) and not isinstance(a.get(k), bool):
                arg[k] = a[k]
        args[name] = arg
        if len(args) > MAX_ARGS:
            return None
    return {"name": raw["name"], "what": _text(raw.get("what"), 400), "args": args,
            "returns": _text(raw.get("returns"), 200), "acts": raw.get("acts") is True,
            "scope": "household" if raw.get("scope") == "household" else "person",
            "examples": [_text(e, 120) for e in (raw.get("examples") or [])[:3] if isinstance(e, str)]
            if isinstance(raw.get("examples"), list) else []}


# --------------------------------------------------------------------------------------------- asking

def _bus_apps() -> list[dict]:
    return bus.apps() if bus.default.started else []


def refresh(apps=None) -> list[str]:
    """Ask `apps` (default: every app that answers the assistant) for their tool lists. Returns those asked."""
    asked = []
    for a in _bus_apps():
        if a["slug"] in NEVER_APPS or LIST_KIND not in a["can"] or not a["active"]:
            continue
        if apps is not None and a["slug"] not in apps:
            continue
        try:
            bus.send(a["slug"], LIST_KIND, {}, ref=f"tools:{a['slug']}", expires_in=LIST_EXPIRES)
        except bus.BusError as e:
            logger.info("Couldn't ask %s for its tools: %s", a["slug"], e)
            continue
        with db.get_conn() as conn:
            conn.execute("INSERT INTO assist_apps (app, name, app_version) VALUES (?, ?, ?) ON CONFLICT(app) DO "
                         "UPDATE SET name = excluded.name", (a["slug"], a["name"], None))
        asked.append(a["slug"])
    return asked


def check() -> list[str]:
    """Every minute: ask the apps whose list is missing, out of date (a new version) or a day old."""
    with db.get_conn() as conn:
        known = {r["app"]: r for r in conn.execute("SELECT app, app_version, learned_at FROM assist_apps")}
    now = datetime.now(timezone.utc)
    due = []
    for a in _bus_apps():
        if a["slug"] in NEVER_APPS or LIST_KIND not in a["can"] or not a["active"]:
            continue
        k = known.get(a["slug"])
        learned = _parse(k["learned_at"]) if k and k["learned_at"] else None
        if k is None or learned is None or k["app_version"] != a["version"] or now - learned > STALE:
            due.append(a["slug"])
    return refresh(due) if due else []


def _on_list_answer(msg, conn) -> None:
    """The bus's callback for answers to `assist.tools.list` (inside the bus's transaction)."""
    app = msg.from_app
    if not isinstance(app, str) or app in NEVER_APPS:
        return
    row = conn.execute("SELECT name, version FROM bus_apps WHERE slug = ?", (app,)).fetchone()
    name, version = (row[0], row[1]) if row else (None, None)
    now = _iso(datetime.now(timezone.utc))
    conn.execute("INSERT INTO assist_apps (app, name) VALUES (?, ?) ON CONFLICT(app) DO UPDATE SET "
                 "name = COALESCE(excluded.name, assist_apps.name)", (app, name))
    if msg.kind != "ack":
        detail = msg.data.get("detail")
        error = ("The tool list is too big." if detail == "answer too large" else
                 "Didn't answer." if msg.reason == "expired" else
                 f"Refused: {msg.reason}{' (' + str(detail)[:60] + ')' if detail else ''}.")
        conn.execute("UPDATE assist_apps SET error = ?, learned_at = ?, app_version = ? WHERE app = ?",
                     (error, now, version, app))
        return
    result = msg.result if isinstance(msg.result, dict) else {}
    raw = result.get("tools") if isinstance(result.get("tools"), list) else []
    tools = [t for t in (clean_tool(x) for x in raw[:MAX_TOOLS]) if t]
    skipped = len(raw[:MAX_TOOLS]) - len(tools) + max(0, len(raw) - MAX_TOOLS)
    conn.execute("UPDATE assist_apps SET app_on = ?, learned_at = ?, app_version = ?, error = ? WHERE app = ?",
                 (1 if result.get("on") is True else 0, now, version,
                  f"{skipped} tool(s) weren't well formed and are left out." if skipped else None, app))
    old = {r[0]: r[1] for r in conn.execute("SELECT name, enabled FROM assist_tools WHERE app = ?", (app,))}
    conn.execute("DELETE FROM assist_tools WHERE app = ?", (app,))
    for t in tools:
        conn.execute("INSERT INTO assist_tools (app, name, app_version, spec, learned_at, enabled) VALUES "
                     "(?, ?, ?, ?, ?, ?)", (app, t["name"], version, json.dumps(t, separators=(",", ":")), now,
                                            old.get(t["name"], 1)))
    logger.info("Learned %d tool(s) from %s.", len(tools), app)


bus.on_reply("tools:", _on_list_answer)


# --------------------------------------------------------------------------------------------- reading

def _active_apps(conn) -> dict[str, str]:
    """slug -> name of the apps that said hello in the last 24 hours."""
    cutoff = _iso(datetime.now(timezone.utc) - ACTIVE)
    out = {}
    for r in conn.execute("SELECT slug, name, last_seen FROM bus_apps"):
        seen = _parse(r[2])
        if seen and _iso(seen) >= cutoff:
            out[r[0]] = r[1] or r[0]
    return out


def app_names(conn) -> dict[str, str]:
    names = {r[0]: r[1] for r in conn.execute("SELECT slug, COALESCE(name, slug) FROM bus_apps")}
    names.update({r[0]: r[1] for r in conn.execute("SELECT app, name FROM assist_apps WHERE name IS NOT NULL")})
    return names


def for_user(conn, user: dict) -> dict[str, dict]:
    """tool name -> {app, appName, spec} that this person may be offered."""
    active = _active_apps(conn)
    child = bool(user.get("is_child"))
    rows = conn.execute("SELECT t.app, t.spec, a.name FROM assist_tools t JOIN assist_apps a ON a.app = t.app "
                        "WHERE a.enabled = 1 AND a.app_on = 1 AND t.enabled = 1 ORDER BY t.app, t.name").fetchall()
    out = {}
    for app, spec, name in rows:
        if app not in active or app in NEVER_APPS:
            continue
        try:
            t = json.loads(spec)
        except ValueError:
            continue
        if child and t.get("scope") != "person":
            continue
        out[t["name"]] = {"app": app, "appName": name or active[app], "spec": t}
    return out


def admin_rows(conn) -> list[dict]:
    """Admin → Apps: each app that offered tools, with its switch, its own switch and its tools in words."""
    active = _active_apps(conn)
    tools: dict[str, list] = {}
    for app, spec, enabled in conn.execute("SELECT app, spec, enabled FROM assist_tools ORDER BY app, name"):
        try:
            t = json.loads(spec)
        except ValueError:
            continue
        tools.setdefault(app, []).append({"name": t["name"], "what": t["what"], "acts": t["acts"],
                                          "scope": t["scope"], "enabled": bool(enabled)})
    out = []
    for r in conn.execute("SELECT app, name, app_version, learned_at, app_on, enabled, error FROM assist_apps "
                          "ORDER BY COALESCE(name, app)"):
        out.append({"app": r["app"], "name": r["name"] or r["app"], "version": r["app_version"],
                    "learnedAt": r["learned_at"], "appOn": bool(r["app_on"]), "enabled": bool(r["enabled"]),
                    "active": r["app"] in active, "error": r["error"], "tools": tools.get(r["app"], [])})
    return out


def set_enabled(conn, app: str, enabled: bool) -> bool:
    return conn.execute("UPDATE assist_apps SET enabled = ? WHERE app = ?", (1 if enabled else 0, app)).rowcount > 0


def suggestions(tools: dict[str, dict], limit: int = 4) -> list[str]:
    """A few questions to start with: the first example of each app's tools, one per app."""
    out, seen = [], set()
    for name, t in tools.items():
        if t["app"] in seen or t["spec"].get("acts"):
            continue
        ex = (t["spec"].get("examples") or [None])[0]
        if ex:
            out.append(ex)
            seen.add(t["app"])
        if len(out) >= limit:
            break
    return out


def enabled_settings() -> dict:
    return {"children_may_ask": bool(settings.get("children_may_ask"))}


def now_iso() -> str:
    return _iso(config.utcnow())
