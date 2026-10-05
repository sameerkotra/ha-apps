# Shared file: edit common/python/settings_core.py and run tools/sync_common.py; don't edit this copy. sha256=809119450f01a9d106d58679eec33f521babad58e3153e978997950a9f8250bb
"""App settings (Admin → App settings), shared by the household apps (common/python/settings_core.py).

Each app lists its settings once — key, default, label, help, group, kind, limits, choices, restart and
secret flags — and builds a `Registry` from that list. The registry gives the app everything around the
`app_settings` table:

- validation: a Pydantic model built from the list (the same messages Pydantic always gave; extra
  rules are callbacks: `validators=` per setting, `check=` across settings), unknown keys refused;
- storage: one row per key (`key`, `value`, `updated_at`, `updated_by`), JSON by default (`encode` /
  `decode` for another format), read through a cache that every write drops (optionally also on the
  app's database generation, after a restore, or after `cache_ttl` seconds), so a change applies without
  a restart;
- the GET/PUT /api/admin/settings payload, the same shape in every app:
  `{values, defaults, meta, groups, secretsSet?}` plus the app's own extras. `meta[key]` describes the
  field for the page (label, help, group, kind, restartRequired and, when set, min, max, unit, choices,
  placeholder, showIf, enabledIf, hidden, …); common/static/settings.js draws Admin → App settings from it;
- backups: `scrub_secrets(conn)` blanks the secret settings in a backup copy, `saved_secrets()` /
  `keep_secrets(saved)` keep this install's secrets over a restored file that has none (backup_core does
  the SQL, on the `app_settings` table).

Needs nothing from the app's modules: the app passes `connect` (a context manager giving a sqlite3
connection; rows are read by index, so any row factory works) and, if it wants them, `now`,
`generation` and the hooks. Side effects of a change (re-publishing sensors, moving to another folder,
…) stay in the app: `update()` returns the changed keys, `on_write` runs inside the write transaction
(e.g. an activity-log row) and `on_change` after it.

Kinds: "bool", "int", "float", "text", "url", "choice" (a drop-down of `choices`), "secret" (write-only:
the page is only told whether it is set), "list" (a list of strings, drawn by the app's own renderer, or
as tick boxes when it has `choices`). The validation type follows the kind unless `type=` says otherwise
(`Literal[...]` for a choice, `int` for a list of numbers, …).
"""
from __future__ import annotations

import contextlib
import json
import logging
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Annotated, Any, Callable, Iterable, Literal, Optional, Sequence

from pydantic import AfterValidator, BaseModel, BeforeValidator, ConfigDict, Field, ValidationError, create_model

__all__ = ["Setting", "Group", "Registry", "SettingsError", "by_label", "by_key", "clean_message"]


class SettingsError(ValueError):
    """A bad settings update: the message is shown to the admin as it is (a 422)."""


@dataclass
class Setting:
    """One App setting. Only `key` and `default` are required."""
    key: str
    default: Any
    label: str = ""
    help: str = ""
    group: str = ""
    kind: str = ""                      # inferred from the default (or "choice" / "secret") when blank
    min: Optional[float] = None         # numbers: ge / le
    max: Optional[float] = None
    choices: Optional[Sequence[tuple]] = None   # (value, label) pairs
    restart: bool = False               # takes effect only after the app restarts
    secret: bool = False                # never sent to the browser
    unit: str = ""
    placeholder: str = ""
    show_if: Optional[str] = None       # the page shows it only while this (bool) setting is on
    enabled_if: Optional[str] = None    # the page greys it out while this (bool) setting is off
    hidden: bool = False                # edited somewhere else, not on Admin → App settings
    type: Any = None                    # validation type (default: from the kind)
    strict: Optional[bool] = None       # Field(strict=…)
    min_length: Optional[int] = None
    max_length: Optional[int] = None
    pattern: Optional[str] = None
    validators: Sequence[Callable] = ()  # after-validators: f(value) or f(value, info) → value, ValueError
    before: Optional[Callable] = None    # a before-validator (runs on the raw value)
    page: dict = field(default_factory=dict)   # more for the page's meta (step, suggestions, …)

    def resolved_kind(self) -> str:
        if self.kind:
            return self.kind
        if self.secret:
            return "secret"
        if self.choices is not None:
            return "choice"
        d = self.default
        if isinstance(d, bool):
            return "bool"
        if isinstance(d, int):
            return "int"
        if isinstance(d, float):
            return "float"
        if isinstance(d, list):
            return "list"
        return "text"

    def annotation(self):
        """The Pydantic type with its constraints and validators."""
        if self.type is not None:
            base = self.type
        elif self.choices is not None and self.resolved_kind() == "choice":
            base = Literal[tuple(v for v, _ in self.choices)]
        else:
            base = {"bool": bool, "int": int, "float": float, "list": list[str]}.get(self.resolved_kind(), str)
        kw = {}
        for name, value in (("ge", self.min), ("le", self.max), ("min_length", self.min_length),
                            ("max_length", self.max_length), ("pattern", self.pattern), ("strict", self.strict)):
            if value is not None:
                kw[name] = value
        extras = [Field(**kw)] if kw else []
        if self.before is not None:
            extras.append(BeforeValidator(self.before))
        extras += [AfterValidator(v) for v in self.validators]
        return Annotated[tuple([base] + extras)] if extras else base


