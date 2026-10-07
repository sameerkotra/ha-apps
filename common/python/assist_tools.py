"""Answering the Household Assistant: an app's tools, like tools on an MCP server, over the app bus
(HOUSEHOLD_ASSISTANT_SPEC.md §4–7; APP_MESSAGES_SPEC.md §6.6).

Shared by the household apps (common/python/assist_tools.py, copied into an app's app/common/ next to
app_bus.py by tools/sync_common.py). Standard library only. The app writes its catalogue once, in its own
`tools.py`, next to the handler that answers each tool:

    from .common import app_bus as bus, assist_tools

    tools = assist_tools.Catalogue(
        "todo",                                         # the app's area: every tool is todo.<thing>[.<verb>]
        targets=[r"/today", r"/lists/[A-Za-z0-9_-]{1,64}"],   # the app's own routes a link may open
        actor=lambda conn, uid: …,                      # the app's user dict for an enabled user, else None
        enabled=lambda conn: …,                         # the admin's "Answer the Household Assistant"
        person_enabled=lambda conn, user: …,            # optional: "Let the Household Assistant answer for me"
        panel=lambda: config.INGRESS_PANEL,             # "/<full slug>" or None
        busy=lambda: …,                                 # optional: raise bus.Nack("busy") while restoring
    )

    @tools.tool("todo.tasks", "Tasks on the person's lists …",
                args={"when": assist_tools.Arg("enum", values=("today", "week"), required=True, what="…")},
                returns="tasks with due date, who and list", examples=("What's on my list today?",))
    def tasks(ctx):                                     # ctx.conn, ctx.user, ctx.args, ctx.link(label, target)
        return ctx.result("3 tasks today: …", items=[…], links=[ctx.link("Today", "/today")])

    tools.install(bus)                                  # before bus.start(): adds both kinds to `can`

What the bus carries (APP_MESSAGES_SPEC §6.6):

- `assist.tools.list` `{}` → `ack {result: {tools: [{name, what, args, returns, acts, scope, examples}], on}}`;
  `on` is the admin switch, so the assistant can say why an app doesn't answer.
- `assist.tool.call` `{tool, args, requested_by, question, confirm?, confirmed_at?}` →
  `ack {result: {question, tool, text, items, links, more}}`, at most 6 KB. Refusals: an unknown tool
  `nack not_found` (detail `tool`); a bad argument `nack invalid` (the argument's name); a tool that changes
  something without `confirm: true` `nack not_allowed` (`confirm`); `requested_by` not an enabled user
  `nack not_allowed` (`no_access`); the admin switch off (`off`); the person's own switch off (`person_off`);
  a child asking a tool not open to children (`child`).

Every handler answers as `requested_by` would see it in the app. Unknown arguments are ignored, as unknown
fields are everywhere on the bus (APP_MESSAGES_SPEC §6); the model is told the arguments, not trusted with them.
"""
import json
import logging
import os
import re
import urllib.request
from datetime import date

from . import app_bus

MAX_TOOLS = 24
MAX_ARGS = 8
MAX_RESULT = 6 * 1024          # bytes of a tool's result (HOUSEHOLD_ASSISTANT_SPEC §6.2)
MAX_CATALOGUE = 7 * 1024       # bytes of the tool list's result: the whole event stays under the bus's 8 KB
MAX_ITEMS = 50
MAX_LINKS = 5
MAX_TEXT = 2000                # characters of a result's own summary
MAX_STRING = 200               # characters of a string argument (unless the argument says otherwise)
ARG_TYPES = ("string", "number", "boolean", "enum", "date", "month")
SCOPES = ("person", "household")

_NAME_PART = r"[a-z][a-z0-9_]{0,31}"
_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_MONTH_RE = re.compile(r"^\d{4}-(0[1-9]|1[0-2])$")
_QUESTION_RE = re.compile(r"^[0-9A-Za-z_-]{1,64}$")
_PANEL_RE = re.compile(r"^/[a-z0-9]{1,16}_[a-z0-9_]{1,64}$")


logger = logging.getLogger("assist_tools")


