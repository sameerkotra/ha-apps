"""Which Host header a request may name (DNS rebinding), and whether a browser's Origin matches it.

A web page elsewhere can point its own name at this machine's address ("DNS rebinding"): the browser then sends that
page's requests here as if they were same-origin. Its Host header still names the page's site, so a request is
served only when Host is

* an IP address (v4, or v6 in brackets) — a rebinding page always has a name, never an address;
* a name without a dot ("nas", the container's own host name) — public names always have a dot;
* `localhost`, or a name ending in `.localhost`, `.local`, `.lan`, `.home.arpa` or `.internal` (names that public
  DNS never hands out);
* or a name in the extra list (ALLOWED_HOSTS on a Docker host: "nas.example.com", ".example.com" for every name
  under it, or "*" for any name, which turns the check off).

A missing Host (HTTP/1.0, which no browser sends) passes, as with Ollama's own check.
"""
import ipaddress
from urllib.parse import urlsplit

LOCAL_SUFFIXES = (".localhost", ".local", ".lan", ".home.arpa", ".internal")


def parse_list(text: str | None) -> set[str]:
    """ALLOWED_HOSTS: names separated by commas or spaces, lower-cased, without a port."""
    out = set()
    for part in (text or "").replace(",", " ").split():
        name = host_only(part)
        if name:
            out.add(name)
    return out


def host_only(value: str | None) -> str:
    """"Name:port" / "[v6]:port" / "1.2.3.4" → the name or address alone, lower-cased, no trailing dot."""
    value = (value or "").strip().lower()
    if value.startswith("["):
        end = value.find("]")
        return value[1:end] if end > 0 else value
    if value.count(":") == 1:
        value = value.split(":", 1)[0]
    return value.rstrip(".")


def _is_address(name: str) -> bool:
    try:
        ipaddress.ip_address(name)
        return True
    except ValueError:
        return False


def allowed(host_header: str | None, extra: set[str] | frozenset = frozenset()) -> bool:
    if "*" in extra:
        return True
    name = host_only(host_header)
    if not name:
        return host_header is None or not host_header.strip()
    if _is_address(name) or "." not in name or name == "localhost" or name.endswith(LOCAL_SUFFIXES):
        return True
    if name in extra:
        return True
    return any(e.startswith(".") and (name.endswith(e) or name == e[1:]) for e in extra)


def origin_matches(origin: str | None, host_header: str | None) -> bool:
    """For a state-changing request: no Origin (not a browser) passes; one must name the same host and port as
    Host. "null" (a sandboxed frame, a file) never matches."""
    if origin is None:
        return True
    origin = origin.strip().lower()
    if not origin or origin == "null":
        return False
    try:
        parts = urlsplit(origin)
        port = parts.port
    except ValueError:
        return False
    if parts.scheme not in ("http", "https") or not parts.hostname:
        return False
    port = port or (443 if parts.scheme == "https" else 80)
    host = (host_header or "").strip().lower()
    if host.startswith("["):
        end = host.find("]")
        h_name, rest = host[1:end], host[end + 1:]
        h_port = rest[1:] if rest.startswith(":") else ""
    elif host.count(":") == 1:
        h_name, h_port = host.split(":", 1)
    else:
        h_name, h_port = host, ""
    if not h_port:
        h_port = str(port) if h_name else ""      # Host without a port: the scheme's default
    return parts.hostname.rstrip(".") == h_name.rstrip(".") and str(port) == h_port
