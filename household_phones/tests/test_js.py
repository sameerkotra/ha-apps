"""The page's scripts parse (`node --check`) and follow the house rules: no HTML strings built from data, no inline
handlers, every view has an address. Skipped without Node."""
import _env  # noqa: F401

import os
import re
import shutil
import subprocess
import unittest

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
STATIC = os.path.join(HERE, "app", "static")


def read(name):
    with open(os.path.join(STATIC, name), encoding="utf-8") as f:
        return f.read()


class Scripts(unittest.TestCase):
    @unittest.skipUnless(shutil.which("node"), "node is not installed")
    def test_scripts_parse(self):
        for name in ("app.js",) + tuple(os.path.join("common", n) for n in os.listdir(os.path.join(STATIC, "common"))
                                        if n.endswith(".js")):
            r = subprocess.run(["node", "--check", os.path.join(STATIC, name)], capture_output=True, text=True, timeout=60)
            self.assertEqual(r.returncode, 0, name + ": " + r.stderr)

    def test_no_html_strings_or_inline_handlers(self):
        js, html = read("app.js"), read("index.html")
        self.assertNotIn("innerHTML", js)
        self.assertNotIn("insertAdjacentHTML", js)
        self.assertIsNone(re.search(r"\son[a-z]+=", html))
        self.assertNotIn("<script>", html)

    def test_routes_and_shell(self):
        js = read("app.js")
        for route in ("/mine/", "/check/", "/phones", "/phone/", "/admin"):
            self.assertIn(route, js)
        for part in ("isTab", "parentOf", "BackNav.replace", "UI.loading", "UI.errorBox", "UI.empty", "summary-strip",
                     "ui-group-head", "aria-current", "HouseholdDeepLink.start"):
            self.assertIn(part, js)
        css = read("style.css")
        self.assertIn("max-width: 760px", css)
        self.assertIn("var(--safe-bottom)", css)
        self.assertNotRegex(css, r"color:\s*#fff")
        html = read("index.html")
        self.assertIn("danger-zone", html)
        self.assertLessEqual(html.count("data-view="), 5)            # the phone's bottom bar: at most 5 items


if __name__ == "__main__":
    unittest.main()
