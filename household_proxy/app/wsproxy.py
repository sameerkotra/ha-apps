"""Websockets through the proxy (spec/SPEC.md §3.6): /s/<site>/… accepts the upgrade (the Supervisor's ingress passes
websockets), opens one to the site (ws/wss, the same path, subprotocols kept, Origin rewritten to the site's own as in
§3.1), and relays messages both ways until either side closes. The same access check as every request, at the upgrade
(the gate in main.py has already checked the ingress source and that the page opening it is on Home Assistant's own
address). The address is checked as for HTTP (netguard.resolve) and the checked address is the one connected to.
"""
import asyncio
import logging

import websockets
from starlette.websockets import WebSocket, WebSocketDisconnect

from . import netguard, proxy, sites

logger = logging.getLogger("wsproxy")

MAX_MESSAGE = 16 * 1024 * 1024
PASS_HEADERS = {"user-agent", "accept-language", "authorization", "cache-control", "pragma"}


def upstream_headers(ws: WebSocket, site: sites.Site, user: dict) -> list:
    out = []
    for k, v in ws.headers.items():
        lk = k.lower()
        if lk in PASS_HEADERS:
            out.append((k, v))
    cookies = [p.strip() for p in (ws.headers.get("cookie") or "").split(";")
               if p.strip() and p.split("=", 1)[0].strip() not in proxy.HA_COOKIES]
    if cookies:
        out.append(("Cookie", "; ".join(cookies)))
    if site.pass_user:
        out.append(("Remote-User", user.get("username") or user["id"]))
        out.append(("X-Forwarded-User", user.get("username") or user["id"]))
    names = {n.lower() for n, _ in site.headers}
    out = [(k, v) for k, v in out if k.lower() not in names]
    return out + list(site.headers)


async def relay(ws: WebSocket, site: sites.Site, user: dict, rest: str) -> None:
    query = ws.scope.get("query_string", b"").decode("latin-1")
    scheme = "wss" if site.scheme == "https" else "ws"
    uri = f"{scheme}://{site.origin.split('://', 1)[1]}{rest}" + (f"?{query}" if query else "")
    subprotocols = [p.strip() for p in (ws.headers.get("sec-websocket-protocol") or "").split(",") if p.strip()]
    try:
        addrs = await netguard.resolve(site.host, site.port, site.allow_public)
        kw = {"host": addrs[0], "port": site.port}
        if scheme == "wss":
            kw.update(ssl=netguard.ssl_context(site.tls_pem), server_hostname=site.host)
        upstream = await websockets.connect(
            uri, origin=site.origin, subprotocols=subprotocols or None,
            additional_headers=upstream_headers(ws, site, user), max_size=MAX_MESSAGE, ping_interval=None,
            open_timeout=site.timeout_s or 30, proxy=None, compression=None, user_agent_header=None, **kw)
    except (OSError, netguard.TargetError, websockets.exceptions.WebSocketException, asyncio.TimeoutError) as e:
        logger.info("%s: websocket %s couldn't be opened: %s", site.slug, rest[:100], e)
        await ws.close(code=1011)
        return
    await ws.accept(subprotocol=upstream.subprotocol)

    async def to_site():
        try:
            while True:
                msg = await ws.receive()
                if msg["type"] == "websocket.disconnect":
                    await upstream.close(code=msg.get("code") or 1000)
                    return
                if msg.get("text") is not None:
                    await upstream.send(msg["text"])
                elif msg.get("bytes") is not None:
                    await upstream.send(msg["bytes"])
        except (WebSocketDisconnect, websockets.exceptions.ConnectionClosed):
            return

    async def to_browser():
        try:
            async for msg in upstream:
                if isinstance(msg, str):
                    await ws.send_text(msg)
                else:
                    await ws.send_bytes(msg)
        except websockets.exceptions.ConnectionClosed:
            pass
        except (WebSocketDisconnect, RuntimeError):
            return
        code = upstream.close_code or 1000
        try:
            await ws.close(code=code if 1000 <= code < 5000 and code not in (1005, 1006, 1015) else 1000)
        except RuntimeError:
            pass

    tasks = [asyncio.create_task(to_site()), asyncio.create_task(to_browser())]
    try:
        await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
    finally:
        for t in tasks:
            t.cancel()
        await upstream.close()
