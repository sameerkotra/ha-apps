"""Websockets through the proxy (spec/SPEC.md §3.6): /s/<site>/… accepts the upgrade (the Supervisor's ingress passes
websockets), opens one to the site (ws/wss, the same path, subprotocols kept, Origin rewritten to the site's own as in
§3.1), and relays messages both ways until either side closes. The same access check as every request, at the upgrade
(the gate in main.py has already checked the ingress source and that the page opening it is on Home Assistant's own
address) — and again while it stays open: every RECHECK_S seconds, and at once when a site or a person is changed, so
a person turned off or taken off a site loses the websockets they have open. The address is checked as for HTTP
(netguard.resolve) and the checked addresses are the ones connected to, in order.

Each person may have MAX_PER_PERSON websockets open on a site at once. With the site's access log on, the opening and
the closing (with how long it was open and how much it carried) are logged, never the messages.
"""
import asyncio
import logging
import threading
import time

import websockets
from starlette.websockets import WebSocket, WebSocketDisconnect

from . import auth, netguard, proxy, rewrite, sites

logger = logging.getLogger("wsproxy")

MAX_MESSAGE = 16 * 1024 * 1024
MAX_PER_PERSON = 16
RECHECK_S = 30
PING_S = 30
PASS_HEADERS = {"user-agent", "accept-language", "authorization", "cache-control", "pragma"}

OPEN = proxy.InFlight(MAX_PER_PERSON)          # (person, site) -> websockets open (on the event loop only)
_relays: set = set()                           # (event loop, wake event) of every open relay
_relays_lock = threading.Lock()


def recheck_all() -> None:
    """Every open websocket checks at once whether its person may still open its site (safe from any thread)."""
    with _relays_lock:
        items = list(_relays)
    for loop, wake in items:
        try:
            loop.call_soon_threadsafe(wake.set)
        except RuntimeError:                   # that loop has closed
            pass


sites.on_invalidate(recheck_all)
auth.on_forget(recheck_all)


def close_code(code) -> int:
    """A close code that may be sent on: 1005 (none given), 1006 (cut off), 1015 (TLS) and unknown ones are only ever
    observed, never sent, so they become 1000."""
    if isinstance(code, int) and (1000 <= code <= 1003 or 1007 <= code <= 1014 or 3000 <= code < 5000):
        return code
    return 1000


def target_uri(site: sites.Site, rest: str, query: str) -> str:
    """The site's websocket address for a path and query from the browser (bytes outside ASCII percent-encoded)."""
    scheme = "wss" if site.scheme == "https" else "ws"
    return (f"{scheme}://{site.origin.split('://', 1)[1]}{proxy.ascii_url(rest)}" +
            (f"?{proxy.ascii_url(query)}" if query else ""))


def upstream_headers(ws: WebSocket, site: sites.Site, user: dict) -> list:
    out = []
    for k, v in ws.headers.items():
        if k.lower() in PASS_HEADERS:
            out.append((k, v))
    cookies = rewrite.site_cookies(ws.headers.get("cookie"), site.slug)      # this site's own cookies only
    if cookies:
        out.append(("Cookie", cookies))
    if site.pass_user:
        out.append(("Remote-User", user.get("username") or user["id"]))
        out.append(("X-Forwarded-User", user.get("username") or user["id"]))
    extra = {proxy.header_key(n) for n, _ in site.headers}
    out = [(k, v) for k, v in out if proxy.header_key(k) not in extra]
    return out + list(site.headers)


def still_allowed(site: sites.Site, user: dict) -> bool:
    """The person may still open the site, and it is still the same site at the same address (blocking: a thread)."""
    fresh = auth.user_by_id(user["id"])
    now = sites.proxied(site.slug)
    return (fresh is not None and now is not None and now.id == site.id and now.origin == site.origin
            and sites.can_open(fresh, now))


async def _connect(site: sites.Site, ws: WebSocket, user: dict, uri: str, subprotocols: list):
    """The websocket to the site, trying each checked address in turn."""
    addrs = await netguard.resolve(site.host, site.port, site.allow_public)
    last = None
    for addr in addrs:
        kw = {"host": addr, "port": site.port}
        if uri.startswith("wss:"):
            kw.update(ssl=netguard.ssl_context(site.tls_pem), server_hostname=site.host)
        try:
            return await websockets.connect(
                uri, origin=site.origin, subprotocols=subprotocols or None,
                additional_headers=upstream_headers(ws, site, user), max_size=MAX_MESSAGE, ping_interval=PING_S,
                ping_timeout=PING_S, open_timeout=site.timeout_s or 30, proxy=None, compression=None,
                user_agent_header=None, **kw)
        except (OSError, asyncio.TimeoutError) as e:
            last = e
    raise last or OSError("no address to connect to")


