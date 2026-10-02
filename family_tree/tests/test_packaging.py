"""Packaging for the public app repository: config.yaml, translations, cache
busting, docs, the changelog, icons, the spec folder, and a scan that keeps
personal details out of every text file in the app."""
import _env

import codecs
import os
import re
import struct
import unittest

ROOT = _env.ROOT
VERSION = "2.1.0"
REPO_URL = "https://github.com/sameerkotra/ha-apps"
# Stored rot13-encoded so this file doesn't spell the words out itself.
NEEDLES = re.compile(codecs.decode(r"fnzrre|xbgen|zvyirg|tznvy|192\.168\.1\.104|nzrevpn/qraire|qraire|pbybenqb|r-470|kpry|cnexre|nheben", "rot13"), re.I)
TEXT_EXT = {".py", ".js", ".css", ".html", ".md", ".yaml", ".yml", ".txt", ".json", ".cfg", ".toml", ""}


def read(*parts):
    with open(os.path.join(ROOT, *parts), encoding="utf-8") as f:
        return f.read()


def block(text, name):
    """The keys directly under a top-level YAML block (no yaml module needed)."""
    m = re.search(rf"^{name}:\n((?:[ #].*\n|\n)*)", text, re.M)
    return re.findall(r"^  ([A-Za-z_]+):", m.group(1), re.M) if m else None


def png_size(path):
    with open(path, "rb") as f:
        head = f.read(24)
    assert head[:8] == b"\x89PNG\r\n\x1a\n", path
    return struct.unpack(">II", head[16:24])


class Packaging(unittest.TestCase):
    def test_config_yaml(self):
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
        html = read("app", "static", "index.html")
        versions = re.findall(r"\?v=([^\"'&]+)", html)
        self.assertGreaterEqual(len(versions), 5)
        self.assertEqual(set(versions), {VERSION})

    def test_version_constants(self):
        from app.routers import admin
        self.assertEqual(admin.APP_VERSION, VERSION)

    def test_changelog_starts_at_this_version(self):
        self.assertTrue(os.path.isfile(os.path.join(ROOT, "CHANGELOG.md")))
        log = read("CHANGELOG.md")
        self.assertTrue(log.startswith("# Changelog\n"))
        headings = re.findall(r"(?m)^## (.+?)\s*$", log)
        self.assertTrue(headings, "no version heading")
        cfg_version = re.search(r'(?m)^version: "([^"]+)"$', read("config.yaml")).group(1)
        self.assertEqual(headings[0], cfg_version)        # newest first: the top entry is this version
        self.assertEqual(cfg_version, VERSION)

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
        self.assertTrue(os.path.isfile(os.path.join(ROOT, "spec", "SPEC.md")))
        self.assertFalse(os.path.exists(os.path.join(ROOT, "data model")))
        self.assertNotRegex(read("spec", "SPEC.md"), r"(?i)version history|\bv0\.\d+|(?<![\d.])0\.\d+\.\d+(?![\d.])|\brev \d+|as built")

    def test_dockerignore(self):
        lines = set(read(".dockerignore").split())
        for entry in ("tests", "spec", "*.md", "!README.md", "icon.png", "logo.png", "translations",
                      "requirements-dev.txt", "**/__pycache__"):
            self.assertIn(entry, lines)

    def test_icons(self):
        self.assertEqual(png_size(os.path.join(ROOT, "icon.png")), (128, 128))
        self.assertEqual(png_size(os.path.join(ROOT, "logo.png")), (250, 100))

    def test_no_personal_details(self):
        hits = []
        for dirpath, dirs, files in os.walk(ROOT):
            dirs[:] = [d for d in dirs if d not in ("__pycache__", ".git", ".venv")]
            for fn in files:
                if os.path.splitext(fn)[1].lower() not in TEXT_EXT:
                    continue
                path = os.path.join(dirpath, fn)
                if os.path.abspath(path) == os.path.abspath(__file__):
                    continue                                    # the needle list itself
                try:
                    with open(path, encoding="utf-8") as f:
                        text = f.read()
                except UnicodeDecodeError:
                    continue
                for n, line in enumerate(text.splitlines(), 1):
                    if NEEDLES.search(line.replace(REPO_URL, "")):
                        hits.append(f"{os.path.relpath(path, ROOT)}:{n}: {line.strip()[:100]}")
        self.assertEqual(hits, [])

    def test_no_real_lan_addresses(self):
        rx = re.compile(r"\b192\.168\.\d+\.\d+\b")
        for dirpath, dirs, files in os.walk(ROOT):
            dirs[:] = [d for d in dirs if d not in ("__pycache__",)]
            for fn in files:
                if os.path.splitext(fn)[1].lower() in TEXT_EXT:
                    with open(os.path.join(dirpath, fn), encoding="utf-8", errors="ignore") as f:
                        for ip in rx.findall(f.read()):
                            self.assertEqual(ip, "192.168.1.10", fn)


if __name__ == "__main__":
    unittest.main()