def sidebar_page(app_slug: str, *, token: str = "", supervisor_api: str = "http://supervisor", info=None,
                 hostname: str | None = None) -> str | None:
    """The app's sidebar page in Home Assistant, "/<full slug>", for the links in its answers (APP_MESSAGES_SPEC
    §6.5), or None when it has none. From the Supervisor (`GET /addons/self/info`: `slug`, `ingress_panel`; allowed
    for every app with its token); when that can't be asked, from the container's host name, which the Supervisor
    sets to the full slug with "-" for "_". Accepted only as `/<8 hex or local>_<app_slug>`."""
    ok = re.compile(rf"^([0-9a-f]{{8}}|local)_{re.escape(app_slug)}$")
    if info is None and token:
        req = urllib.request.Request(f"{supervisor_api.rstrip('/')}/addons/self/info",
                                     headers={"Authorization": f"Bearer {token}"})
        try:
            with urllib.request.urlopen(req, timeout=10) as resp:
                body = json.loads(resp.read(256 * 1024).decode("utf-8"))
            info = body.get("data") if isinstance(body, dict) else None
        except Exception as e:                                   # noqa: BLE001 — the host name below
            logger.info("Couldn't read this app's info from the Supervisor (%s).", type(e).__name__)
    if isinstance(info, dict) and isinstance(info.get("slug"), str):
        return "/" + info["slug"] if ok.match(info["slug"]) and info.get("ingress_panel") is not False else None
    host = (hostname if hostname is not None else os.environ.get("HOSTNAME", "")).strip().lower()
    slug = host.replace("-", "_")
    return "/" + slug if ok.match(slug) else None


def _size(obj) -> int:
    return len(json.dumps(obj, separators=(",", ":"), ensure_ascii=False).encode("utf-8"))


class Arg:
    """One argument of a tool: flat, one of ARG_TYPES. `what` is read by the model."""

    def __init__(self, type: str, what: str = "", *, required: bool = False, values=None, min=None, max=None,
                 max_length: int = MAX_STRING):
        if type not in ARG_TYPES:
            raise ValueError(f"bad argument type {type!r}")
        if type == "enum" and not values:
            raise ValueError("an enum argument needs values")
        self.type, self.what, self.required = type, what, required
        self.values = tuple(values) if values else None
        self.min, self.max, self.max_length = min, max, max_length

    def spec(self) -> dict:
        out = {"type": self.type}
        if self.required:
            out["required"] = True
        if self.what:
            out["what"] = self.what
        if self.values:
            out["values"] = list(self.values)
        if self.min is not None:
            out["min"] = self.min
        if self.max is not None:
            out["max"] = self.max
        return out

    def clean(self, name: str, value):
        """The checked value, or Nack invalid with the argument's name."""
        bad = app_bus.Nack("invalid", name)
        if self.type == "string":
            if not isinstance(value, str):
                raise bad
            value = " ".join(value.split())
            if not 1 <= len(value) <= self.max_length:
                raise bad
            return value
        if self.type == "number":
            if isinstance(value, bool) or not isinstance(value, (int, float)) or value != value:
                raise bad
            if (self.min is not None and value < self.min) or (self.max is not None and value > self.max):
                raise bad
            return value
        if self.type == "boolean":
            if not isinstance(value, bool):
                raise bad
            return value
        if self.type == "enum":
            if value not in self.values:
                raise bad
            return value
        if self.type == "date":
            if not isinstance(value, str) or not _DATE_RE.match(value):
                raise bad
            try:
                date.fromisoformat(value)
            except ValueError:
                raise bad from None
            return value
        if not isinstance(value, str) or not _MONTH_RE.match(value):     # month
            raise bad
        return value


class Tool:
    def __init__(self, name, what, fn, args, returns, acts, scope, examples, children):
        self.name, self.what, self.fn, self.args = name, what, fn, args
        self.returns, self.acts, self.scope = returns, acts, scope
        self.examples, self.children = tuple(examples), children

    def spec(self) -> dict:
        out = {"name": self.name, "what": self.what, "args": {k: a.spec() for k, a in self.args.items()},
               "returns": self.returns, "acts": self.acts, "scope": self.scope}
        if self.examples:
            out["examples"] = list(self.examples)
        return out


