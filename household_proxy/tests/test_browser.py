"""The proxy in a real browser (Chromium through Playwright; skipped without it), spec/SPEC.md §10: a fake single-page
app written for "/" loads through the viewer under an ingress-like prefix — its router (single-page app mode), API
call, websocket and a stylesheet added at run time all work — and the launcher shows only the person's sites."""
import _env  # noqa: F401  (must be first)

import socket
import threading
import time
import unittest

try:
    from playwright.sync_api import sync_playwright
except ImportError:                                     # pragma: no cover
    sync_playwright = None

from helpers import ADMIN, reset_db

PREFIX = "/api/hassio_ingress/TOKEN123"


def _ingress_front(app_port: int):
    """A tiny stand-in for Home Assistant's ingress: /api/hassio_ingress/TOKEN123/… → the app, with the identity
    headers and X-Ingress-Path added (HTTP and websockets)."""
    import httpx
    import websockets
    from starlette.applications import Starlette
    from starlette.responses import Response
    from starlette.routing import Route, WebSocketRoute

    idh = {"X-Remote-User-Id": ADMIN["X-Remote-User-Id"], "X-Remote-User-Name": ADMIN["X-Remote-User-Name"],
           "X-Remote-User-Display-Name": ADMIN["X-Remote-User-Display-Name"], "X-Ingress-Path": PREFIX}
    client = httpx.AsyncClient(base_url=f"http://127.0.0.1:{app_port}", follow_redirects=False)

    async def http(request):
        path = request.url.path[len(PREFIX):] or "/"
        headers = {k: v for k, v in request.headers.items() if k.lower() not in ("host", "content-length")}
        headers.update(idh)
        r = await client.request(request.method, path, params=request.url.query or None, headers=headers,
                                 content=await request.body())
        out = [(k, v) for k, v in r.headers.multi_items() if k.lower() not in ("content-length", "content-encoding",
                                                                               "transfer-encoding", "connection")]
        resp = Response(r.content, status_code=r.status_code)
        resp.raw_headers = [(k.lower().encode(), v.encode("latin-1")) for k, v in out] + [
            (b"content-length", str(len(r.content)).encode())]
        return resp

    async def ws(websocket):
        path = websocket.url.path[len(PREFIX):]
        headers = dict(idh)
        if websocket.headers.get("cookie"):
            headers["Cookie"] = websocket.headers["cookie"]
        protos = [p.strip() for p in (websocket.headers.get("sec-websocket-protocol") or "").split(",") if p.strip()]
        async with websockets.connect(f"ws://127.0.0.1:{app_port}{path}", additional_headers=headers,
                                      # the Supervisor keeps Origin and Host matching: here the app's own address
                                      origin=f"http://127.0.0.1:{app_port}", subprotocols=protos or None,
                                      proxy=None) as up:
            await websocket.accept(subprotocol=up.subprotocol)
            import asyncio

            async def a():
                async for m in up:
                    await (websocket.send_text(m) if isinstance(m, str) else websocket.send_bytes(m))

            async def b():
                while True:
                    m = await websocket.receive()
                    if m["type"] == "websocket.disconnect":
                        return
                    await up.send(m.get("text") if m.get("text") is not None else m.get("bytes"))
            done, pending = await asyncio.wait([asyncio.create_task(a()), asyncio.create_task(b())],
                                               return_when=asyncio.FIRST_COMPLETED)
            for t in pending:
                t.cancel()

    return Starlette(routes=[Route(PREFIX + "/{p:path}", http, methods=["GET", "POST", "PUT", "DELETE", "PATCH"]),
                             WebSocketRoute(PREFIX + "/{p:path}", ws)])


def _serve(app) -> int:
    import uvicorn
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning"))
    threading.Thread(target=server.run, daemon=True).start()
    for _ in range(200):
        if server.started:
            break
        time.sleep(0.05)
    return port


@unittest.skipIf(sync_playwright is None, "Playwright isn't installed")
class Browser(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from app import main, netguard
        import fakesite
        netguard.ALLOW_LOOPBACK = True
        cls.site_port = fakesite.start()
        import helpers
        helpers.client()                              # the app's database and lifespan
        cls.app_port = _serve(main.app)
        cls.front_port = _serve(_ingress_front(cls.app_port))
        try:
            cls.pw = sync_playwright().start()
            cls.browser = cls.pw.chromium.launch()
        except Exception as e:                        # pragma: no cover — no browser in this environment
            raise unittest.SkipTest(f"Chromium isn't available: {e}")

    @classmethod
    def tearDownClass(cls):
        cls.browser.close()
        cls.pw.stop()

    def setUp(self):
        reset_db()
        import helpers
        helpers.client().get("/api/me", headers=ADMIN)

    def add(self, **kw):
        import helpers
        body = {"name": "Stacks", "slug": "stacks", "target": f"http://127.0.0.1:{self.site_port}",
                "trusted_ok": True, "start_path": "/stacks/1"}
        body.update(kw)
        r = helpers.client().post("/api/admin/sites", json=body, headers=ADMIN)
        self.assertEqual(r.status_code, 200, r.text)

    def open_viewer(self, slug):
        page = self.browser.new_page()
        errors = []
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.goto(f"http://127.0.0.1:{self.front_port}{PREFIX}/#/site/{slug}")
        frame = page.frame_locator("#frame")
        return page, frame, errors

    def test_single_page_app_works_through_the_viewer(self):
        self.add(spa_mode=True)
        page, frame, errors = self.open_viewer("stacks")
        from playwright.sync_api import expect
        expect(frame.locator("#route")).to_have_text("route:/stacks/1", timeout=10000)
        expect(frame.locator("#data")).to_have_text("data:yes", timeout=10000)
        expect(frame.locator("#ws")).to_have_text("echo:ping", timeout=10000)
        expect(frame.locator("#css")).to_have_text("css:loaded", timeout=10000)
        frame.locator("#nav").click()
        expect(frame.locator("#route")).to_have_text("route:/stacks/2")
        self.assertEqual(errors, [])
        page.close()

    def test_without_single_page_app_mode_the_router_sees_the_prefix(self):
        self.add()
        page, frame, errors = self.open_viewer("stacks")
        from playwright.sync_api import expect
        expect(frame.locator("#route")).to_have_text(f"route:{PREFIX}/s/stacks/stacks/1", timeout=10000)
        expect(frame.locator("#data")).to_have_text("data:yes", timeout=10000)      # the API call still works
        page.close()

    def test_launcher(self):
        self.add(access="everyone")
        page = self.browser.new_page()
        page.goto(f"http://127.0.0.1:{self.front_port}{PREFIX}/")
        from playwright.sync_api import expect
        expect(page.locator(".tile")).to_have_count(1)
        expect(page.locator(".tile-name")).to_have_text("Stacks")
        page.close()


if __name__ == "__main__":
    unittest.main()
