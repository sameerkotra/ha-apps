"""Packaging of the app for the public app repository: config.yaml, translations, versions and cache busting, docs,
the changelog, icons, .dockerignore, requirements, and a scan of every text file for personal details. The checks
every app shares are in common_tests/packaging_core.py."""
import _env  # noqa: F401

import os
import re
import unittest

from common_tests import packaging_core as pk

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))     # the app folder
REPO_URL = pk.REPO_URL
VERSION = "1.3.2"
TEXT_EXT = (".py", ".html", ".md", ".yaml", ".yml", ".js", ".css", ".txt", ".json")
TEXT_NAMES = ("Dockerfile", ".dockerignore", ".gitignore")


def read(*parts):
    return pk.read(HERE, *parts)


class Packaging(unittest.TestCase):
    def test_config_yaml(self):
        cfg = pk.check_config_basics(HERE, VERSION)
        self.assertEqual(pk.section_keys(read("config.yaml"), "options"), [("admin_users", "[]")])
        self.assertEqual((cfg["slug"], cfg["name"], cfg["panel_title"], cfg["panel_icon"]),
                         ("household_phones", "Household Phones", "Phones", "mdi:cellphone-check"))
        self.assertEqual(cfg["ingress_port"], "8106")
        self.assertIs(cfg["homeassistant_api"], True)
        for key in ("host_network", "privileged", "map", "devices"):
            self.assertNotIn(key, cfg)                     # no NET_RAW, no host network: there is no ping
        self.assertTrue(10 < len(cfg["description"]) < 400)

    def test_every_version_string_matches(self):
        found = pk.check_cache_busting(HERE, VERSION, at_least=10, static_files="top")
        index = read("app", "static", "index.html")
        self.assertEqual(len(found), len(re.findall(r"<(?:script|link)\b[^>]*\b(?:src|href)=\"[^\"]+\.(?:js|css)", index)))
        with open(os.path.join(HERE, "app", "config.py"), encoding="utf-8") as f:
            self.assertIn(f'APP_VERSION = "{VERSION}"', f.read())

    def test_docs_and_layout(self):
        pk.check_files(HERE)
        docs = read("DOCS.md")
        for phrase in ("admin_users", "No admin yet", "App settings", "Backups", "Troubleshooting", "How the app sees you",
                       "Settings → Apps → Install app", "Repositories", "command_update_sensors",
                       "RECEIVE_SENSITIVE_NOTIFICATIONS", "sensor.*_last_notification", "household_phones_check_now"):
            self.assertIn(phrase, docs)
        self.assertIn(REPO_URL, docs)
        pk.check_dockerignore(HERE)
        self.assertIn("## 14. As built", read("spec", "SPEC.md"))

    def test_changelog(self):
        versions = pk.check_changelog(HERE, VERSION)
        self.assertEqual(versions, ["1.3.2", "1.3.1", "1.3.0", "1.2.0", "1.1.0", "1.0.0"])
        log = read("CHANGELOG.md")
        self.assertIn("First release", log)
        self.assertIn(REPO_URL, log)
        for line in log.splitlines():
            if line.startswith("- "):
                self.assertRegex(line, r"^- \*\*[^*]+\*\*")          # a bold lead-in on every bullet

    def test_readme_notice_and_wording(self):
        readme = read("README.md")
        notice = readme.split("\n\n")[1]
        self.assertTrue(notice.startswith("> ⚠️ **Unofficial app.**"))
        todo = os.path.join(HERE, "..", "household_todo", "README.md")
        if os.path.isfile(todo):
            with open(todo, encoding="utf-8") as f:
                self.assertEqual(notice, f.read().split("\n\n")[1])
        for name in ("README.md", "DOCS.md", "CHANGELOG.md", os.path.join("translations", "en.yaml")):
            self.assertIsNone(re.search(r"(?i)add-?ons?\b", read(name).replace("ha-apps", "")), name)

    def test_icon_and_logo_sizes(self):
        pk.check_icons(HERE)

    def test_requirements_pinned(self):
        for line in read("requirements.txt").splitlines():
            if line.strip() and not line.startswith("#"):
                self.assertRegex(line, r"^[A-Za-z0-9_.\[\]-]+==\d")

    def test_nothing_personal_in_any_text_file(self):
        hits = []
        for rel, path in pk.text_files(HERE, exts=TEXT_EXT, names=TEXT_NAMES,
                                       skip_dirs=(".git", "__pycache__", ".pytest_cache", ".venv")):
            with open(path, encoding="utf-8", errors="replace") as f:
                text = f.read().replace(REPO_URL, "")
            hits += [f"{rel}: {n}" for n in pk.find_needles(text)]
            hits += [f"{rel}: {a}" for a in pk.find_private_ipv4(text)]
            if pk.find_emails(text):
                hits.append(f"{rel}: e-mail address")
        self.assertEqual(hits, [])


if __name__ == "__main__":
    unittest.main()
