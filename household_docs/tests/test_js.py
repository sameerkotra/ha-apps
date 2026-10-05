"""Runs the browser code's own tests (`node --test tests/js/*.test.js`) and a syntax check of every page script, when Node is
installed; skipped otherwise. Node isn't needed to run the app."""
import _env  # noqa: F401

import os
import shutil
import subprocess
import unittest

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


@unittest.skipUnless(shutil.which("node"), "node is not installed")
class BrowserCode(unittest.TestCase):
    def test_node_tests_pass(self):
        files = sorted(os.path.join("tests", "js", n) for n in os.listdir(os.path.join(HERE, "tests", "js")) if n.endswith(".test.js"))
        self.assertTrue(files)
        r = subprocess.run(["node", "--test", *files], cwd=HERE, capture_output=True, text=True, timeout=300)
        self.assertEqual(r.returncode, 0, r.stdout[-4000:] + r.stderr[-2000:])

    def test_page_scripts_parse(self):
        for name in ("textutil.js", "md.js", "app.js", "docs.js", "sheetcalc.js", "sheet.js", "files.js", "search.js", "organise.js",
                     "admin.js", "scanpdf.js", "activity.js", "bring.js", "ai.js", "start.js"):
            r = subprocess.run(["node", "--check", os.path.join(HERE, "app", "static", name)],
                               capture_output=True, text=True, timeout=60)
            self.assertEqual(r.returncode, 0, name + ": " + r.stderr)


if __name__ == "__main__":
    unittest.main()
