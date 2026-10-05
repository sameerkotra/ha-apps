"""Tests for the shared map helpers (common/python/geo.py), against a local HTTP server."""
import json
import threading
import time
import unittest
import urllib.error
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from app.common import geo


class _Handler(BaseHTTPRequestHandler):
    seen = []

    def _answer(self):
        n = int(self.headers.get("Content-Length") or 0)
        body = self.rfile.read(n) if n else b""
        _Handler.seen.append((self.command, self.path, dict(self.headers), body))
        if self.path.startswith("/missing"):
            self.send_response(404)
            self.end_headers()
            return
        data = json.dumps([{"lat": "1.5", "lon": "2.5"}]).encode()
        self.send_response(200)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    do_GET = do_POST = _answer

    def log_message(self, *a):
        pass


class GeoTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()
        cls.base = f"http://127.0.0.1:{cls.server.server_address[1]}"

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()

    def setUp(self):
        _Handler.seen.clear()

    def test_search_url(self):
        self.assertEqual(geo.search_url("http://n", "12 Main St, Town"),
                         "http://n/search?q=12+Main+St%2C+Town&format=json&limit=1")
        self.assertEqual(geo.search_url("http://n", "x", format="jsonv2", limit="1", extra={"email": "a@b"}),
                         "http://n/search?q=x&format=jsonv2&limit=1&email=a%40b")

    def test_fetch_and_first_result(self):
        body = geo.fetch(geo.search_url(self.base, "x"), headers={"User-Agent": "Test/1.0"}, timeout=5)
        self.assertEqual(geo.first_result(body), (1.5, 2.5))
        method, path, headers, _ = _Handler.seen[0]
        self.assertEqual((method, path, headers["User-Agent"]), ("GET", "/search?q=x&format=json&limit=1", "Test/1.0"))
        with self.assertRaises(IndexError):
            geo.first_result(b"[]")

    def test_fetch_post_and_errors(self):
        geo.fetch(self.base + "/interpreter", data=b"data=q", method="POST",
                  headers={"Content-Type": "application/x-www-form-urlencoded"}, timeout=5)
        self.assertEqual(_Handler.seen[0][0], "POST")
        self.assertEqual(_Handler.seen[0][3], b"data=q")
        with self.assertRaises(urllib.error.HTTPError):
            geo.fetch(self.base + "/missing", timeout=5)

    def test_lonlat(self):
        self.assertEqual(geo.lonlat([(1.5, 2.25), (3.0, 4.0)]), "2.25,1.5;4.0,3.0")
        self.assertEqual(geo.lonlat([(1.5, 2.25)], precision=6), "2.250000,1.500000")

    def test_throttle_spaces_requests(self):
        t = geo.Throttle(lambda: 0.05)
        start = time.monotonic()
        for _ in range(3):
            t.wait()
        self.assertGreaterEqual(time.monotonic() - start, 0.09)
        t = geo.Throttle(0.05)
        with t.spaced():
            pass
        ended = t.last
        with t.spaced():
            self.assertGreaterEqual(time.monotonic() - ended, 0.045)
        with self.assertRaises(RuntimeError):
            with t.spaced():
                raise RuntimeError("the request failed")
        self.assertGreater(t.last, ended)      # stamped even when the request failed


if __name__ == "__main__":
    unittest.main()
