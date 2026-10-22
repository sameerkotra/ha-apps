"""The page's QR encoder (app/static/qr.js, copied from Household Vault with its checks) under Node, for the Set up
this phone code (spec/SPEC.md §15.7). Skipped without Node."""
import _env  # noqa: F401

import os
import shutil
import subprocess
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))


@unittest.skipUnless(shutil.which("node"), "Node.js not installed")
class QrJs(unittest.TestCase):
    def test_qr_js(self):
        r = subprocess.run(["node", os.path.join(HERE, "qr_test.js")], capture_output=True, text=True, timeout=300)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("qr ok", r.stdout)


if __name__ == "__main__":
    unittest.main()
