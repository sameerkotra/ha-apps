"""A cache of web search results and pages, so the same search or page is not asked for again and again.

Lookups repeat themselves: an hours lookup for a store sends the same search every time, and retries and
the debug page's presets send identical ones. Search engines block bursts of automated searches (see
SearXNG's ``unresponsive_engines``), so every repeat saved matters.

* search results are kept ``web_search_cache_days`` (default 3; 0 turns caching off); a search that found
  nothing is kept 12 hours, in case it was a passing hiccup
* pages are kept 24 hours (prices and hours on them change more often)

Kept in ``websearch_cache.db`` next to the database: its own file, not part of backups, safe to delete. Only
successful answers are kept, never errors or blocked searches.
"""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import threading
import time
from typing import Any

from app.config import get_settings
from app.logging_config import get_logger

logger = get_logger("webcache")

PAGE_SECONDS = 24 * 3600
EMPTY_SEARCH_SECONDS = 12 * 3600
MAX_ROWS = 2000

_lock = threading.Lock()
_conn: sqlite3.Connection | None = None
_path: str | None = None


def _db() -> sqlite3.Connection | None:
    global _conn, _path
    path = os.path.join(os.path.dirname(get_settings().database_path) or ".", "websearch_cache.db")
    if _conn is not None and _path == path:
        return _conn
    try:
        conn = sqlite3.connect(path, check_same_thread=False, timeout=5)
        conn.execute("CREATE TABLE IF NOT EXISTS cache (key TEXT PRIMARY KEY, kind TEXT NOT NULL, label TEXT, value TEXT NOT NULL, "
                     "saved REAL NOT NULL, expires REAL NOT NULL)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_cache_expires ON cache(expires)")
        conn.commit()
    except sqlite3.Error:
        logger.exception("The web search cache could not be opened; searching without it")
        return None
    _conn, _path = conn, path
    return conn


def _key(kind: str, *parts: Any) -> str:
    return kind + ":" + hashlib.sha256(json.dumps(parts, sort_keys=True).encode("utf-8")).hexdigest()


def search_key(provider: str, query: str, site: str | None, limit: int) -> str:
    return _key("search", provider, " ".join(query.lower().split()), site or "", limit)


def page_key(url: str) -> str:
    return _key("page", url)


def get(key: str) -> tuple[Any, float] | None:
    """(value, saved time) if kept and not expired."""
    with _lock:
        conn = _db()
        if conn is None:
            return None
        try:
            row = conn.execute("SELECT value, saved, expires FROM cache WHERE key = ?", (key,)).fetchone()
        except sqlite3.Error:
            return None
    if not row or row[2] < time.time():
        return None
    try:
        return json.loads(row[0]), row[1]
    except ValueError:
        return None


def put(key: str, kind: str, value: Any, seconds: float, label: str = "") -> None:
    if seconds <= 0:
        return
    now = time.time()
    with _lock:
        conn = _db()
        if conn is None:
            return
        try:
            try:
                text = json.dumps(value, default=str)
            except (TypeError, ValueError):
                logger.warning("Could not keep %s in the web search cache: not storable", kind)
                return
            conn.execute("INSERT OR REPLACE INTO cache (key, kind, label, value, saved, expires) VALUES (?, ?, ?, ?, ?, ?)",
                         (key, kind, label[:300], text, now, now + seconds))
            # tidy up now and then: expired rows, and the oldest beyond the limit
            if hash(key) % 20 == 0:
                conn.execute("DELETE FROM cache WHERE expires < ?", (now,))
                conn.execute("DELETE FROM cache WHERE key IN (SELECT key FROM cache ORDER BY saved DESC LIMIT -1 OFFSET ?)", (MAX_ROWS,))
            conn.commit()
        except sqlite3.Error:
            logger.warning("Could not save to the web search cache", exc_info=True)


def search_seconds(found_anything: bool) -> float:
    days = get_settings().web_search_cache_days
    if days <= 0:
        return 0
    return days * 86400 if found_anything else min(EMPTY_SEARCH_SECONDS, days * 86400)


def page_seconds() -> float:
    return PAGE_SECONDS if get_settings().web_search_cache_days > 0 else 0


def stats() -> dict[str, Any]:
    with _lock:
        conn = _db()
        if conn is None:
            return {"available": False}
        try:
            now = time.time()
            rows = dict(conn.execute("SELECT kind, COUNT(*) FROM cache WHERE expires >= ? GROUP BY kind", (now,)).fetchall())
        except sqlite3.Error:
            return {"available": False}
    return {"available": True, "searches": rows.get("search", 0), "pages": rows.get("page", 0),
            "days": get_settings().web_search_cache_days}


def expire(key: str) -> None:
    with _lock:
        conn = _db()
        if conn is not None:
            try:
                conn.execute("DELETE FROM cache WHERE key = ?", (key,))
                conn.commit()
            except sqlite3.Error:
                pass


def clear() -> None:
    with _lock:
        conn = _db()
        if conn is not None:
            try:
                conn.execute("DELETE FROM cache")
                conn.commit()
            except sqlite3.Error:
                logger.warning("Could not clear the web search cache", exc_info=True)
