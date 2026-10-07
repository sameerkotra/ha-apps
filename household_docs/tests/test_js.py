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


class EditorHeader(unittest.TestCase):
    """SPEC §13: the editor's header is one line — ← name (cut with "…") Share ⋯ — so a phone keeps the height for the text.
    (The layout itself is checked in a browser; this guards the pieces against drifting back.)"""

    def read(self, name):
        with open(os.path.join(HERE, "app", "static", name), encoding="utf-8") as f:
            return f.read()

    def test_share_and_more_sit_on_the_name_line(self):
        js = self.read("docs.js")
        head = js[js.index("function editorHead("):js.index("D.editorHead = editorHead;")]
        self.assertIn('h("div", { class: "doc-head-row" }, back, title, h("div", { class: "head-actions doc-actions" }, actions))', head)
        self.assertIn('id: "docShare"', head)
        self.assertIn('id: "docMore"', head)
        self.assertIn('"aria-label": "More actions"', head)
        self.assertIn("title: name", head)                  # the full name as the tooltip when it's cut
        self.assertNotIn("status", head[head.index("doc-head-row"):head.index("doc-sub-row")])   # save state on the small line

    def test_the_name_is_cut_not_wrapped(self):
        css = self.read("style.css")
        rule = css[css.index(".doc-title {"):]
        rule = rule[:rule.index("}")]
        for want in ("white-space: nowrap", "overflow: hidden", "text-overflow: ellipsis", "min-width: 0"):
            self.assertIn(want, rule)
        self.assertIn(".doc-actions { flex: none;", css)


if __name__ == "__main__":
    unittest.main()
