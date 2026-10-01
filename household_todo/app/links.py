"""The optional link (URL) on tasks and schedule items (SPEC §5.1, §5.4).

The server is the enforcement: only absolute http(s) URLs with a host are
stored, so the UI can render them as links and reminders can make them
tappable without ever handing a `javascript:` / `data:` URL to a browser
or a phone.
"""
import urllib.parse

MAX_URL_LENGTH = 2000
_SCHEMES = ("http", "https")
INVALID_MESSAGE = "A link must be a full web address starting with http:// or https://, e.g. https://example.com."


def clean_url(value) -> str | None:
    """Validate a submitted link and return what to store: None for
    null/blank, else the trimmed URL (scheme lower-cased). Raises ValueError
    with a user-facing message."""
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError("url must be text or null.")
    url = value.strip()
    if not url:
        return None
    if len(url) > MAX_URL_LENGTH:
        raise ValueError(f"A link can be at most {MAX_URL_LENGTH} characters.")
    # urlsplit silently drops tabs/newlines, and a space would end the link
    # in a notification — refuse whitespace and control characters outright
    if any(ord(ch) <= 32 or ord(ch) == 127 for ch in url):
        raise ValueError(INVALID_MESSAGE)
    try:
        parts = urllib.parse.urlsplit(url)
        host = parts.hostname
    except ValueError:            # e.g. a malformed [IPv6] host
        raise ValueError(INVALID_MESSAGE)
    if parts.scheme not in _SCHEMES or not host:
        raise ValueError(INVALID_MESSAGE)
    return parts.scheme + url[len(parts.scheme):]


def is_web_url(value) -> bool:
    """True for a stored value that is safe to link to (a cheap re-check used
    before a URL goes into a notification)."""
    return isinstance(value, str) and value.lower().startswith(("http://", "https://"))