@dataclass
class Group:
    id: str
    label: str
    help: str = ""


def clean_message(err: dict) -> str:
    """Pydantic's message without its "Value error, " prefix."""
    return str(err.get("msg", "invalid value")).removeprefix("Value error, ")


def by_label(labels: dict, *, fallback_key: bool = True, empty: str = "Invalid settings.") -> Callable:
    """"<Label>: <message>; …" — a ValidationError formatter. A key without a label shows the key
    (or only the message, with fallback_key=False)."""
    def fmt(e: ValidationError) -> str:
        parts = []
        for err in e.errors():
            key = str(err["loc"][0]) if err.get("loc") else ("settings" if fallback_key else "")
            label = labels.get(key) or (key if fallback_key else "")
            msg = clean_message(err)
            parts.append(f"{label}: {msg}" if label else msg)
        return "; ".join(parts) or empty
    return fmt


def by_key(e: ValidationError) -> str:
    """"<key>: <message>; …" (the dotted location)."""
    parts = []
    for err in e.errors():
        loc = ".".join(str(p) for p in err.get("loc", ())) or "settings"
        parts.append(f"{loc}: {clean_message(err)}")
    return "; ".join(parts)


def _copy(values: dict) -> dict:
    return {k: (list(v) if isinstance(v, list) else v) for k, v in values.items()}


def _default_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _default_write(conn, key: str, value: str, who: Optional[str], now: str) -> None:
    conn.execute("INSERT INTO app_settings (key, value, updated_at, updated_by) VALUES (?, ?, ?, ?) "
                 "ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at, "
                 "updated_by = excluded.updated_by", (key, value, now, who))


