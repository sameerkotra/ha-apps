"""The app as it's published: config.yaml, docs, icons, cache-busting versions, and nothing personal.
The checks every app shares are in common_tests/packaging_core.py."""
import _env  # noqa: F401

import os
import re
import unittest

from common_tests import packaging_core as pk

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))     # the app folder
VERSION = "2.4.4"
REPO_URL = pk.REPO_URL


def read(*parts):
    return pk.read(HERE, *parts)


class PackagingTests(unittest.TestCase):
    def test_config_yaml(self):
        cfg = pk.check_config_basics(HERE, VERSION)
        self.assertEqual(cfg["slug"], "household_todo")
        self.assertTrue(10 < len(cfg["description"]) < 300)

    def test_versions_match(self):
        # theme-boot.js, themes.css, settings.css, style.css, ui.js, settings.js, people.js, backnav.js, whoami.js,
        # connected-apps.js, app.js, maintenance.js
        pk.check_cache_busting(HERE, VERSION, count=13, static_files="top")

    def test_app_version_said_to_other_apps(self):
        from app import config as app_config
        self.assertEqual(app_config.APP_VERSION, VERSION)            # in `hello` (app_messages.py)

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
        # newest first: the top entry is this version (later releases add theirs above it)
        pk.check_changelog(HERE, VERSION)

    def test_icon_and_logo_sizes(self):
        pk.check_icons(HERE)

    def test_nothing_personal(self):
        hits, checked = pk.personal_details(HERE)
        self.assertEqual(hits, [])
        self.assertGreater(checked, 30)


if __name__ == "__main__":
    unittest.main()
