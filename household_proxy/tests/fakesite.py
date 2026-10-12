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

# A single-page app written for "/": absolute assets, a client-side router reading location.pathname, an API call, a
# websocket to its own host and a stylesheet added at run time.
SPA = """<!DOCTYPE html><html><head><meta charset="utf-8"><title>SPA</title>
<script src="/static/spa.js" defer></script></head>
<body><div id="route">…</div><div id="data">…</div><div id="ws">…</div><div id="css">…</div>
<a id="nav" href="/stacks/2">next</a></body></html>"""

SPA_JS = """
function render() { document.getElementById("route").textContent = "route:" + location.pathname; }
render();
document.getElementById("nav").addEventListener("click", function (e) {
  e.preventDefault(); history.pushState(null, "", "/stacks/2"); render();
});
fetch("/api/data").then(function (r) { return r.json(); }).then(function (j) {
  document.getElementById("data").textContent = "data:" + j.ok;
});
var ws = new WebSocket((location.protocol === "https:" ? "wss://" : "ws://") + "{host}/ws");
ws.onmessage = function (m) { if (m.data.indexOf("echo:") === 0) document.getElementById("ws").textContent = m.data; else ws.send("ping"); };
var l = document.createElement("link"); l.rel = "stylesheet"; l.href = "/static/app.css";
l.onload = function () { document.getElementById("css").textContent = "css:loaded"; };
document.head.appendChild(l);
"""

BASENAME_PAGE = """<html><head><link rel="icon" href="/img/icon.png"></head><body><script src="/static/base.js"></script>
<script src="/static/base.js" integrity="sha384-x"></script></body></html>"""
BASENAME_JS = 'createBrowserRouter(routes, {basename:"/"}); fetch("/api/x")'
PNG = (b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x06\x00\x00\x00\x1f\x15\xc4\x89"
       b"\x00\x00\x00\rIDATx\x9cc\xf8\xcf\xc0\xf0\x1f\x00\x05\x00\x01\xff\x89\x99=\x1d\x00\x00\x00\x00IEND\xaeB`\x82")

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

    async def more_css(request):
        return Response("p{margin:0}", media_type="text/css")

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

    async def spa(request):
        return HTMLResponse(SPA)

    async def spa_js(request):
        return Response(SPA_JS.replace("{host}", host), media_type="application/javascript")

    async def data(request):
        return JSONResponse({"ok": "yes"})

    async def basename_page(request):
        return HTMLResponse(BASENAME_PAGE)

    async def basename_js(request):
        return Response(BASENAME_JS, media_type="application/javascript",
                        headers={"X-Base": "/old-base", "Content-Security-Policy": "script-src 'self'"})

    async def png(request):
        return Response(PNG, media_type="image/png")

    async def svg_page(request):
        return HTMLResponse('<html><head><link rel="icon" href="/img/i.svg"></head></html>')

    async def svg(request):
        return Response('<svg xmlns="http://www.w3.org/2000/svg"><script>alert(1)</script></svg>',
                        media_type="image/svg+xml")

    async def bomb(request):
        import zlib
        c = zlib.compressobj(9, zlib.DEFLATED, 31)
        data = c.compress(b"<html><body>" + b"A" * (20 * 1024 * 1024)) + c.compress(b"</body></html>") + c.flush()
        return Response(data, media_type="text/html", headers={"Content-Encoding": "gzip"})

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
        Route("/", home), Route("/static/app.css", css), Route("/static/more.css", more_css), Route("/static/app.js", js), Route("/r", redirect),
        Route("/r-abs", redirect_abs), Route("/r-away", redirect_away),
        Route("/echo", echo, methods=["GET", "POST", "PUT", "PATCH", "DELETE"]),
        Route("/echo/{rest:path}", echo, methods=["GET", "POST"]),
        Route("/big", big), Route("/gz", gz), Route("/slow", slow), Route("/huge", huge_html),
        Route("/status/{code}", status), WebSocketRoute("/ws", ws_echo),
        Route("/stacks", spa), Route("/stacks/{rest:path}", spa), Route("/static/spa.js", spa_js),
        Route("/api/data", data), Route("/basename", basename_page), Route("/static/base.js", basename_js),
        Route("/img/icon.png", png), Route("/favicon.ico", png), Route("/svgicon", svg_page), Route("/img/i.svg", svg),
        Route("/bomb", bomb),
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


def start_tls(cert_file: str, key_file: str) -> int:
    """A second copy of the fake site over https with the given certificate (a new one per call); its port."""
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    config = uvicorn.Config(make_app(f"127.0.0.1:{port}"), host="127.0.0.1", port=port, log_level="warning",
                            ssl_certfile=cert_file, ssl_keyfile=key_file)
    server = uvicorn.Server(config)
    threading.Thread(target=server.run, daemon=True).start()
    for _ in range(100):
        if server.started:
            break
        time.sleep(0.05)
    return port


def self_signed(folder: str, name: str = "nas.lan") -> tuple[str, str]:
    """A fresh self-signed certificate and key in `folder`: (cert file, key file)."""
    import datetime
    import os
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.x509.oid import NameOID
    key = ec.generate_private_key(ec.SECP256R1())
    subject = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, name)])
    now = datetime.datetime.now(datetime.timezone.utc)
    cert = (x509.CertificateBuilder().subject_name(subject).issuer_name(subject).public_key(key.public_key())
            .serial_number(x509.random_serial_number()).not_valid_before(now - datetime.timedelta(days=1))
            .not_valid_after(now + datetime.timedelta(days=30)).sign(key, hashes.SHA256()))
    cf, kf = os.path.join(folder, name + ".crt"), os.path.join(folder, name + ".key")
    with open(cf, "wb") as f:
        f.write(cert.public_bytes(serialization.Encoding.PEM))
    with open(kf, "wb") as f:
        f.write(key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.TraditionalOpenSSL,
                                  serialization.NoEncryption()))
    return cf, kf


def _has_sansio() -> bool:
    try:
        import uvicorn.protocols.websockets.websockets_sansio_impl  # noqa: F401
        return True
    except ImportError:
        return False
