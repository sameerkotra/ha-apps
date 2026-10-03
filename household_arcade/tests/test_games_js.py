"""Runs the games' own JavaScript tests (`node --test tests/js`) when Node is
installed; skipped otherwise. Node isn't needed to run the app."""
import _env  # noqa: F401

import os
import shutil
import subprocess
import unittest

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


@unittest.skipUnless(shutil.which("node"), "node is not installed")
class TestGamesJs(unittest.TestCase):
    def test_node_tests_pass(self):
        js = os.path.join(HERE, "tests", "js")
        if not os.path.isdir(js) or not any(n.endswith((".js", ".mjs")) for n in os.listdir(js)):
            self.skipTest("no JavaScript tests yet")
        r = subprocess.run(["node", "--test", "tests/js"], cwd=HERE, capture_output=True, text=True, timeout=300)
        self.assertEqual(r.returncode, 0, r.stdout[-4000:] + r.stderr[-2000:])

    def test_shell_scripts_parse(self):
        for name in ("app.js", "play.js", "admin.js", "theme-boot.js", "backnav.js"):
            r = subprocess.run(["node", "--check", os.path.join(HERE, "app", "static", name)],
                               capture_output=True, text=True, timeout=60)
            self.assertEqual(r.returncode, 0, name + ": " + r.stderr)


if __name__ == "__main__":
    unittest.main()
