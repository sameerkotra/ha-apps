"""Websockets through the proxy (spec/SPEC.md §3.6): relayed both ways, subprotocols kept, Origin and cookies cleaned,
the same access check at the upgrade, other sites' pages refused."""
import _env  # noqa: F401  (must be first)

import unittest

from starlette.websockets import WebSocketDisconnect

from app import db, proxy, sites, wsproxy
from helpers import ADMIN, BOB, ProxyCase


class Websockets(ProxyCase):
    def test_relayed_both_ways(self):
        self.add_site(headers=[{"name": "Authorization", "value": "Bearer k"}])
        with self.c.websocket_connect("/s/fake/ws?x=1", headers={**ADMIN, "Cookie": "ingress_session=S; a=0; hp_fake_a=1; hp_x_b=2",
                                                                  "Remote-User": "someone-else"},
                                      subprotocols=["chat"]) as ws:
            first = ws.receive_json()
            self.assertEqual(first["origin"], f"http://127.0.0.1:{self.port}")
            self.assertEqual(first["cookie"], "a=1")
            self.assertIsNone(first["remote_user"])
            self.assertEqual(first["auth"], "Bearer k")
            self.assertEqual(first["path"], "/ws")
            self.assertEqual(ws.accepted_subprotocol, "chat")
            ws.send_text("hi")
            self.assertEqual(ws.receive_text(), "echo:hi")
            ws.send_bytes(b"\x00\x01")
            self.assertEqual(ws.receive_bytes(), b"echo:\x00\x01")

    def test_query_reaches_the_site_as_the_browser_sent_it(self):
        # Home Assistant's ingress turns "target[type]" into "target%5Btype%5D" on the way; Komodo's terminal
        # (serde_qs, strict) refuses that form, so the terminal closed at once. The site gets the brackets back.
        self.add_site()
        sent = ("target%5Btype%5D=Container&target%5Bparams%5D%5Bserver%5D=abc&target%5bparams%5d%5bcontainer%5d=web"
                "&init%5Bcommand%5D=sh+-c+%22x%22&p=1%2B1&q=%7Ba%7C%5E%60%7D")
        with self.c.websocket_connect("/s/fake/ws?" + sent, headers=ADMIN) as ws:
            self.assertEqual(ws.receive_json()["query"],
                             "target[type]=Container&target[params][server]=abc&target[params][container]=web"
                             "&init[command]=sh+-c+%22x%22&p=1%2B1&q={a|^`}")

    def test_access_checked_at_the_upgrade(self):
        self.add_site()
        with self.assertRaises(WebSocketDisconnect) as e:
            with self.c.websocket_connect("/s/fake/ws", headers=BOB):
                pass
        self.assertEqual(e.exception.code, 1008)
        with self.assertRaises(WebSocketDisconnect):
            with self.c.websocket_connect("/s/nope/ws", headers=ADMIN):
                pass

    def test_other_sites_pages_refused(self):
        self.add_site()
        with self.assertRaises(WebSocketDisconnect) as e:
            with self.c.websocket_connect("/s/fake/ws", headers={**ADMIN, "Origin": "https://evil.example.com"}):
                pass
        self.assertEqual(e.exception.code, 1008)

    def test_unreachable_site_closes(self):
        self.add_site(target="http://127.0.0.1:9")
        with self.assertRaises(WebSocketDisconnect) as e:
            with self.c.websocket_connect("/s/fake/ws", headers=ADMIN) as ws:
                ws.receive_text()
        self.assertEqual(e.exception.code, 1011)


