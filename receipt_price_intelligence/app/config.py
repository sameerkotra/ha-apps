"""The app's settings, as the rest of the app reads them: ``get_settings().<name>``.

Two sources:

* where things live (``database_path``, ``image_path``) and how the server listens (``host``,
  ``port``), from environment variables the Dockerfile sets;
* everything an administrator can change, from Admin → App settings (``app.app_settings``), which
  is kept in the database and applies straight away.

``get_settings()`` is cheap: the same object is handed out until a setting changes, so callers
read it each time they need a value rather than keeping it.

The only app option (Configuration tab) is ``admin_users``; see ``app.auth``.
"""

from __future__ import annotations

import os
import threading
from typing import Any

from app import app_settings

# Set from config.yaml's version by the release; the tests check the two match.
APP_VERSION = "1.3.2"


def _infra() -> dict[str, Any]:
    return {
        "database_path": os.environ.get("DATABASE_PATH") or "/data/receipt_price_intelligence.db",
        "image_path": os.environ.get("IMAGE_PATH") or "/data/receipts",
        "host": os.environ.get("HOST") or "0.0.0.0",
        "port": int(os.environ.get("PORT") or 8099),
    }


class Settings:
    """A read-only snapshot of every setting, as attributes."""

    __slots__ = ("_values",)

    def __init__(self, values: dict[str, Any]):
        object.__setattr__(self, "_values", dict(values))

    def __getattr__(self, name: str) -> Any:
        try:
            return self._values[name]
        except KeyError:
            raise AttributeError(name) from None

    def __setattr__(self, name, value):
        raise AttributeError("Settings are read-only; change them in Admin → App settings")

    def as_dict(self) -> dict[str, Any]:
        return dict(self._values)

    def redact_secrets(self) -> dict[str, Any]:
        """Every setting, with secrets left out (for logs and the debug page)."""
        return {k: v for k, v in self._values.items() if k not in app_settings.SECRETS}

    @property
    def model_ready(self) -> bool:
        """A model server and a model name are both set, so receipts can be read."""
        return bool(self._values.get("model_url") and self._values.get("model_name"))


_lock = threading.Lock()
_snapshot: tuple[tuple, Settings] | None = None
_fast: tuple[tuple, Settings] | None = None


def _registry_state() -> tuple | None:
    """Which cached copy of the App settings is current, without copying or comparing them (None: not cached)."""
    reg = app_settings.REGISTRY
    cache = getattr(reg, "_cache", None)
    if cache is None:
        return None
    return getattr(reg, "_version", None), id(cache)


def get_settings() -> Settings:
    """The current settings (the same object until something changes). Called in loops: when neither the
    environment nor the cached App settings changed since the last call, nothing is copied or compared."""
    global _snapshot, _fast
    infra = _infra()
    app_settings.configure(infra["database_path"])
    state = _registry_state()
    quick = (tuple(infra.items()), state) if state is not None else None
    if quick is not None:
        with _lock:
            if _fast is not None and _fast[0] == quick:
                return _fast[1]
    before = state
    values = app_settings.values()
    key = (tuple(sorted(infra.items())), tuple(sorted((k, repr(v)) for k, v in values.items())))
    with _lock:
        if _snapshot is not None and _snapshot[0] == key:
            settings = _snapshot[1]
        else:
            settings = Settings({**values, **infra})
            _snapshot = (key, settings)
        # only when the values used are the cached copy seen before reading them (no change raced this call)
        state = _registry_state()
        _fast = ((tuple(infra.items()), state), settings) if state is not None and state == before else None
        return settings
