"""Packaging for the public app repository: config.yaml, versions, files,
the changelog, icon/logo sizes, and nothing personal in any text file of
the app.

Standard library only (PyYAML and Pillow aren't pinned dependencies).
Run with the rest: python3 -m unittest discover -s tests
The checks every app shares are in common_tests/packaging_core.py."""
import codecs
import re
import unittest
from pathlib import Path

from common_tests import packaging_core as pk

ADDON_DIR = Path(__file__).resolve().parent.parent
VERSION = "2.4.0"
REPO_URL = pk.REPO_URL

# Personal details that must never appear (the repository URL is the one
# allowed occurrence of the owner's handle, and is removed before checking).
# Stored rot13-encoded so this file doesn't spell them out itself; any
# 192.168.x.y other than the example 192.168.1.10 and any e-mail address are
# checked separately below.
NEEDLES = tuple(codecs.decode(n, "rot13") for n in (
    "fnzrre", "xbgen", "zvyirg", "tznvy", "nzrevpn/qraire", "qraire", "pbybenqb",
    "r-470", "kpry", "cnexre", "nheben", "p:\\hfref"))
TEXT_SUFFIXES = {".py", ".js", ".css", ".html", ".md", ".yaml", ".yml", ".sql",
                 ".mermaid", ".txt", ".json", ".cfg", ".toml", ".sh", ""}


def section(text, name):
    """{key: rest-of-line} directly under a top-level `name:` (a tiny YAML
    reader, enough for config.yaml and translations/en.yaml)."""
    return dict(pk.section_keys(text, name))


class ConfigYaml(unittest.TestCase):
    def setUp(self):
        self.text = (ADDON_DIR / "config.yaml").read_text(encoding="utf-8")
        self.cfg = pk.top_level(self.text)

    def test_metadata(self):
        pk.check_config_basics(ADDON_DIR, VERSION)
        self.assertEqual(self.cfg["version"], VERSION)
        self.assertEqual(self.cfg["url"], REPO_URL)
        self.assertEqual(self.cfg["ingress"], "true")
        self.assertEqual(self.cfg["panel_admin"], "false")
        self.assertNotIn("ports", self.cfg)
        self.assertNotIn("ports_description", self.cfg)
        self.assertNotIn("webui", self.cfg)

    def test_options_schema_translations_agree_and_defaults_empty(self):
        options = section(self.text, "options")
        schema = section(self.text, "schema")
        tr = section((ADDON_DIR / "translations" / "en.yaml").read_text(encoding="utf-8"), "configuration")
        self.assertEqual(set(options), {"admin_users"})
        self.assertEqual(set(options), set(schema))
        self.assertEqual(set(options), set(tr))
        self.assertEqual(options["admin_users"], "[]")


class Files(unittest.TestCase):
    def test_every_asset_version_matches(self):
        # theme-boot.js, themes.css, settings.css, style.css, ui.js, settings.js, people.js, backnav.js, whoami.js, app.js
        pk.check_cache_busting(ADDON_DIR, VERSION, count=10)

    def test_docs_and_layout(self):
        pk.check_files(ADDON_DIR, names=("README.md", "DOCS.md", "Dockerfile", ".dockerignore"), min_size=100)
        # newest first: the top version heading is this version (later releases add theirs above it)
        pk.check_changelog(ADDON_DIR, VERSION)
        docs = (ADDON_DIR / "DOCS.md").read_text(encoding="utf-8")
        self.assertIn(REPO_URL, docs)
        self.assertIn("No admin yet", docs)

    def test_no_version_history_in_docs(self):
        for name in ("README.md", "DOCS.md", "spec/SPEC.md"):
            text = (ADDON_DIR / name).read_text(encoding="utf-8")
            # x.y.z release numbers; dependency pins (==x.y.z) and IP addresses don't count
            self.assertIsNone(re.search(r"(?<![\w.=])v?\d+\.\d+\.\d+(?![\d.])", text), name)
            self.assertNotIn("Version history", text, name)
            self.assertNotIn("Upgrading", text, name)

    def test_dockerignore_keeps_dev_files_out(self):
        pk.check_dockerignore(ADDON_DIR)

    def test_icon_and_logo_sizes(self):
        pk.check_icons(ADDON_DIR)


class NothingPersonal(unittest.TestCase):
    def test_no_personal_details(self):
        checked = 0
        for rel, path in pk.text_files(ADDON_DIR, suffixes=TEXT_SUFFIXES, dotfiles=True):
            text = Path(path).read_text(encoding="utf-8", errors="replace").replace(REPO_URL, "").lower()
            self.assertEqual(pk.find_needles(text, NEEDLES, whole_words=False), [], rel)
            self.assertEqual(pk.find_lan_addresses(text, {"192.168.1.10"}, re.compile(r"\b192\.168\.\d+\.\d+\b")), [], rel)
            self.assertEqual(pk.find_emails(text.replace("@media", "").replace("@keyframes", "").replace("@import", ""),
                                            pattern=re.compile(r"[\w.+-]+@[\w-]+\.[a-z]{2,}\b")), [],
                             f"an email address in {rel}")
            checked += 1
        self.assertGreater(checked, 15)


if __name__ == "__main__":
    unittest.main()
