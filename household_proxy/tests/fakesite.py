"""A fake site on the "home network" for the tests: a small Starlette app served by uvicorn on 127.0.0.1 in a thread
(one per test run). The app's address check allows loopback only while netguard.ALLOW_LOOPBACK is on (tests only).

Routes: a root-path HTML page with absolute assets and cookies, CSS, a redirect, an echo of the request (headers,
body), a 3 MB download, a gzip page, a slow stream, a big HTML page, and (phase 2) a websocket echo.
"""
import asyncio
import gzip
import socket
import threading
import time

import uvicorn
from starlette.applications import Starlette
from starlette.responses import HTMLResponse, JSONResponse, PlainTextResponse, RedirectResponse, Response, \
    StreamingResponse
from starlette.routing import Route, WebSocketRoute

PAGE = """<!DOCTYPE html>
<html><head><meta charset="utf-8"><base href="/"><title>Fake</title>
<link rel="stylesheet" href="/static/app.css" integrity="sha384-x">
<script src="/static/app.js"></script>
<meta http-equiv="refresh" content="300; url=/again">
</head><body>
<a id="abs" href="http://{host}/stacks">a</a> <a id="rel" href="stacks/1">b</a> <a id="other" href="https://example.com/x">c</a>
<img src="/img/a.png" srcset="/img/a.png 1x, /img/b.png 2x">
<form action="/login" method="post"><button formaction="/login2">go</button></form>
<div style="background:url('/img/bg.png')"></div>
<style>.x{{background:url(/img/c.png)}}</style>
<svg><use href="/icons.svg#a"></use></svg>
<p>Caf&eacute; &amp; &#169; — ünïcode</p>
</body></html>"""

ECHO_SKIP = {"user-agent"}


def make_app(host: str) -> Starlette:
    async def home(request):
        return HTMLResponse(PAGE.format(host=host), headers={
            "Set-Cookie": "sid=abc; Path=/; Domain=127.0.0.1; HttpOnly; Secure; SameSite=None",
            "X-Frame-Options": "DENY",
            "Content-Security-Policy": "default-src 'self'; frame-ancestors 'none'; upgrade-insecure-requests",
            "Strict-Transport-Security": "max-age=31536000",
            "Clear-Site-Data": '"*"',
            "Link": "</static/app.css>; rel=preload; as=style",
        })

    async def css(request):
        return Response('@import "/static/more.css"; body{background:url("/img/z.png")}', media_type="text/css")

    async def js(request):
        return Response('fetch("/api/data")', media_type="application/javascript")

    async def redirect(request):
        return RedirectResponse("/login", status_code=302)

    async def redirect_abs(request):
        return RedirectResponse(f"http://{host}/home", status_code=302)

    async def redirect_away(request):
        return RedirectResponse("https://example.com/away", status_code=302)

    async def echo(request):
        body = await request.body()
        return JSONResponse({"method": request.method, "path": request.url.path, "query": request.url.query,
                             "headers": {k: v for k, v in request.headers.items() if k not in ECHO_SKIP},
                             "body": body.decode("latin-1"), "size": len(body)})

    async def big(request):
        chunk = b"x" * 65536

        async def gen():
            for _ in range(48):                 # 3 MB
                yield chunk
        return StreamingResponse(gen(), media_type="application/octet-stream",
                                 headers={"Content-Length": str(48 * 65536)})

    async def gz(request):
        data = gzip.compress(b"<html><head></head><body><a href='/z'>z</a></body></html>")
        return Response(data, media_type="text/html", headers={"Content-Encoding": "gzip"})

    async def slow(request):
        async def gen():
            for i in range(3):
                yield f"data: {i}\n\n".encode()
                await asyncio.sleep(0.4)
        return StreamingResponse(gen(), media_type="text/event-stream")

    async def huge_html(request):
        return HTMLResponse("<html><body>" + "<a href='/x'>x</a>" * 400000 + "</body></html>")

    async def status(request):
        return PlainTextResponse("teapot", status_code=int(request.path_params["code"]))

    async def ws_echo(websocket):
        await websocket.accept(subprotocol=(websocket.scope.get("subprotocols") or [None])[0])
        await websocket.send_json({"origin": websocket.headers.get("origin"), "host": websocket.headers.get("host"),
                                   "cookie": websocket.headers.get("cookie"), "path": websocket.url.path,
                                   "remote_user": websocket.headers.get("x-remote-user-id"),
                                   "auth": websocket.headers.get("authorization")})
        try:
            while True:
                msg = await websocket.receive()
                if msg["type"] == "websocket.disconnect":
                    return
                if msg.get("text") is not None:
                    await websocket.send_text("echo:" + msg["text"])
                elif msg.get("bytes") is not None:
                    await websocket.send_bytes(b"echo:" + msg["bytes"])
        except Exception:
            return

    return Starlette(routes=[
        Route("/", home), Route("/static/app.css", css), Route("/static/app.js", js), Route("/r", redirect),
        Route("/r-abs", redirect_abs), Route("/r-away", redirect_away),
        Route("/echo", echo, methods=["GET", "POST", "PUT", "PATCH", "DELETE"]),
        Route("/echo/{rest:path}", echo, methods=["GET", "POST"]),
        Route("/big", big), Route("/gz", gz), Route("/slow", slow), Route("/huge", huge_html),
        Route("/status/{code}", status), WebSocketRoute("/ws", ws_echo),
    ])


_server = {}


def start() -> int:
    """Start the fake site once; its port."""
    if "port" in _server:
        return _server["port"]
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    config = uvicorn.Config(make_app(f"127.0.0.1:{port}"), host="127.0.0.1", port=port, log_level="warning",
                            ws="websockets-sansio" if _has_sansio() else "auto")
    server = uvicorn.Server(config)
    threading.Thread(target=server.run, daemon=True).start()
    for _ in range(100):
        if server.started:
            break
        time.sleep(0.05)
    _server.update(port=port, server=server)
    return port


def _has_sansio() -> bool:
    try:
        import uvicorn.protocols.websockets.websockets_sansio_impl  # noqa: F401
        return True
    except ImportError:
        return False