class Context:
    """What a tool's handler gets: the bus's connection (inside the answer's transaction), the asking person
    as the app's own user dict, the checked arguments and the call's details."""

    def __init__(self, catalogue, conn, msg, user: dict, args: dict, tool: Tool):
        self.catalogue, self.conn, self.msg, self.user, self.args, self.tool = catalogue, conn, msg, user, args, tool
        self.question = msg.data.get("question") if _QUESTION_RE.match(str(msg.data.get("question") or "")) else None
        self.confirmed = msg.data.get("confirm") is True

    def link(self, label: str, target: str | None = None) -> dict:
        """A link back to the app (§6.3): its own sidebar page plus one of its own routes. A target that isn't
        one of the catalogue's patterns is a bug in the app (ValueError), never sent."""
        return self.catalogue.link(label, target)

    def result(self, text: str, items=None, links=None, more: bool = False) -> dict:
        return Result(text, items, links, more)


class Result(dict):
    """A tool's answer before fitting: text, items (flat rows), links, more."""

    def __init__(self, text, items=None, links=None, more=False):
        super().__init__(text=text, items=list(items or []), links=list(links or []), more=bool(more))


def _flat(row) -> dict:
    """An item as a flat row of strings, numbers, booleans and nulls (anything else becomes its text)."""
    out = {}
    for k, v in (row or {}).items():
        if v is None or isinstance(v, (bool, int, float)):
            out[str(k)] = v
        else:
            out[str(k)] = str(v)[:500]
    return out


def fit(result: dict, limit: int = MAX_RESULT) -> dict:
    """Keep a result at most `limit` bytes: drop items from the end first (setting `more`), then shorten text."""
    while _size(result) > limit and result["items"]:
        result["items"].pop()
        result["more"] = True
    if _size(result) > limit:
        result["more"] = True
        text = result["text"]
        while text and _size(dict(result, text=text + "…")) > limit:
            over = _size(dict(result, text=text + "…")) - limit      # bytes ≥ characters, so this never undershoots
            text = text[:max(0, len(text) - over)]
        result["text"] = text + "…"
    return result


