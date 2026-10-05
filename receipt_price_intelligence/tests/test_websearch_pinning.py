"""Pages the app fetches itself: the host name is looked up once, refused unless every address is public, and
the connection goes to exactly the address that was checked (no DNS-rebinding window). Redirects are
checked and pinned again. Uses a fake resolver and a local server; no network."""
import os
import socket
import sys
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import app_settings  # noqa: E402
from app.services import websearch  # noqa: E402

PUBLIC = "93.184.216.34"          # documentation-style public addresses (never contacted: see connect())
PUBLIC_2 = "93.184.216.35"
PRIVATE = "192.168.1.1"


class Handler(BaseHTTPRequestHandler):
    seen: list = []

    def log_message(self, *args):
        pass

    def do_GET(self):  # noqa: N802
        Handler.seen.append((self.path, self.headers.get("Host")))
        if self.path == "/to-private":
            self.send_response(302)
            self.send_header("Location", "http://internal.example/admin")
            self.end_headers()
            return
        if self.path == "/to-other":
            self.send_response(302)
            self.send_header("Location", "http://other.example/page")
            self.end_headers()
            return
        body = f"<html><body>page {self.path} for {self.headers.get('Host')}</body></html>".encode()
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


class PinningTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.dir = tempfile.mkdtemp()
        os.environ.setdefault("DATABASE_PATH", os.path.join(cls.dir, "t.db"))
        app_settings.configure(os.environ["DATABASE_PATH"])
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        cls.port = cls.server.server_address[1]
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()

    def setUp(self):
        Handler.seen = []
        self.lookups = []
        self.connects = []
        # first answer public, every later one private: a rebinding name
        self.answers = {"shop.example": [PUBLIC, PRIVATE], "other.example": [PUBLIC_2, PRIVATE],
                        "internal.example": [PRIVATE]}
        real_getaddrinfo = socket.getaddrinfo
        real_connect = socket.create_connection

        def getaddrinfo(host, port, *args, **kwargs):
            if host in self.answers:
                self.lookups.append(host)
                queue = self.answers[host]
                ip = queue.pop(0) if len(queue) > 1 else queue[0]
                return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (ip, port or 0))]
            return real_getaddrinfo(host, port, *args, **kwargs)

        def create_connection(address, *args, **kwargs):
            self.connects.append(tuple(address))
            if address[0] in (PUBLIC, PUBLIC_2):              # "the internet" is the local test server
                return real_connect(("127.0.0.1", self.port), *args, **kwargs)
            if address[0] in self.answers:                    # connecting by name: would look it up again
                ip = getaddrinfo(address[0], address[1])[0][4][0]
                if ip == PRIVATE:
                    raise AssertionError("connected into the home network")
            raise OSError("no other network in tests")

        for p in (mock.patch.object(socket, "getaddrinfo", getaddrinfo),
                  mock.patch.object(socket, "create_connection", create_connection),
                  mock.patch.dict(os.environ, {"http_proxy": "", "HTTP_PROXY": "", "https_proxy": "",
                                               "HTTPS_PROXY": "", "no_proxy": "*"})):
            p.start()
            self.addCleanup(p.stop)

    def fetch(self, url):
        headers = {"User-Agent": "test", "Accept": "text/html"}
        return websearch._fetch_direct(url, headers)

    def test_connects_to_the_checked_address_only(self):
        text, status, kind, final = self.fetch("http://shop.example/page")
        self.assertEqual(status, 200)
        self.assertIn("page /page for shop.example", text)              # the Host header keeps the name
        self.assertEqual(self.lookups, ["shop.example"])                # looked up once
        self.assertEqual(self.connects, [(PUBLIC, 80)])                 # and connected to that address

    def test_private_address_refused_before_connecting(self):
        with self.assertRaises(websearch.WebSearchError):
            self.fetch("http://internal.example/")
        self.assertEqual(self.connects, [])

    def test_redirect_into_the_home_network_refused(self):
        with self.assertRaises(websearch.WebSearchError) as e:
            self.fetch("http://shop.example/to-private")
        self.assertIn("public internet", str(e.exception))
        self.assertEqual(self.connects, [(PUBLIC, 80)])

    def test_redirect_is_checked_and_pinned_again(self):
        text, status, kind, final = self.fetch("http://shop.example/to-other")
        self.assertEqual(status, 200)
        self.assertEqual(final, "http://other.example/page")
        self.assertIn("for other.example", text)
        self.assertEqual(self.lookups, ["shop.example", "other.example"])
        self.assertEqual(self.connects, [(PUBLIC, 80), (PUBLIC_2, 80)])

    def test_https_keeps_the_name_for_sni_and_the_certificate(self):
        wrapped = {}

        class Context:
            check_hostname = True

            def wrap_socket(self, sock, server_hostname=None, **kw):
                wrapped["server_hostname"] = server_hostname
                return sock

        conn = websearch._PinnedHTTPSConnection("shop.example", 443, pin=("shop.example", PUBLIC),
                                                context=Context(), timeout=1)
        with mock.patch.object(socket, "create_connection",
                               side_effect=lambda addr, *a, **k: (self.connects.append(addr), socket.socket())[1]):
            conn.connect()
        conn.sock.close()
        self.assertEqual(self.connects, [(PUBLIC, 443)])
        self.assertEqual(wrapped["server_hostname"], "shop.example")

    def test_check_url_rules_unchanged(self):
        for url in ("ftp://shop.example/", "http://shop.example:22/", "javascript:alert(1)"):
            with self.subTest(url=url), self.assertRaises(websearch.WebSearchError):
                websearch._check_url(url)
        self.assertEqual(websearch._check_url("https://shop.example:8443/x"), ("shop.example", PUBLIC))
        self.assertFalse(websearch._public_host("internal.example"))


if __name__ == "__main__":
    unittest.main()
