"""Websockets through the proxy (spec/SPEC.md §3.6): relayed both ways, subprotocols kept, Origin and cookies cleaned,
the same access check at the upgrade, other sites' pages refused."""
import _env  # noqa: F401  (must be first)

import unittest

from starlette.websockets import WebSocketDisconnect

from helpers import ADMIN, BOB, ProxyCase


class Websockets(ProxyCase):
    def test_relayed_both_ways(self):
        self.add_site(headers=[{"name": "Authorization", "value": "Bearer k"}])
        with self.c.websocket_connect("/s/fake/ws?x=1", headers={**ADMIN, "Cookie": "ingress_session=S; a=1",
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


if __name__ == "__main__":
    unittest.main()
