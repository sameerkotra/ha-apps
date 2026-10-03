"""The app's files: one option, the version in step, every page on the shared shell. Standard library only."""
import glob
import os
import re
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
VERSION = "1.0.1"


def read(*parts):
    with open(os.path.join(ROOT, *parts), encoding="utf-8") as f:
        return f.read()


class PackagingTests(unittest.TestCase):
    def test_version_everywhere(self):
        self.assertIn(f'version: "{VERSION}"', read("config.yaml"))
        self.assertIn(f'APP_VERSION = "{VERSION}"', read("app", "config.py"))
        self.assertEqual(re.findall(r"(?m)^## (\S+)", read("CHANGELOG.md"))[0], VERSION)

    def test_admin_users_is_the_only_option(self):
        config = read("config.yaml")
        options = config.split("\noptions:\n", 1)[1].split("\nschema:\n")
        self.assertEqual(options[0].strip(), "admin_users: []")
        self.assertEqual(options[1].split()[:3], ["admin_users:", "-", '"str"'])
        self.assertNotRegex(config, r"(?m)^ports:")
        self.assertRegex(config, r"(?m)^panel_admin: false")
        self.assertIn("url: https://github.com/sameerkotra/ha-apps", config)
        translations = read("translations", "en.yaml")
        self.assertEqual(re.findall(r"(?m)^  (\w+):", translations), ["admin_users"])

    def test_requirements_are_pinned(self):
        for line in read("requirements.txt").splitlines():
            if line.strip() and not line.startswith("#"):
                self.assertRegex(line, r"^[A-Za-z0-9_.\-\[\]]+==\d", line)

    def test_no_leftover_launcher(self):
        self.assertIn('CMD ["python3", "-m", "app.main"]', read("Dockerfile"))
        self.assertFalse(os.path.exists(os.path.join(ROOT, "run.sh")))
        self.assertFalse(os.path.exists(os.path.join(ROOT, "build.yaml")))

    def test_every_page_uses_the_shell(self):
        pages = sorted(glob.glob(os.path.join(ROOT, "frontend", "*.html")))
        self.assertGreater(len(pages), 10)
        main = read("app", "main.py")
        for path in pages:
            name = os.path.basename(path)
            with self.subTest(name):
                html = read("frontend", name)
                head = html.split("</head>")[0]
                boot = head.index('var THEMES = ["midnight", "slate", "daylight"];')
                self.assertLess(boot, head.index('<link rel="stylesheet" href="static/app.css">'))
                self.assertIn('localStorage.getItem("theme")', head)
                self.assertLess(html.index('<script src="static/backnav.js"></script>'),
                                html.index('<script src="static/app.js"></script>'))
                self.assertNotIn("navHost", html)
                self.assertNotIn("location.href =", html)       # RPI.go keeps the back gesture working
                if name != "list.html" and name != "index.html":
                    self.assertIn(f'"{name}"', main)               # served by a route

    def test_themes_are_defined(self):
        css = read("frontend", "app.css")
        self.assertIn(':root, [data-theme="midnight"] {', css)
        block = lambda t: re.search(r'\[data-theme="%s"\] \{(.*?)\n\}' % t, css, re.S).group(1)
        names = lambda b: sorted(re.findall(r"(--[\w-]+):", b))
        for theme in ("slate", "daylight"):
            self.assertEqual(names(block(theme)), names(block("midnight")), theme)
        self.assertNotIn("prefers-color-scheme", css)

    def test_shell_lists_admin_and_app_settings(self):
        js = read("frontend", "app.js")
        self.assertIn("href: 'admin.html'", js)
        self.assertIn("'whoami.html'", js)
        admin = read("frontend", "admin.html")
        self.assertIn("api/admin/settings", admin)

    def test_user_text_says_app(self):
        for path in glob.glob(os.path.join(ROOT, "frontend", "*")) + glob.glob(os.path.join(ROOT, "app", "**", "*.py"), recursive=True):
            if path.endswith(("backnav.js",)) or os.path.isdir(path):
                continue
            with self.subTest(os.path.relpath(path, ROOT)):
                with open(path, encoding="utf-8") as f:
                    self.assertNotRegex(f.read(), r"(?i)add-?ons?\b")

    def test_whoami_never_lists_admin_users(self):
        src = read("app", "api", "homes.py")
        block = src.split('@router.get("/whoami")')[1].split("@router")[0]
        self.assertIn("len(ADMIN_USERS)", block)
        self.assertNotRegex(block, r'"[^"]*": ADMIN_USERS')


if __name__ == "__main__":
    unittest.main()
