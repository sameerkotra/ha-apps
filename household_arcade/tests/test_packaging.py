"""The app as it's published: config.yaml, docs, icons, cache-busting versions, and nothing personal.
The checks every app shares are in common_tests/packaging_core.py."""
import _env  # noqa: F401

import os
import re
import unittest

from common_tests import packaging_core as pk

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))     # the app folder
VERSION = "1.12.2"
REPO_URL = pk.REPO_URL


def read(*parts):
    return pk.read(HERE, *parts)


class PackagingTests(unittest.TestCase):
    def test_config_yaml(self):
        cfg = pk.check_config_basics(HERE, VERSION)
        self.assertEqual(cfg["slug"], "household_arcade")
        self.assertEqual(cfg["name"], "Household Arcade")
        self.assertEqual(cfg["ingress_port"], "8104")
        self.assertEqual(cfg["panel_icon"], "mdi:gamepad-variant")
        self.assertEqual(cfg["panel_title"], "Arcade")
        self.assertIs(cfg["homeassistant_api"], True)
        for flag in ("hassio_api", "auth_api", "docker_api", "full_access"):
            self.assertIs(cfg[flag], False, flag)
        self.assertNotIn("map", cfg)
        self.assertEqual(cfg["arch"], ["amd64", "aarch64", "armv7", "armhf", "i386"])
        self.assertEqual(cfg["environment"]["DATA_DIR"], "/data")
        self.assertTrue(10 < len(cfg["description"]) < 300)

    def test_versions_match(self):
        index = read("app", "static", "index.html")
        found = pk.check_cache_busting(HERE, VERSION, at_least=41, static_files="all")
        # every script and stylesheet of the page carries the version (theme-boot.js, style.css, backnav.js, the game
        # files, app.js, play.js, admin.js, together.js ...): counted from the tags, not by hand
        self.assertEqual(len(found), len(re.findall(r"<(?:script|link)\b[^>]*\b(?:src|href)=\"[^\"]+\.(?:js|css)", index)))
        from app import config
        self.assertEqual(config.APP_VERSION, VERSION)

    def test_docs_and_layout(self):
        pk.check_files(HERE, names=("README.md", "DOCS.md", "Dockerfile", "config.yaml"))
        docs = read("DOCS.md")
        self.assertIn(REPO_URL, docs)
        self.assertIn("admin", docs.lower())
        pk.check_dockerignore(HERE)

    def test_no_history_in_user_facing_text(self):
        for name in ("README.md", "DOCS.md", os.path.join("translations", "en.yaml"), os.path.join("spec", "SPEC.md")):
            text = read(name)
            # section numbers (§15.3.1 and "### 15.3.1" headings) aren't versions
            text = re.sub(r"§ ?\d+(\.\d+)*", "", text)
            text = re.sub(r"(?m)^#+ [\d.]+", "", text)
            text = re.sub(r"==\d+\.\d+\.\d+", "", text)          # pinned dependencies aren't history
            text = text.replace(VERSION, "")                       # the current version is fine
            self.assertIsNone(re.search(r"(?<![\d.])\d+\.\d+\.\d+(?![\d.])|\bv\d+\.\d+|\brev \d+|\bUpgrad(e|ing) to\b|\bsince v?\d+\.\d", text), name)

    def test_changelog(self):
        # a fresh changelog: one entry, for the version in config.yaml (Home Assistant shows it on update)
        log = read("CHANGELOG.md")
        # newest first: the top entry is this version (later releases add theirs above it)
        versions = pk.check_changelog(HERE, VERSION)
        self.assertEqual(versions[-1], "1.0.0")           # newest first, back to the first release
        self.assertEqual(versions, sorted(versions, key=lambda v: tuple(map(int, v.split("."))), reverse=True))
        self.assertIn("First release", log)

    def test_icon_and_logo_sizes(self):
        # drawn by the coordinator; checked once they are there
        for name, size in (("icon.png", (128, 128)), ("logo.png", (250, 100))):
            path = os.path.join(HERE, name)
            if not os.path.exists(path):
                self.skipTest(f"{name} not added yet")
            self.assertEqual(pk.png_size(path), size)

    def test_readme_notice_and_wording(self):
        readme = read("README.md")
        todo_path = os.path.join(HERE, "..", "household_todo", "README.md")
        todo = None
        if os.path.isfile(todo_path):
            with open(todo_path, encoding="utf-8") as f:
                todo = f.read()
        notice = readme.split("\n\n")[1]
        self.assertTrue(notice.startswith("> ⚠️ **Unofficial app.**"))
        if todo:
            self.assertEqual(notice, todo.split("\n\n")[1])           # the same notice as the other apps
        for name in ("README.md", "DOCS.md", "CHANGELOG.md", os.path.join("translations", "en.yaml")):
            text = read(name).replace("ha-apps", "")
            self.assertIsNone(re.search(r"(?i)add-?ons?\b", text), name)
        docs = read("DOCS.md")
        self.assertIn("Settings → Apps → Install app", docs)
        self.assertIn("Repositories", docs)
        for word in ("Tetris", "Nokia", "Arkanoid", "Breakout", "9999"):
            for name in ("README.md", "DOCS.md", os.path.join("spec", "SPEC.md")):
                self.assertNotIn(word, read(name), f"{word} in {name}")

    def test_shared_files_present(self):
        # copies of the repository's common/ (tests/common_tests/test_shared_copies.py checks they're unedited)
        for rel in (os.path.join("app", "common", "ha_notify.py"), os.path.join("app", "common", "ha_people.py"),
                    os.path.join("app", "static", "common", "backnav.js")):
            self.assertTrue(os.path.exists(os.path.join(HERE, rel)), rel)

    def test_requirements_pure_python_and_pinned(self):
        reqs = [l.strip() for l in read("requirements.txt").splitlines() if l.strip()]
        self.assertEqual(reqs, ["fastapi==0.141.1", "uvicorn==0.53.0", "python-multipart==0.0.32", "wsproto==1.3.2"])   # wsproto: the live link's WebSocket
        docker = read("Dockerfile")
        self.assertIn("python:3.12-alpine", docker)
        self.assertIn("tzdata", docker)
        self.assertIn("8104", docker)

    def test_nothing_personal(self):
        hits, checked = pk.personal_details(HERE)
        self.assertEqual(hits, [])
        self.assertGreater(checked, 30)


if __name__ == "__main__":
    unittest.main()
