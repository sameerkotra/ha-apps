"""Runs the in-page script's own JavaScript tests (tests/js/*.test.js, `node --test`) when Node is installed; skipped
otherwise. Node isn't needed to run the app."""
import _env  # noqa: F401

import glob
import os
import shutil
import subprocess
import unittest

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


@unittest.skipUnless(shutil.which("node"), "node is not installed")
class ShimJs(unittest.TestCase):
    def test_node_tests_pass(self):
        files = sorted(glob.glob(os.path.join(HERE, "tests", "js", "*.test.js")))
        self.assertTrue(files)
        r = subprocess.run(["node", "--test", *files], cwd=HERE, capture_output=True, text=True, timeout=300)
        self.assertEqual(r.returncode, 0, r.stdout[-4000:] + r.stderr[-2000:])

    def test_scripts_parse(self):
        for name in ("app.js", os.path.join("_hp", "shim.js")):
            r = subprocess.run(["node", "--check", os.path.join(HERE, "app", "static", name)],
                               capture_output=True, text=True, timeout=60)
            self.assertEqual(r.returncode, 0, name + ": " + r.stderr)


if __name__ == "__main__":
    unittest.main()