class Registry:
    """The settings of one app. See the module docstring; every keyword is optional except `connect`.

    connect            () -> context manager yielding a sqlite3 connection (committed by `update`)
    groups             [Group] in page order
    model_name         the Pydantic model's name (it appears in Pydantic's own error text)
    model_config       ConfigDict for the model (extra="forbid" is always added)
    format_error       ValidationError -> str (default: by_label(labels))
    unknown_message    [keys] -> str, for keys that aren't settings
    not_object_message the message when the update isn't a JSON object
    flags              {name: message}: extra true/false keys an update may carry (e.g. "clear_<secret>"),
                       handed to `prepare`; `message` is used when one isn't true or false
    prepare            (partial, current, flags) -> partial, before validation (e.g. a blank secret keeps the saved one)
    check              (merged, partial, current) -> None or raise SettingsError (rules across settings)
    context            the Pydantic validation context when validating the merged values
    load_context       the context when validating a stored row (load_check="validate")
    load_check         a stored row is used when: "validate" (it validates), "isinstance" (it has the
                       default's type, bool counts as int), "type" (exactly the default's type)
    encode / decode    encode(value) -> stored text, decode(text, key) -> value (default JSON)
    write              (conn, key, text, who, now) -> None (default: the INSERT … ON CONFLICT above)
    write_all          write every key on each update (not only the changed ones)
    write_lock         a lock held around the read-validate-write of an update
    on_write           (conn, changed, before, after, who) inside the write transaction (only if something changed)
    on_change          [(changed, after)] after a committed change; a failing hook is logged, never raised
    log                (who, changed, before, after) -> None (default: key names only, at INFO)
    now                () -> the updated_at text
    generation         () -> int: the cache is dropped when it changes (the app's database was replaced)
    cache_ttl          seconds a cached read is used (None: until the next write / invalidate())
    fallback_on_error  a failing read gives the defaults (logged) instead of raising
    secret_values      "blank": secrets are "" in the payload's values/defaults; "omit": left out
    logger             a logging.Logger (default "settings")
    """

    def __init__(self, settings: Iterable[Setting], *, connect: Callable, groups: Iterable[Group] = (),
                 model_name: str = "AppSettings", model_config: Optional[dict] = None,
                 format_error: Optional[Callable] = None, unknown_message: Optional[Callable] = None,
                 not_object_message: str = "Send the settings as a JSON object.", flags: Optional[dict] = None,
                 prepare: Optional[Callable] = None, check: Optional[Callable] = None, context: Any = None,
                 load_context: Any = None, load_check: str = "validate", encode: Callable = json.dumps,
                 decode: Callable = lambda text, key: json.loads(text), write: Callable = _default_write, write_all: bool = False,
                 write_lock=None, on_write: Optional[Callable] = None, on_change: Iterable[Callable] = (),
                 log: Optional[Callable] = None, now: Callable = _default_now,
                 generation: Optional[Callable] = None, cache_ttl: Optional[float] = None,
                 fallback_on_error: bool = False, secret_values: str = "blank",
                 logger: Optional[logging.Logger] = None):
        self.settings: dict[str, Setting] = {}
        for s in settings:
            if s.key in self.settings:
                raise ValueError(f"Setting {s.key} is listed twice")
            self.settings[s.key] = s
        self.groups = list(groups)
        group_ids = {g.id for g in self.groups}
        for s in self.settings.values():
            if s.group and self.groups and s.group not in group_ids:
                raise ValueError(f"Setting {s.key}: unknown group {s.group!r}")
        self.keys = tuple(self.settings)
        self.defaults = {k: s.default for k, s in self.settings.items()}
        self.labels = {k: s.label for k, s in self.settings.items() if s.label}
        self.secrets = frozenset(k for k, s in self.settings.items() if s.resolved_kind() == "secret")
        self.connect = connect
        self.model_name = model_name
        config = dict(model_config or {})
        config["extra"] = "forbid"
        self.model_config = ConfigDict(**config)
        self.model = create_model(model_name, __config__=self.model_config,
                                  **{k: (s.annotation(), ...) for k, s in self.settings.items()})
        self._one: dict[str, type[BaseModel]] = {}
        self.format_error = format_error or by_label(self.labels)
        self.unknown_message = unknown_message or (
            lambda keys: f"Unknown setting{'s' if len(keys) > 1 else ''}: {', '.join(sorted(map(str, keys)))}")
        self.not_object_message = not_object_message
        self.flags = dict(flags or {})
        self.prepare, self.check = prepare, check
        self.context, self.load_context = context, load_context
        if load_check not in ("validate", "isinstance", "type"):
            raise ValueError("load_check must be validate, isinstance or type")
        self.load_check = load_check
        self.encode, self.decode, self.write = encode, decode, write
        self.write_all = write_all
        self.write_lock = write_lock if write_lock is not None else contextlib.nullcontext()
        self.on_write = on_write
        self.on_change = list(on_change)
        self.logger = logger or logging.getLogger("settings")
        self.log = log or (lambda who, changed, before, after: self.logger.info(
            "App settings changed by %s: %s", who or "?", ", ".join(changed)))
        self.now = now
        self.generation = generation
        self.cache_ttl = cache_ttl
        self.fallback_on_error = fallback_on_error
        if secret_values not in ("blank", "omit"):
            raise ValueError("secret_values must be blank or omit")
        self.secret_values = secret_values
        self._lock = threading.Lock()
        self._cache: Optional[dict] = None
        self._cache_at = 0.0
        self._cache_gen = None
        self._version = 0

    # ---------- validation ----------
    def validate(self, values: dict, context: Any = None) -> dict:
        """A full set of values → the validated, normalised dict. Raises SettingsError."""
        try:
            return self.model.model_validate(values, context=context).model_dump()
        except ValidationError as e:
            raise SettingsError(self.format_error(e)) from None
        except TypeError as e:          # e.g. a key that isn't a string
            raise SettingsError(str(e)) from None

    def one_model(self, key: str) -> type[BaseModel]:
        """A model with only `key` (its errors look exactly like the full model's)."""
        m = self._one.get(key)
        if m is None:
            m = create_model(self.model_name, __config__=self.model_config,
                             **{key: (self.settings[key].annotation(), ...)})
            self._one[key] = m
        return m

    def validate_one(self, key: str, value: Any, context: Any = None) -> Any:
        """One setting's validated value (the other settings play no part). Raises SettingsError."""
        if key not in self.settings:
            raise SettingsError(self.unknown_message([key]))
        try:
            return getattr(self.one_model(key).model_validate({key: value}, context=context), key)
        except ValidationError as e:
            raise SettingsError(self.format_error(e)) from None

    # ---------- reading ----------
    def _rows(self, conn) -> list:
        return [(r[0], r[1]) for r in conn.execute("SELECT key, value FROM app_settings").fetchall()]

    def _accept(self, key: str, value: Any) -> Any:
        """The value to use for a stored row, or raise ValueError/SettingsError."""
        default = self.defaults[key]
        if self.load_check == "isinstance":
            if not isinstance(value, type(default)):
                raise ValueError(f"not a {type(default).__name__}")
            return value
        if self.load_check == "type":
            if type(value) is not type(default):
                raise ValueError(f"not a {type(default).__name__}")
            return value
        return self.validate_one(key, value, context=self.load_context)

    def load(self, conn) -> dict:
        """The defaults overlaid with the stored rows that are still valid (read now, not cached)."""
        values = _copy(self.defaults)
        for key, raw in self._rows(conn):
            if key not in self.settings:
                continue                    # another row in the same table (an app's own, an old one)
            try:
                values[key] = self._accept(key, self.decode(raw, key))
            except (ValueError, TypeError, SettingsError) as e:
                shown = "(secret)" if key in self.secrets else e
                self.logger.warning("Ignoring the stored setting %s (%s); using the default", key, shown)
        return values

    def invalidate(self) -> None:
        with self._lock:
            self._cache = None
            self._version += 1

    def _fresh(self) -> bool:
        if self._cache is None:
            return False
        if self.generation is not None and self._cache_gen != self.generation():
            return False
        if self.cache_ttl is not None and time.monotonic() - self._cache_at >= self.cache_ttl:
            return False
        return True

    def all(self, conn=None) -> dict:  # noqa: A003 — the design's name
        """Every value (secrets included: server-side use only), from the cache when it is fresh. A read that
        raced a write or a database swap isn't cached, so a reader can never pin the old values."""
        with self._lock:
            if self._fresh():
                return _copy(self._cache)
            version = self._version
            gen = self.generation() if self.generation is not None else None
        try:
            if conn is not None:
                values = self.load(conn)
            else:
                with self.connect() as c:
                    values = self.load(c)
        except Exception:
            if not self.fallback_on_error:
                raise
            self.logger.exception("Could not read the app settings; using the defaults")
            return _copy(self.defaults)
        with self._lock:
            if self._version == version and (self.generation is None or self.generation() == gen):
                self._cache, self._cache_at, self._cache_gen = values, time.monotonic(), gen
        return _copy(values)

    def get(self, key: str, conn=None) -> Any:
        return self.all(conn)[key]

    # ---------- writing ----------
    def update(self, partial: Any, who: Optional[str] = None) -> tuple[dict, list[str]]:
        """Validate {current values + partial} and store it. Returns (the new values, the changed keys in
        the registry's order). Raises SettingsError — nothing is stored then."""
        if not isinstance(partial, dict):
            raise SettingsError(self.not_object_message)
        partial = dict(partial)
        flags = {}
        for name, message in self.flags.items():
            if name in partial:
                flags[name] = partial.pop(name)
                if not isinstance(flags[name], bool):
                    raise SettingsError(message)
        unknown = [k for k in partial if k not in self.settings]
        if unknown:
            raise SettingsError(self.unknown_message(unknown))
        with self.write_lock:
            with self.connect() as conn:
                current = self.load(conn)
                if self.prepare is not None:
                    partial = self.prepare(partial, current, flags)
                merged = self.validate({**current, **partial}, context=self.context)
                if self.check is not None:
                    self.check(merged, partial, current)
                changed = [k for k in self.keys if merged[k] != current[k]]
                now = self.now()
                for k in (self.keys if self.write_all else changed):
                    self.write(conn, k, self.encode(merged[k]), who, now)
                if changed and self.on_write is not None:
                    self.on_write(conn, changed, current, merged, who)
                conn.commit()
            self.invalidate()
        if changed:
            self.log(who, changed, current, merged)
            for fn in self.on_change:
                try:
                    fn(changed, merged)
                except Exception:  # noqa: BLE001 — a hook must not undo a saved change
                    self.logger.exception("Settings hook failed")
        return merged, changed

    # ---------- the page ----------
    def meta(self) -> dict:
        out = {}
        for k, s in self.settings.items():
            m: dict = {"label": s.label or k, "help": s.help, "group": s.group, "kind": s.resolved_kind(),
                       "restartRequired": bool(s.restart)}
            for name, value in (("min", s.min), ("max", s.max)):
                if value is not None:
                    m[name] = value
            if s.max_length is not None and m["kind"] in ("text", "url", "secret"):
                m["maxLength"] = s.max_length
            if s.choices is not None:
                m["choices"] = [{"value": v, "label": lbl} for v, lbl in s.choices]
            for name, value in (("unit", s.unit), ("placeholder", s.placeholder), ("showIf", s.show_if),
                                ("enabledIf", s.enabled_if)):
                if value:
                    m[name] = value
            if s.hidden:
                m["hidden"] = True
            m.update(s.page)
            out[k] = m
        return out

    def groups_payload(self) -> list[dict]:
        return [{"id": g.id, "label": g.label, "help": g.help} for g in self.groups]

    # ---------- backups ----------
    def secret_keys(self) -> tuple:
        """The secret settings' keys, sorted."""
        return tuple(sorted(self.secrets))

    def secret_blanks(self) -> dict:
        """{secret key: the stored text of "not set"} (its default, encoded) — for backup_core's
        `blank_settings` / `saved_settings` / `keep_settings`."""
        return {k: self.encode(self.defaults[k]) for k in self.secret_keys()}

    def scrub_secrets(self, conn) -> None:
        """In a backup copy (a sqlite3 connection to it, e.g. db_core.snapshot's `after=`): every secret
        setting blanked, committed. A backup never carries access keys or passwords."""
        from . import backup_core
        backup_core.blank_settings(conn, self.secret_blanks())

    def saved_secrets(self, conn=None) -> dict:
        """Before a restore: this install's secrets that are set, as stored ({key: text})."""
        from . import backup_core
        if conn is not None:
            return backup_core.saved_settings(conn, self.secret_blanks())
        with self.connect() as c:
            return backup_core.saved_settings(c, self.secret_blanks())

    def keep_secrets(self, saved: dict, conn=None, *, by: str = "kept on restore") -> list:
        """After a restore (migrations done): each of `saved` (from `saved_secrets`) that the restored
        database leaves blank goes back in, so restoring a backup never wipes working keys. Drops the
        cache. Returns the keys put back."""
        from . import backup_core
        if not saved:
            return []
        if conn is not None:
            kept = backup_core.keep_settings(conn, saved, self.secret_blanks(), by=by, now=self.now())
        else:
            with self.connect() as c:
                kept = backup_core.keep_settings(c, saved, self.secret_blanks(), by=by, now=self.now())
        self.invalidate()
        return kept

    def public(self, values: dict) -> dict:
        """`values` for the browser: secrets blanked (or left out)."""
        if self.secret_values == "omit":
            return {k: v for k, v in _copy(values).items() if k not in self.secrets}
        return {k: ("" if k in self.secrets else v) for k, v in _copy(values).items()}

    def payload(self, values: Optional[dict] = None, **extra) -> dict:
        """The GET/PUT /api/admin/settings response: {values, defaults, meta, groups, secretsSet?, …extra}."""
        values = self.all() if values is None else values
        out = {"values": self.public(values), "defaults": self.public(self.defaults), "meta": self.meta(),
               "groups": self.groups_payload()}
        if self.secrets:
            out["secretsSet"] = {k: bool(values.get(k)) for k in sorted(self.secrets)}
        out.update(extra)
        return out
