"""Where the proxy may connect (spec/SPEC.md §4.5), checked when the connection is made, not only when a site is saved.

- Only http/https targets, host and port from the site — never from a request.
- Allowed without question: private and link-local addresses (Home Assistant's internal network included), and any
  name that resolves to such addresses (nas.lan, printer.local, a single-label name) — the address is what is judged,
  not the name. Home Assistant's internal network and link-local addresses are allowed, but the site form warns about
  them (target_warning).
- A public address only with the site's "Allow an internet address".
- Never: loopback (this container itself — the app's own port), "this host" (0.0.0.0, ::), multicast, reserved.

The name is resolved once per connection; the address that was checked is the one connected to (no second lookup
that DNS rebinding could answer differently). TLS still verifies the site's own name (SNI and certificate), or the one
trusted certificate an admin chose (§4.5).
"""
import asyncio
import functools
import ipaddress
import socket
import ssl
import time
from concurrent.futures import ThreadPoolExecutor
from functools import partial
from urllib.parse import urlsplit

import certifi
import httpcore

# The Supervisor's network (Home Assistant, the Supervisor, every app): allowed, with a warning in the form.
SUPERVISOR_NETWORK = ipaddress.ip_network("172.30.32.0/23")
HOME_ASSISTANT_NAMES = {"supervisor", "homeassistant", "hassio"}
# X509_V_FLAG_NO_CHECK_TIME (OpenSSL): a trusted self-signed certificate keeps working after its dates, as the
# admin chose that exact certificate (its fingerprint is what is trusted).
_NO_CHECK_TIME = 0x200000


# Tests only: their fake sites listen on 127.0.0.1. Never set in the app itself.
ALLOW_LOOPBACK = False


class TargetError(ValueError):
    """A target the proxy won't reach, with a message for people."""


def parse_target(text: str) -> tuple[str, str, int]:
    """'http://192.168.1.10:9120' → ('http', '192.168.1.10', 9120). TargetError for anything else: other schemes,
    a path, credentials, a query or fragment."""
    raw = (text or "").strip()
    try:
        u = urlsplit(raw)
        port = u.port
    except ValueError:
        raise TargetError("isn't a valid address")
    if u.scheme not in ("http", "https"):
        raise TargetError("must start with http:// or https://")
    if not u.hostname:
        raise TargetError("needs a host name or address")
    if u.username or u.password:
        raise TargetError("can't contain a user name or password (use an extra header instead)")
    if u.path not in ("", "/") or u.query or u.fragment:
        raise TargetError("is the site's address only (scheme, host and port) — put a path in Start path")
    host = u.hostname.lower().rstrip(".")
    if not host or any(c in host for c in "/\\@ "):
        raise TargetError("needs a host name or address")
    return u.scheme, host, port or (443 if u.scheme == "https" else 80)


def parse_monitor_target(text: str) -> tuple[str, str, int | None]:
    """A monitor-only site's address (§13.1): a URL (as parse_target), or a host name or address alone (ping), or
    host:port. → (scheme or "", host, port or None)."""
    raw = (text or "").strip()
    if "://" in raw:
        return parse_target(raw)
    try:
        u = urlsplit("//" + raw)
        port = u.port
    except ValueError:
        raise TargetError("isn't a valid address")
    if not u.hostname or u.path or u.query or u.fragment or u.username or u.password:
        raise TargetError("must be a URL, a host name or address, or host:port")
    return "", u.hostname.lower().rstrip("."), port


def origin(scheme: str, host: str, port: int) -> str:
    """The canonical origin: default ports left out, IPv6 in brackets."""
    h = f"[{host}]" if ":" in host else host
    default = (scheme == "http" and port == 80) or (scheme == "https" and port == 443)
    return f"{scheme}://{h}" if default else f"{scheme}://{h}:{port}"


def address_kind(ip: ipaddress._BaseAddress) -> str:
    """'local' (private / link-local / shared), 'public', or 'never' (loopback, unspecified, multicast, reserved)."""
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped:
        ip = ip.ipv4_mapped
    if ip.is_loopback and ALLOW_LOOPBACK:
        return "local"
    if ip.is_loopback or ip.is_unspecified or ip.is_multicast or ip.is_reserved:
        return "never"
    if ip.is_private or ip.is_link_local or ip in ipaddress.ip_network("100.64.0.0/10"):
        return "local"
    return "public" if ip.is_global else "never"


def literal_ip(host: str):
    try:
        return ipaddress.ip_address(host.strip("[]"))
    except ValueError:
        return None


def target_warning(host: str) -> str | None:
    """A word of warning about an allowed address that is rarely what an admin means, or None."""
    ip = literal_ip(host or "")
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped:
        ip = ip.ipv4_mapped
    if (host or "").lower() in HOME_ASSISTANT_NAMES or (ip is not None and ip.version == 4 and
                                                         ip in SUPERVISOR_NETWORK):
        return ("This address is on Home Assistant's own internal network (Home Assistant, the Supervisor and your "
                "other apps). Add it only if you mean to: whoever may open the site reaches that service through "
                "this app.")
    if ip is not None and ip.is_link_local:
        return ("This is a link-local address (169.254.x.x or fe80::…), which a device usually has only when it "
                "didn't get a proper one. Check it is the device you mean.")
    return None


