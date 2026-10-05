"""The app's files: one option, the version in step, every page on the shared shell. Standard library only.
The checks every app shares are in common_tests/packaging_core.py."""
import glob
import os
import re
import unittest

from common_tests import packaging_core as pk

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
VERSION = "1.1.1"


def read(*parts):
    return pk.read(ROOT, *parts)


class PackagingTests(unittest.TestCase):
    def test_version_everywhere(self):
        self.assertIn(f'version: "{VERSION}"', read("config.yaml"))
        self.assertIn(f'APP_VERSION = "{VERSION}"', read("app", "config.py"))
        self.assertEqual(re.findall(r"(?m)^## (\S+)", read("CHANGELOG.md"))[0], VERSION)
        pk.check_changelog(ROOT, VERSION)

    def test_config_basics(self):
        pk.check_config_basics(ROOT, VERSION)

    def test_icons_and_dockerignore(self):
        pk.check_icons(ROOT)
        pk.check_dockerignore(ROOT)

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
        pages = sorted(glob.glob(os.path.join(ROOT, "app", "static", "*.html")))
        self.assertGreater(len(pages), 10)
        main = read("app", "main.py")
        for path in pages:
            name = os.path.basename(path)
            with self.subTest(name):
                html = read("app", "static", name)
                head = html.split("</head>")[0]
                boot = head.index('<script src="static/common/theme-boot.js"></script>')
                self.assertLess(boot, head.index('<link rel="stylesheet" href="static/common/themes.css">'))
                self.assertLess(head.index('<link rel="stylesheet" href="static/common/themes.css">'),
                                head.index('<link rel="stylesheet" href="static/app.css">'))
                self.assertLess(html.index('<script src="static/common/backnav.js"></script>'),
                                html.index('<script src="static/app.js"></script>'))
                self.assertLess(html.index('<script src="static/common/ui.js"></script>'),
                                html.index('<script src="static/app.js"></script>'))
                # the page's own script is static/pages/<page>.js, after app.js (no inline scripts: CSP)
                page_js = f'<script src="static/pages/{name[:-5]}.js"></script>'
                self.assertLess(html.index('<script src="static/app.js"></script>'), html.index(page_js))
                self.assertNotIn("<script>", html)
                code = html + read("app", "static", "pages", name[:-5] + ".js")
                self.assertNotIn("navHost", code)
                self.assertNotIn("location.href =", code)       # RPI.go keeps the back gesture working
                if name != "list.html" and name != "index.html":
                    self.assertIn(f'"{name}"', main)               # served by a route

    def test_themes_are_defined(self):
        css = read("app", "static", "app.css")
        self.assertIn(':root, [data-theme="midnight"] {', css)
        block = lambda t: re.search(r'\[data-theme="%s"\] \{(.*?)\n\}' % t, css, re.S).group(1)
        names = lambda b: sorted(re.findall(r"(--[\w-]+):", b))
        for theme in ("slate", "daylight"):
            self.assertEqual(names(block(theme)), names(block("midnight")), theme)
        self.assertNotIn("prefers-color-scheme", css)

    def test_shell_lists_admin_and_app_settings(self):
        js = read("app", "static", "app.js")
        self.assertIn("href: 'admin.html'", js)
        self.assertIn("'whoami.html'", js)
        admin = read("app", "static", "pages", "admin.js")          # admin.html's own script
        self.assertIn("api/admin/settings", admin)

    def test_user_text_says_app(self):
        for path in (glob.glob(os.path.join(ROOT, "app", "static", "*")) + glob.glob(os.path.join(ROOT, "app", "static", "pages", "*"))
                     + glob.glob(os.path.join(ROOT, "app", "**", "*.py"), recursive=True)):
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
