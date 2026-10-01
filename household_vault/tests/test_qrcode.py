"""The server's QR encoder (app/qrcode.py) draws exactly what the browser's (static/qr.js) does."""
import _env  # noqa: F401

import json
import os
import shutil
import subprocess
import unittest

from app import qrcode

HERE = os.path.dirname(os.path.abspath(__file__))


class ServerQr(unittest.TestCase):
    def test_wifi_text(self):
        self.assertEqual(qrcode.wifi_text('My;Net', 'p:a,s"s\\', "WPA"), 'WIFI:T:WPA;S:My\;Net;P:p\\:a\\,s\\"s\\\;;')
        self.assertEqual(qrcode.wifi_text("Cafe", "", "nopass"), "WIFI:T:nopass;S:Cafe;;")

    def test_svg_is_small_and_plain(self):
        s = qrcode.svg(qrcode.wifi_text("Home", "correct horse battery", "WPA"))
        self.assertTrue(s.startswith("<svg") and "<script" not in s)
        self.assertLess(len(s), 12000)

    @unittest.skipUnless(shutil.which("node"), "Node.js not installed")
    def test_same_modules_as_the_browser(self):
        texts = ["WIFI:T:WPA;S:Home;P:secret;;", "a", "ä€ unicode ✓ " * 3,
                 "otpauth://totp/Ex?secret=JBSWY3DPEHPK3PXP", "x" * 150, "y" * 700, "z" * 2300]
        r = subprocess.run(["node", os.path.join(HERE, "qr_compare.js")], input=json.dumps(texts), capture_output=True,
                           text=True, timeout=300)
        self.assertEqual(r.returncode, 0, r.stderr)
        js = json.loads(r.stdout)
        for t, grid in zip(texts, js):
            py = ["".join("1" if b else "0" for b in row) for row in qrcode.encode(t)["modules"]]
            self.assertEqual(py, grid, t[:30])


if __name__ == "__main__":
    unittest.main()