def check_address(ip, allow_public: bool) -> None:
    kind = address_kind(ip)
    if kind == "never":
        raise TargetError(f"{ip} can't be reached through the proxy (loopback, multicast or reserved)")
    if kind == "public" and not allow_public:
        raise TargetError(f"{ip} is an internet address — turn on “Allow an internet address” to use it")


def check_saved(host: str, allow_public: bool) -> None:
    """The check when a site is saved: a literal address is judged now; a name at connect time."""
    ip = literal_ip(host)
    if ip is not None:
        check_address(ip, allow_public)
    elif host == "localhost" or host.endswith(".localhost"):
        raise TargetError("localhost is this app itself — use the device's address on your network")


# Name lookups run on threads of their own (a slow DNS server can't fill the pool every other blocking task uses), and
# a name that couldn't be found is remembered for a few seconds (a page asking for it fifty times waits once).
_dns_pool = ThreadPoolExecutor(max_workers=4, thread_name_prefix="dns")
NOT_FOUND_FOR_S = 10
_not_found: dict = {}


async def _lookup(host: str, port: int):
    """getaddrinfo on the lookup threads."""
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(_dns_pool, partial(socket.getaddrinfo, host, port, type=socket.SOCK_STREAM))


def _forget_old(now: float) -> None:
    if len(_not_found) > 200:
        for k in [k for k, t in _not_found.items() if t <= now]:
            _not_found.pop(k, None)


async def resolve(host: str, port: int, allow_public: bool) -> list[str]:
    """The addresses `host` may be connected to (checked), in resolver order. TargetError when none."""
    ip = literal_ip(host)
    if ip is not None:
        check_address(ip, allow_public)
        return [str(ip)]
    check_saved(host, allow_public)
    message = f"the name {host} couldn't be found (DNS) — try the device's IP address"
    now = time.monotonic()
    if _not_found.get(host, 0) > now:
        raise TargetError(message)
    try:
        infos = await _lookup(host, port)
    except socket.gaierror:
        _forget_old(now)
        _not_found[host] = now + NOT_FOUND_FOR_S
        raise TargetError(message)
    out, problem = [], None
    for info in infos:
        addr = info[4][0]
        try:
            check_address(ipaddress.ip_address(addr.split("%")[0]), allow_public)
        except TargetError as e:
            problem = e
            continue
        if addr not in out:
            out.append(addr)
    if not out:
        raise problem or TargetError(f"the name {host} has no usable address")
    return out


class GuardedBackend(httpcore.AsyncNetworkBackend):
    """httpcore's network backend with the address check: resolve once, check, connect to the checked address.
    TLS is started by httpcore afterwards with the original host name (SNI, certificate check)."""

    def __init__(self, allow_public: bool):
        from httpcore._backends.auto import AutoBackend
        self._inner = AutoBackend()
        self.allow_public = allow_public

    async def connect_tcp(self, host, port, timeout=None, local_address=None, socket_options=None):
        name = host.decode() if isinstance(host, bytes) else host
        try:
            addrs = await resolve(name, port, self.allow_public)
        except TargetError as e:
            raise httpcore.ConnectError(str(e))
        last = None
        for addr in addrs:
            try:
                return await self._inner.connect_tcp(addr, port, timeout=timeout, local_address=local_address,
                                                     socket_options=socket_options)
            except (httpcore.ConnectError, httpcore.ConnectTimeout) as e:
                last = e
        raise last

    async def connect_unix_socket(self, *args, **kwargs):
        raise httpcore.ConnectError("not allowed")

    async def sleep(self, seconds):
        await self._inner.sleep(seconds)


@functools.lru_cache(maxsize=64)
def ssl_context(pem: str | None) -> ssl.SSLContext:
    """Verify against the usual certificate authorities — or, with `pem`, accept that one certificate only (a
    self-signed one an admin trusted after the Test: its fingerprint, any host name, any dates). Made once per
    certificate and shared (loading the authorities takes a moment; the context isn't changed after)."""
    if not pem:
        return ssl.create_default_context(cafile=certifi.where())
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_REQUIRED
    ctx.load_verify_locations(cadata=pem)
    ctx.verify_flags |= ssl.VERIFY_X509_PARTIAL_CHAIN | _NO_CHECK_TIME
    return ctx


def guarded_transport(pem: str | None, allow_public: bool, **kw):
    """An httpx transport whose every connection goes through the address check (GuardedBackend). httpx has no
    public way to give its pool a network backend, so it is set on the pool, and checked: if a new httpx puts it
    elsewhere, nothing is connected unchecked — the app refuses to make the transport."""
    import httpx
    t = httpx.AsyncHTTPTransport(verify=ssl_context(pem), retries=0, **kw)
    backend = GuardedBackend(allow_public)
    pool = getattr(t, "_pool", None)
    if pool is None or not hasattr(pool, "_network_backend"):
        raise RuntimeError("httpx changed: the address check can't be attached to its connections")
    pool._network_backend = backend
    if getattr(t._pool, "_network_backend", None) is not backend:
        raise RuntimeError("httpx changed: the address check isn't attached to its connections")
    return t


def insecure_context() -> ssl.SSLContext:
    """For the Test only: read a certificate to show its fingerprint (never used to send anything)."""
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    return ctx