class WhileOpen(ProxyCase):
    """An open websocket is checked again when sites or people change (and every 30 seconds), and logged."""

    def everyone_site(self, **kw):
        return self.add_site(access="everyone", **kw)

    def body(self, **kw):
        b = {"name": "Fake", "target": f"http://127.0.0.1:{self.port}", "trusted_ok": True}
        b.update(kw)
        return b

    def assert_closed_with(self, ws, code):
        with self.assertRaises(WebSocketDisconnect) as e:
            for _ in range(5):
                ws.receive_text()
        self.assertEqual(e.exception.code, code)

    def test_closed_when_taken_off_the_site(self):
        s = self.everyone_site()
        with self.c.websocket_connect("/s/fake/ws", headers=BOB) as ws:
            ws.receive_json()
            ws.send_text("a")
            self.assertEqual(ws.receive_text(), "echo:a")
            self.c.put(f"/api/admin/sites/{s['id']}", json=self.body(access="admins"), headers=ADMIN)
            self.assert_closed_with(ws, 1008)

    def test_closed_when_turned_off(self):
        self.everyone_site()
        with self.c.websocket_connect("/s/fake/ws", headers=BOB) as ws:
            ws.receive_json()
            self.c.put("/api/admin/people/u-bob", json={"enabled": False}, headers=ADMIN)
            self.assert_closed_with(ws, 1008)

    def test_still_open_after_an_unrelated_change(self):
        s = self.everyone_site()
        with self.c.websocket_connect("/s/fake/ws", headers=BOB) as ws:
            ws.receive_json()
            self.c.put(f"/api/admin/sites/{s['id']}", json=self.body(access="everyone", name="Renamed", slug="fake"),
                       headers=ADMIN)
            sites.invalidate()
            import time
            time.sleep(0.2)                            # the check has run by now
            ws.send_text("b")
            self.assertEqual(ws.receive_text(), "echo:b")

    def test_opening_and_closing_logged(self):
        s = self.everyone_site(access_log=True)
        with self.c.websocket_connect("/s/fake/ws?secret=1", headers=BOB) as ws:
            ws.receive_json()
            ws.send_text("hello")
            ws.receive_text()
        for _ in range(50):                          # the relay ends just after the browser's side closes
            proxy.flush_access_log()
            with db.get_conn() as conn:
                rows = conn.execute("SELECT method, path, status, bytes, seconds FROM access_log ORDER BY rowid").fetchall()
            if len(rows) == 2:
                break
            import time
            time.sleep(0.05)
        self.assertEqual([(r["method"], r["path"], r["status"]) for r in rows][:1], [("WS", "/ws", 101)])
        self.assertEqual((rows[1]["method"], rows[1]["path"]), ("WS", "/ws"))
        self.assertGreater(rows[1]["bytes"], 0)
        self.assertIsNotNone(rows[1]["seconds"])
        self.assertEqual(len(self.get(f"/api/admin/sites/{s['id']}/log").json()["log"]), 2)

    def test_too_many_at_once(self):
        s = self.everyone_site()
        wsproxy.OPEN.open[("u-bob", s["id"])] = wsproxy.MAX_PER_PERSON
        try:
            with self.assertRaises(WebSocketDisconnect) as e:
                with self.c.websocket_connect("/s/fake/ws", headers=BOB) as ws:
                    ws.receive_json()
            self.assertEqual(e.exception.code, 1013)
        finally:
            wsproxy.OPEN.open.pop(("u-bob", s["id"]), None)
        with self.c.websocket_connect("/s/fake/ws", headers=BOB) as ws:
            ws.receive_json()

    def test_each_checked_address_tried(self):
        self.add_site()

        async def two(host, port, allow_public):
            return ["127.0.0.2", "127.0.0.1"]         # nothing listens on the first
        from unittest import mock
        from app import netguard
        with mock.patch.object(netguard, "resolve", two):
            with self.c.websocket_connect("/s/fake/ws", headers=ADMIN) as ws:
                self.assertEqual(ws.receive_json()["path"], "/ws")


class Pieces(unittest.TestCase):
    def test_close_codes_that_may_be_sent(self):
        for got, sent in ((1000, 1000), (1001, 1001), (1005, 1000), (1006, 1000), (1015, 1000), (None, 1000),
                          (4001, 4001), (999, 1000), (1011, 1011), (2000, 1000)):
            self.assertEqual(wsproxy.close_code(got), sent, got)

    def test_address_outside_ascii(self):
        site = sites.Site(id=1, slug="x", name="X", icon="", scheme="https", host="nas.lan", port=5001,
                          origin="https://nas.lan:5001", start_path="/", access="admins", people=frozenset(),
                          spa_mode=False, show_bar=True, keep_host=False, strip_csp=False, pass_user=False,
                          allow_public=False, tls_pem=None, timeout_s=0, access_log=False, updated_at="")
        self.assertEqual(wsproxy.target_uri(site, "/ws/caf\xc3\xa9", "q=\xe2\x82\xac&t[x]=1"),
                         "wss://nas.lan:5001/ws/caf%C3%A9?q=%E2%82%AC&t[x]=1")


if __name__ == "__main__":
    unittest.main()
