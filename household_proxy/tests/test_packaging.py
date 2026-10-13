"""Packaging of the app for the public app repository: config.yaml, translations, versions, docs, the changelog,
icons, the spec folder, and a scan of every text file for personal details. Standard library only.
The checks every app shares are in common_tests/packaging_core.py."""
import os
import re
import unittest

from common_tests import packaging_core as pk

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))     # the app folder
REPO_URL = pk.REPO_URL
VERSION = "0.6.4"

NEEDLES = pk.NEEDLES + ("c:\\" + "users",)
ALLOWED_ADDRESSES = {"192.168.1.10", "192.168.1.11", "192.168.1.12", "192.168.1.20", "192.168.1.30", "192.168.1.5",
                     "10.0.0.5", "192.168.1.1", "192.168.0.20", "240.0.0.1", "172.30.32.2", "172.30.33.9", "169.254.1.2", "100.64.1.1",
                     "172.30.32.0", "172.30.32.1", "172.30.34.1"}
TEXT_EXT = (".py", ".html", ".md", ".yaml", ".yml", ".js", ".css", ".sql", ".txt", ".json")
TEXT_NAMES = ("Dockerfile", ".dockerignore")
EMAIL = re.compile(r"(?<![:/\w.+-])[\w.+-]+@[\w-]+\.[a-z]{2,}")


def read(*parts):
    return pk.read(HERE, *parts)


class Packaging(unittest.TestCase):
    def test_config_yaml(self):
        cfg = pk.check_config_basics(HERE, VERSION)
        text = read("config.yaml")
        self.assertEqual(pk.section_keys(text, "options"), [("admin_users", "[]")])      # empty default
        self.assertEqual(cfg["slug"], "household_proxy")
        self.assertIs(cfg["ingress_stream"], True)
        self.assertIs(cfg["homeassistant_api"], True)
        self.assertNotIn("host_network", cfg)
        self.assertNotIn("map", cfg)

    def test_every_version_string_matches(self):
        pk.check_cache_busting(HERE, VERSION, at_least=10)
        with open(os.path.join(HERE, "app", "config.py"), encoding="utf-8") as f:
            self.assertIn(f'APP_VERSION = "{VERSION}"', f.read())

    def test_docs_and_layout(self):
        pk.check_files(HERE)
        docs = read("DOCS.md")
        for phrase in ("admin_users", "No admin yet", "App settings", "Backups", "Troubleshooting",
                       "software you run", "Extra headers", "How the app sees you"):
            self.assertIn(phrase, docs)
        pk.check_dockerignore(HERE)

    def test_changelog(self):
        pk.check_changelog(HERE, VERSION)
        self.assertIn(REPO_URL, read("CHANGELOG.md"))

    def test_icon_and_logo_sizes(self):
        pk.check_icons(HERE)

    def test_nothing_personal_in_any_text_file(self):
        hits = []
        for rel, path in pk.text_files(HERE, exts=TEXT_EXT, names=TEXT_NAMES,
                                       skip_dirs=(".git", "__pycache__", ".pytest_cache", ".venv")):
            with open(path, encoding="utf-8", errors="replace") as f:
                text = f.read().replace(REPO_URL, "")
            hits += [f"{rel}: {n}" for n in pk.find_needles(text, NEEDLES, whole_words=False)]
            hits += [f"{rel}: {a}" for a in pk.find_private_ipv4(text, ALLOWED_ADDRESSES)]
            if pk.find_emails(text, pattern=EMAIL):
                hits.append(f"{rel}: e-mail address")
        self.assertEqual(hits, [])


if __name__ == "__main__":
    unittest.main()