async def relay(ws: WebSocket, site: sites.Site, user: dict, rest: str) -> None:
    key = (user["id"], site.id)
    if not OPEN.enter(key):
        logger.warning("%s: %s already has %d websockets open; refused one more", site.slug, user["id"],
                       MAX_PER_PERSON)
        await ws.close(code=1013)                  # try again later
        return
    try:
        await _relay(ws, site, user, rest, proxy.log_writer(site, user, "WS", rest))
    finally:
        OPEN.leave(key)


async def _relay(ws: WebSocket, site: sites.Site, user: dict, rest: str, log) -> None:
    query = proxy.site_query(ws.scope)       # brackets as the browser sent them (Komodo's terminal needs it)
    subprotocols = [p.strip() for p in (ws.headers.get("sec-websocket-protocol") or "").split(",") if p.strip()]
    try:
        uri = target_uri(site, rest, query)
        upstream = await _connect(site, ws, user, uri, subprotocols)
    except (OSError, netguard.TargetError, websockets.exceptions.WebSocketException, asyncio.TimeoutError,
            ValueError) as e:
        logger.info("%s: websocket %s couldn't be opened: %s", site.slug, rest[:100], e)
        if log:
            log(1011, 0)
        await ws.close(code=1011)
        return
    await ws.accept(subprotocol=upstream.subprotocol)
    started = time.monotonic()
    if log:
        log(101, 0)
    carried = {"bytes": 0, "code": None}
    wake = asyncio.Event()
    entry = (asyncio.get_running_loop(), wake)
    with _relays_lock:
        _relays.add(entry)

    async def to_site():
        try:
            while True:
                msg = await ws.receive()
                if msg["type"] == "websocket.disconnect":
                    carried["code"] = msg.get("code")
                    await upstream.close(code=close_code(msg.get("code")))
                    return
                if msg.get("text") is not None:
                    carried["bytes"] += len(msg["text"])
                    await upstream.send(msg["text"])
                elif msg.get("bytes") is not None:
                    carried["bytes"] += len(msg["bytes"])
                    await upstream.send(msg["bytes"])
        except (WebSocketDisconnect, websockets.exceptions.ConnectionClosed):
            return

    async def to_browser():
        try:
            async for msg in upstream:
                carried["bytes"] += len(msg)
                if isinstance(msg, str):
                    await ws.send_text(msg)
                else:
                    await ws.send_bytes(msg)
        except websockets.exceptions.ConnectionClosed:
            pass
        except (WebSocketDisconnect, RuntimeError):
            return
        carried["code"] = upstream.close_code
        try:
            await ws.close(code=close_code(upstream.close_code))
        except RuntimeError:
            pass

    async def watch():
        """Ends when the person may no longer open the site."""
        while True:
            try:
                await asyncio.wait_for(wake.wait(), RECHECK_S)
            except asyncio.TimeoutError:
                pass
            wake.clear()
            try:
                if not await asyncio.to_thread(still_allowed, site, user):
                    return
            except Exception:                  # noqa: BLE001 — can't tell now: ask again next time
                logger.exception("checking an open websocket failed")

    tasks = [asyncio.create_task(to_site()), asyncio.create_task(to_browser())]
    watcher = asyncio.create_task(watch())
    try:
        done, _ = await asyncio.wait(tasks + [watcher], return_when=asyncio.FIRST_COMPLETED)
        if watcher in done:
            logger.info("%s: closed a websocket of %s: they may no longer open this site", site.slug, user["id"])
            carried["code"] = 1008
            try:
                await ws.close(code=1008)
            except RuntimeError:
                pass
    finally:
        for t in tasks + [watcher]:
            t.cancel()
        with _relays_lock:
            _relays.discard(entry)
        if log:                                    # before the last await: a cancelled relay still logs it
            log(carried["code"] or 1000, carried["bytes"], time.monotonic() - started)
        await upstream.close()
