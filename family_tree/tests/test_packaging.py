"""Packaging for the public app repository: config.yaml, translations, cache
busting, docs, the changelog, icons, the spec folder, and a scan that keeps
personal details out of every text file in the app. The checks every app shares
are in common_tests/packaging_core.py."""
import _env

import codecs
import os
import re
import unittest

from common_tests import packaging_core as pk

ROOT = _env.ROOT
VERSION = "2.3.0"
REPO_URL = pk.REPO_URL
# Stored rot13-encoded so this file doesn't spell the words out itself.
NEEDLES = re.compile(codecs.decode(r"fnzrre|xbgen|zvyirg|tznvy|192\.168\.1\.104|nzrevpn/qraire|qraire|pbybenqb|r-470|kpry|cnexre|nheben", "rot13"), re.I)
TEXT_EXT = {".py", ".js", ".css", ".html", ".md", ".yaml", ".yml", ".txt", ".json", ".cfg", ".toml", ""}


def read(*parts):
    return pk.read(ROOT, *parts)


def block(text, name):
    """The keys directly under a top-level YAML block (no yaml module needed)."""
    m = re.search(rf"^{name}:\n((?:[ #].*\n|\n)*)", text, re.M)
    return re.findall(r"^  ([A-Za-z_]+):", m.group(1), re.M) if m else None


class Packaging(unittest.TestCase):
    def test_config_yaml(self):
        pk.check_config_basics(ROOT, VERSION)
        cfg = read("config.yaml")
        self.assertRegex(cfg, rf'(?m)^version: "{re.escape(VERSION)}"$')
        self.assertRegex(cfg, rf"(?m)^url: {re.escape(REPO_URL)}$")
        self.assertRegex(cfg, r"(?m)^ingress: true$")
        self.assertRegex(cfg, r"(?m)^panel_admin: false$")
        self.assertNotRegex(cfg, r"(?m)^ports:")
        self.assertNotRegex(cfg, r"(?m)^ports_description:")
        options, schema = block(cfg, "options"), block(cfg, "schema")
        self.assertEqual(options, ["admin_users"])
        self.assertEqual(schema, options)
        self.assertEqual(block(read("translations", "en.yaml"), "configuration"), options)
        self.assertRegex(cfg, r"(?m)^  admin_users: \[\]$")            # empty default
        desc = re.search(r"(?ms)^description: >-\n(.*?)^\S", cfg).group(1)
        self.assertLessEqual(len(re.findall(r"[.!?](\s|$)", desc.strip())), 2)

    def test_cache_busting_follows_the_version(self):
        pk.check_cache_busting(ROOT, VERSION, at_least=5)

    def test_version_constants(self):
        from app.routers import admin
        self.assertEqual(admin.APP_VERSION, VERSION)

    def test_changelog_starts_at_this_version(self):
        pk.check_changelog(ROOT, VERSION)

    def test_docs_and_no_history(self):
        for name in ("README.md", "DOCS.md"):
            self.assertTrue(os.path.isfile(os.path.join(ROOT, name)), name)
        docs = read("DOCS.md")
        self.assertIn(REPO_URL, docs)
        for heading in ("## Getting started", "## Features you can turn on or off", "## App settings",
                        "## Who can see what, and where data goes", "## Backups", "## Limits", "## Troubleshooting"):
            self.assertIn(heading, docs)
        for text in (docs, read("README.md")):
            self.assertNotRegex(text, r"(?<![\d.])0\.\d+\.\d+(?![\d.])|\bv0\.\d+|[Uu]pgrading to|coming in")

    def test_spec_folder(self):
        pk.check_files(ROOT, names=())             # spec/SPEC.md there, no "data model" folder
        self.assertNotRegex(read("spec", "SPEC.md"), r"(?i)version history|\bv0\.\d+|(?<![\d.])0\.\d+\.\d+(?![\d.])|\brev \d+|as built")

    def test_dockerignore(self):
        pk.check_dockerignore(ROOT, [e for e in pk.DOCKERIGNORE if e != "__pycache__"] + ["**/__pycache__"])

    def test_icons(self):
        pk.check_icons(ROOT)

    def test_no_personal_details(self):
        hits = []
        for rel, path in pk.text_files(ROOT, suffixes=TEXT_EXT, skip_dirs=("__pycache__", ".git", ".venv"),
                                       skip=(__file__,)):                 # this file holds the needle list itself
            try:
                with open(path, encoding="utf-8") as f:
                    text = f.read()
            except UnicodeDecodeError:
                continue
            for n, line in enumerate(text.splitlines(), 1):
                if NEEDLES.search(line.replace(REPO_URL, "")):
                    hits.append(f"{rel}:{n}: {line.strip()[:100]}")
        self.assertEqual(hits, [])

    def test_no_real_lan_addresses(self):
        for rel, path in pk.text_files(ROOT, suffixes=TEXT_EXT, skip_dirs=("__pycache__",)):
            with open(path, encoding="utf-8", errors="ignore") as f:
                self.assertEqual(pk.find_lan_addresses(f.read(), {"192.168.1.10"}, re.compile(r"\b192\.168\.\d+\.\d+\b")),
                                 [], rel)


if __name__ == "__main__":
    unittest.main()
