"""The app as it's published: config.yaml (experimental stage, no extra folder mappings), docs, icons,
cache-busting versions, a one-version CHANGELOG.md, the first-run "No admin yet" notice, and nothing personal.
The checks every app shares are in common_tests/packaging_core.py."""
import _env  # noqa: F401

import os
import re
import unittest
from unittest import mock

from common_tests import packaging_core as pk

from base import ADMIN, ApiTestCase

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))     # the app folder
VERSION = "2.1.3"
REPO_URL = pk.REPO_URL


def read(*parts):
    return pk.read(HERE, *parts)


class PackagingTests(unittest.TestCase):
    def test_config_yaml(self):
        cfg = pk.check_config_basics(HERE, VERSION)
        self.assertEqual(cfg["slug"], "household_vault")
        self.assertEqual(cfg["stage"], "experimental")         # Home Assistant shows an "experimental" badge
        self.assertNotIn("map", cfg)                            # only /data (always mapped); no /share
        self.assertNotIn("share", read("config.yaml").replace("shared", ""))
        for flag in ("hassio_api", "auth_api", "docker_api", "full_access"):
            self.assertIs(cfg[flag], False, flag)
        self.assertTrue(10 < len(cfg["description"]) < 300)

    def test_versions_match(self):
        from app.routers import admin
        self.assertEqual(admin.APP_VERSION, VERSION)
        pk.check_cache_busting(HERE, VERSION, at_least=5, static_files="top")

    def test_docs_and_layout(self):
        pk.check_files(HERE, names=("README.md", "DOCS.md", "Dockerfile", "config.yaml"))
        docs = read("DOCS.md")
        self.assertIn(REPO_URL, docs)
        self.assertIn("admin_users", docs)
        readme = read("README.md")
        self.assertIn("**Unofficial app.** This is not an official Home Assistant app", readme)
        # README: the unofficial notice first, then straight away the experimental one
        self.assertRegex(readme, r"^# Household Vault\n\n> ⚠️ \*\*Unofficial app\.\*\*[^\n]*\n(?:>[^\n]*\n)*\n> \*\*Experimental\.\*\*")
        # DOCS has no unofficial notice: the experimental one comes first
        self.assertRegex(read("DOCS.md"), r"^# Household Vault\n\n> \*\*Experimental\.\*\*")
        for name in ("README.md", "DOCS.md"):
            text = read(name)
            self.assertIn("Experimental", text[:1500], name)
            self.assertIn("independent security review", text[:1500], name)
            self.assertIn("Download all my passwords", text[:1500], name)
        pk.check_dockerignore(HERE)

    def test_changelog(self):
        # Home Assistant shows CHANGELOG.md in the update dialog; it starts again at the release version
        # newest first: the top entry is this version (later releases add theirs above it)
        pk.check_changelog(HERE, VERSION)

    def test_no_history_in_user_facing_text(self):
        # CHANGELOG.md is exempt: version numbers are its whole point
        for name in ("README.md", "DOCS.md", os.path.join("translations", "en.yaml"), os.path.join("spec", "SPEC.md")):
            text = read(name)
            # section numbers (§12.3 and "### 12.3" headings) aren't versions; KeePass 2.47 is someone else's
            text = re.sub(r"§ ?\d+(\.\d+)*", "", text)
            text = re.sub(r"(?m)^#+ [\d.]+", "", text)
            text = text.replace("KeePass 2.47", "KeePass")
            self.assertIsNone(re.search(r"(?<![\d.])\d+\.\d+\.\d+(?![\d.])|\bv\d+\.\d+|\brev \d+|\bUpgrad(e|ing) to\b"
                                        r"|\bsince v?\d+\.\d|\bmilestone|/share\b", text, re.I), name)

    def test_icon_and_logo_sizes(self):
        pk.check_icons(HERE)

    def test_nothing_personal(self):
        # documentation-only addresses: example.com, and the account in Google's published sample export
        hits, checked = pk.personal_details(
            HERE, needles=pk.NEEDLES + ("pri" + "ya", "ra" + "vi"),
            allowed_emails=r"@example\.(com|org|net)$|^alice@google\.com$")
        self.assertEqual(hits, [])
        self.assertGreater(checked, 30)


class NoAdminYet(ApiTestCase):
    """A fresh install has an empty admin_users: everyone is told how to fix it; nobody is promoted."""

    def test_flag_when_admin_list_is_empty(self):
        from app import config
        self.assertFalse(self.ok(self.get("/api/me", token=False))["noAdmin"])
        stranger = {"id": "u-first", "name": "first.visitor", "display": "First Visitor"}
        with mock.patch.object(config, "ADMIN_NAMES", set()):
            me = self.ok(self.get("/api/me", stranger, token=False))
            self.assertTrue(me["noAdmin"])
            self.assertEqual(me["username"], "first.visitor")          # what the banner tells them to add
            self.assertFalse(me["isAdmin"])                             # the first visitor isn't promoted
            self.assertTrue(self.ok(self.get("/api/whoami", stranger, token=False))["noAdmin"])
            self.assertEqual(self.get("/api/admin/settings", stranger, token=False).status_code, 403)
            self.assertFalse(self.ok(self.get("/api/me", ADMIN, token=False))["isAdmin"])

    def test_banner_in_the_page(self):
        js = read("app", "static", "app.js")
        self.assertIn("HouseholdWhoami.noAdminBanner(state.me.username", js)
        self.assertIn('src="common/whoami.js', read("app", "static", "index.html"))
        shared = read("app", "static", "common", "whoami.js")       # the banner's text, the same in every app
        self.assertIn('"No admin yet"), " — add your Home Assistant user name', shared)
        self.assertIn("admin_users", shared)
        self.assertIn('"How the app sees you")', js)
        # shown on every screen: every top-level mount goes through topBanners()
        self.assertNotIn('mount($("#app"), httpBanner()', js)
        self.assertEqual(js.count("topBanners()"), js.count('mount($("#app"), topBanners()') + 2)   # + definition + main shell
        self.assertIn("Experimental", js)


if __name__ == "__main__":
    unittest.main()
