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
APP_VERSION = "1.2.2"


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


def get_settings() -> Settings:
    """The current settings (the same object until something changes)."""
    global _snapshot
    infra = _infra()
    app_settings.configure(infra["database_path"])
    values = app_settings.values()
    key = (tuple(sorted(infra.items())), tuple(sorted((k, repr(v)) for k, v in values.items())))
    with _lock:
        if _snapshot is not None and _snapshot[0] == key:
            return _snapshot[1]
        settings = Settings({**values, **infra})
        _snapshot = (key, settings)
        return settings
