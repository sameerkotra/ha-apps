"""Structured logging configuration for the application."""

import logging
import sys
from typing import Any

from app.config import get_settings


class SecretRedactor(logging.Filter):
    """Filter that redacts secret values from log records."""

    SECRET_KEYS = {"model_api_key", "api_key", "secret", "password", "token"}

    def filter(self, record: logging.LogRecord) -> bool:
        """Redact secrets from log message."""
        # Redact any args that might contain secrets
        if record.args:
            record.args = self._redact_args(record.args)

        # Redact from message if it's a dict
        if isinstance(record.msg, dict):
            record.msg = self._redact_dict(record.msg)

        return True

    def _redact_args(self, args: tuple[Any, ...]) -> tuple[Any, ...]:
        """Redact secrets from log args."""
        redacted = []
        for arg in args:
            if isinstance(arg, dict):
                redacted.append(self._redact_dict(arg))
            else:
                redacted.append(arg)
        return tuple(redacted)

    def _redact_dict(self, data: dict) -> dict:
        """Recursively redact secret keys from dict."""
        result = {}
        for key, value in data.items():
            key_lower = key.lower()
            if any(secret in key_lower for secret in self.SECRET_KEYS):
                result[key] = "***REDACTED***"
            elif isinstance(value, dict):
                result[key] = self._redact_dict(value)
            else:
                result[key] = value
        return result


def setup_logging() -> logging.Logger:
    """Configure structured logging for the application."""
    settings = get_settings()

    # Get root logger
    root_logger = logging.getLogger()
    root_logger.setLevel(getattr(logging, settings.log_level))

    # Clear existing handlers
    root_logger.handlers.clear()

    # Create console handler
    handler = logging.StreamHandler(sys.stdout)
    handler.setLevel(getattr(logging, settings.log_level))

    # Add secret redactor filter
    handler.addFilter(SecretRedactor())

    # Use JSON-like format for structured logging
    formatter = logging.Formatter(
        fmt="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S"
    )
    handler.setFormatter(formatter)

    root_logger.addHandler(handler)

    # Get application logger
    logger = logging.getLogger("app")

    # Log startup info
    logger.info("Application starting...")
    logger.info("Settings: Admin → App settings (in the database)")

    return logger


def get_logger(name: str) -> logging.Logger:
    """Get a named application logger."""
    return logging.getLogger(f"app.{name}")