class Catalogue:
    def __init__(self, area, *, targets=(), actor, enabled, person_enabled=None, panel=None, busy=None,
                 is_child=None):
        areas = (area,) if isinstance(area, str) else tuple(area)
        for a in areas:
            if not re.fullmatch(_NAME_PART, a):
                raise ValueError(f"bad area {a!r}")
        self.areas = areas
        self.targets = [re.compile(t) for t in targets]
        self.actor, self.enabled, self.person_enabled = actor, enabled, person_enabled
        self.panel, self.busy = panel, busy
        self.is_child = is_child or (lambda user: bool(user.get("is_child")))
        self.tools: dict[str, Tool] = {}

    # -- writing the catalogue -------------------------------------------------------------------
    def tool(self, name: str, what: str, *, args=None, returns: str = "", acts: bool = False,
             scope: str = "person", examples=(), children: bool = False):
        """Decorator: `fn(ctx) -> Result | dict | Nack` answers the tool `name`."""
        if not re.fullmatch(rf"{_NAME_PART}(\.{_NAME_PART}){{1,2}}", name or ""):
            raise ValueError(f"bad tool name {name!r}")
        if name.split(".", 1)[0] not in self.areas:
            raise ValueError(f"{name} is outside this app's area {self.areas}")
        if name in self.tools:
            raise ValueError(f"{name} twice")
        if len(self.tools) >= MAX_TOOLS:
            raise ValueError(f"at most {MAX_TOOLS} tools")
        args = dict(args or {})
        if len(args) > MAX_ARGS:
            raise ValueError(f"{name}: at most {MAX_ARGS} arguments")
        for k, a in args.items():
            if not re.fullmatch(_NAME_PART, k) or not isinstance(a, Arg):
                raise ValueError(f"{name}: bad argument {k!r}")
        if scope not in SCOPES:
            raise ValueError(f"{name}: bad scope {scope!r}")
        if not what or len(what) > 400:
            raise ValueError(f"{name}: `what` is one or two sentences")
        if len(examples) > 3:
            raise ValueError(f"{name}: at most 3 examples")

        def deco(fn):
            self.tools[name] = Tool(name, what, fn, args, returns, acts, scope, examples, children)
            return fn
        return deco

    def spec(self) -> list:
        return [t.spec() for t in self.tools.values()]

    def check(self) -> None:
        """For the app's tests: the catalogue fits on the bus."""
        size = _size({"tools": self.spec(), "on": True})
        if size > MAX_CATALOGUE:
            raise AssertionError(f"the tool list is {size} bytes (at most {MAX_CATALOGUE})")

    def link(self, label: str, target: str | None = None) -> dict:
        if target is not None and not any(p.fullmatch(target) for p in self.targets):
            raise ValueError(f"{target!r} isn't one of this app's link targets")
        out = {"label": str(label)[:80]}
        panel = self.panel() if self.panel else None
        if isinstance(panel, str) and _PANEL_RE.match(panel):
            out["panel"] = panel
        if target:
            out["target"] = target
        return out

    # -- answering ------------------------------------------------------------------------------
    def install(self, bus) -> None:
        """Register the two kinds on the app's bus (the module or an AppBus); call before bus.start()."""
        bus.handler("assist.tools.list")(self.on_list)
        bus.handler("assist.tool.call")(self.on_call)

    def on_list(self, msg, conn):
        if self.busy:
            self.busy()
        return {"tools": self.spec(), "on": bool(self.enabled(conn))}

    def on_call(self, msg, conn):
        if self.busy:
            self.busy()
        d = msg.data
        name = d.get("tool")
        tool = self.tools.get(name) if isinstance(name, str) else None
        if tool is None:
            raise app_bus.Nack("not_found", "tool")
        uid = d.get("requested_by")
        if not isinstance(uid, str) or not uid or len(uid) > 64:
            raise app_bus.Nack("invalid", "requested_by")
        q = d.get("question")
        if q is not None and not _QUESTION_RE.match(str(q)):
            raise app_bus.Nack("invalid", "question")
        if not self.enabled(conn):
            raise app_bus.Nack("not_allowed", "off")
        user = self.actor(conn, uid)
        if not user:
            raise app_bus.Nack("not_allowed", "no_access")
        if self.person_enabled and not self.person_enabled(conn, user):
            raise app_bus.Nack("not_allowed", "person_off")
        if not tool.children and self.is_child(user):
            raise app_bus.Nack("not_allowed", "child")
        raw = d.get("args") if d.get("args") is not None else {}
        if not isinstance(raw, dict):
            raise app_bus.Nack("invalid", "args")
        args = {}
        for k, a in tool.args.items():
            v = raw.get(k)
            if v is None:
                if a.required:
                    raise app_bus.Nack("invalid", k)
                continue
            args[k] = a.clean(k, v)
        if tool.acts and d.get("confirm") is not True:
            raise app_bus.Nack("not_allowed", "confirm")
        res = tool.fn(Context(self, conn, msg, user, args, tool))
        if isinstance(res, app_bus.Nack):
            raise res
        if not isinstance(res, dict) or not isinstance(res.get("text"), str):
            raise TypeError(f"{tool.name} returned {type(res).__name__}, not a result")
        text = res["text"].strip()
        out = {"question": q if isinstance(q, str) else None, "tool": tool.name,
               "text": text if len(text) <= MAX_TEXT else text[:MAX_TEXT - 1] + "…",
               "items": [_flat(r) for r in (res.get("items") or [])[:MAX_ITEMS]],
               "links": [dict(x) for x in (res.get("links") or [])[:MAX_LINKS]],
               "more": bool(res.get("more")) or len(res.get("items") or []) > MAX_ITEMS or len(text) > MAX_TEXT}
        return fit(out)
