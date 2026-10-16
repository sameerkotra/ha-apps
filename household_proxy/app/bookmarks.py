"""Bookmarks: the household's saved links (spec/SPEC.md §5.6), seen by everyone the app lets in. A bookmark is only a
name and an http(s) address the page opens in the browser — in Chrome on a phone, in a new tab on a computer. It is
never fetched or proxied by this app, so it adds nothing the app can reach. Anyone may add one; the person who added
it, or an admin, may change or delete it; admins put them in order."""
from __future__ import annotations

import re
from datetime import datetime, timezone
from urllib.parse import urlsplit

from . import db

MAX_BOOKMARKS = 300
MAX_NAME = 80
MAX_URL = 2000
_ICON_RE = re.compile(r"^\S{1,8}$")


class BookmarkError(ValueError):
    pass


class NotYours(PermissionError):
    pass


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def clean_url(raw) -> str:
    """An absolute http(s) address (a bare "example.com/x" gets https://), else BookmarkError."""
    url = str(raw or "").strip()
    if not url:
        raise BookmarkError("Type the address.")
    if not re.match(r"^[a-zA-Z][a-zA-Z0-9+.-]*://", url):
        if re.match(r"^[a-zA-Z][a-zA-Z0-9+.-]*:(?!\d+(?:[/?#]|$))", url):   # javascript:, data:, mailto: …
            raise BookmarkError("Only web addresses (http:// or https://) can be bookmarked.")
        url = "https://" + url
    if len(url) > MAX_URL or any(c.isspace() or ord(c) < 32 for c in url):
        raise BookmarkError("That address isn't valid.")
    try:
        parts = urlsplit(url)
        host = parts.hostname
        parts.port  # raises on a port that isn't a number
    except ValueError:
        raise BookmarkError("That address isn't valid.") from None
    if parts.scheme.lower() not in ("http", "https") or not host:
        raise BookmarkError("Only web addresses (http:// or https://) can be bookmarked.")
    if parts.username or parts.password:
        raise BookmarkError("Leave the user name and password out of the address — everyone sees bookmarks.")
    return url


def _clean(body: dict) -> tuple[str, str, str | None]:
    url = clean_url(body.get("url"))
    name = " ".join(str(body.get("name") or "").split())[:MAX_NAME] or (urlsplit(url).hostname or url)[:MAX_NAME]
    icon = str(body.get("icon") or "").strip() or None
    if icon is not None and not _ICON_RE.match(icon):
        raise BookmarkError("The icon is one emoji (or leave it empty).")
    return name, url, icon


def list_all(user: dict) -> list[dict]:
    """Every bookmark, in order, each saying whether this person may change it."""
    with db.get_conn() as conn:
        names = {r["id"]: r["name"] for r in conn.execute("SELECT id, name FROM users")}
        rows = conn.execute("SELECT * FROM bookmarks ORDER BY position, id").fetchall()
    return [{"id": r["id"], "name": r["name"], "url": r["url"], "icon": r["icon"],
             "addedBy": names.get(r["created_by"]),
             "canEdit": bool(user["is_admin"] or (r["created_by"] and r["created_by"] == user["id"]))} for r in rows]


def add(user: dict, body: dict) -> list[dict]:
    name, url, icon = _clean(body)
    with db.get_conn() as conn:
        n, last = conn.execute("SELECT COUNT(*), COALESCE(MAX(position), 0) FROM bookmarks").fetchone()
        if n >= MAX_BOOKMARKS:
            raise BookmarkError(f"At most {MAX_BOOKMARKS} bookmarks — delete some first.")
        conn.execute("INSERT INTO bookmarks (name, url, icon, position, created_by, created_at) VALUES (?, ?, ?, ?, ?, ?)",
                     (name, url, icon, last + 1, user["id"], _now()))
    return list_all(user)


def _mine(conn, user: dict, bid: int) -> None:
    r = conn.execute("SELECT created_by FROM bookmarks WHERE id = ?", (bid,)).fetchone()
    if r is None:
        raise LookupError("No such bookmark.")
    if not user["is_admin"] and r["created_by"] != user["id"]:
        raise NotYours("Only the person who added it, or an admin, can change this bookmark.")


def edit(user: dict, bid: int, body: dict) -> list[dict]:
    name, url, icon = _clean(body)
    with db.get_conn() as conn:
        _mine(conn, user, bid)
        conn.execute("UPDATE bookmarks SET name = ?, url = ?, icon = ? WHERE id = ?", (name, url, icon, bid))
    return list_all(user)


def delete(user: dict, bid: int) -> list[dict]:
    with db.get_conn() as conn:
        _mine(conn, user, bid)
        conn.execute("DELETE FROM bookmarks WHERE id = ?", (bid,))
    return list_all(user)


def reorder(user: dict, ids) -> list[dict]:
    """Admins only (the order is everyone's)."""
    if not user["is_admin"]:
        raise NotYours("Only an admin can change the order.")
    if not isinstance(ids, list) or not all(isinstance(i, int) for i in ids):
        raise BookmarkError("ids must be a list of bookmark ids.")
    with db.get_conn() as conn:
        have = [r[0] for r in conn.execute("SELECT id FROM bookmarks")]
        if sorted(have) != sorted(ids):
            raise LookupError("The bookmarks changed meanwhile — reload and try again.")
        conn.executemany("UPDATE bookmarks SET position = ? WHERE id = ?", [(i + 1, bid) for i, bid in enumerate(ids)])
    return list_all(user)
