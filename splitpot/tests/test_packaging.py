"""Packaging for the public app repository: config.yaml, versions, files,
the changelog, icon/logo sizes, and nothing personal in any text file of
the app.

Standard library only (PyYAML and Pillow aren't pinned dependencies).
Run with the rest: python3 -m unittest discover -s tests"""
import codecs
import re
import struct
import unittest
from pathlib import Path

ADDON_DIR = Path(__file__).resolve().parent.parent
VERSION = "2.1.0"
REPO_URL = "https://github.com/sameerkotra/ha-apps"

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
    out, inside = {}, False
    for line in text.splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        if not line.startswith(" "):
            inside = line.split(":", 1)[0] == name
            continue
        if inside and re.match(r"^  [A-Za-z_]", line):
            key, _, rest = line.strip().partition(":")
            out[key] = rest.strip()
    return out


def top(text):
    """Top-level `key: value` pairs of a YAML file."""
    return {m.group(1): m.group(2).strip() for m in re.finditer(r"^([A-Za-z_]+):(.*)$", text, re.M)}


def png_size(path):
    data = path.read_bytes()[:24]
    assert data[:8] == b"\x89PNG\r\n\x1a\n", f"{path.name} isn't a PNG"
    return struct.unpack(">II", data[16:24])


class ConfigYaml(unittest.TestCase):
    def setUp(self):
        self.text = (ADDON_DIR / "config.yaml").read_text(encoding="utf-8")
        self.cfg = top(self.text)

    def test_metadata(self):
        self.assertEqual(self.cfg["version"].strip('"'), VERSION)
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
        html = (ADDON_DIR / "public" / "index.html").read_text(encoding="utf-8")
        versions = re.findall(r"\?v=([^\"'&]+)", html)
        self.assertEqual(len(versions), 3)   # style.css, backnav.js, app.js
        self.assertEqual(set(versions), {VERSION})

    def test_docs_and_layout(self):
        for name in ("README.md", "DOCS.md", "Dockerfile", ".dockerignore"):
            self.assertGreater((ADDON_DIR / name).stat().st_size, 100, name)
        changelog = ADDON_DIR / "CHANGELOG.md"
        self.assertTrue(changelog.is_file())
        text = changelog.read_text(encoding="utf-8")
        self.assertTrue(text.startswith("# Changelog\n"))
        # newest first: the top version heading is this version (later releases add theirs above it)
        self.assertEqual(re.findall(r"^##\s+(.+?)\s*$", text, re.M)[0], VERSION)
        self.assertTrue((ADDON_DIR / "spec" / "SPEC.md").is_file())
        self.assertFalse((ADDON_DIR / "data model").exists())
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
        lines = (ADDON_DIR / ".dockerignore").read_text(encoding="utf-8").split()
        for entry in ("tests", "spec", "*.md", "!README.md", "icon.png", "logo.png",
                      "translations", "requirements-dev.txt", "__pycache__"):
            self.assertIn(entry, lines)

    def test_icon_and_logo_sizes(self):
        self.assertEqual(png_size(ADDON_DIR / "icon.png"), (128, 128))
        self.assertEqual(png_size(ADDON_DIR / "logo.png"), (250, 100))


class NothingPersonal(unittest.TestCase):
    def text_files(self):
        for path in sorted(ADDON_DIR.rglob("*")):
            if not path.is_file() or "__pycache__" in path.parts or ".git" in path.parts:
                continue
            if path.suffix.lower() in TEXT_SUFFIXES or path.name.startswith("."):
                yield path

    def test_no_personal_details(self):
        checked = 0
        for path in self.text_files():
            text = path.read_text(encoding="utf-8", errors="replace").replace(REPO_URL, "").lower()
            for needle in NEEDLES:
                self.assertNotIn(needle, text, f"{needle!r} in {path.relative_to(ADDON_DIR)}")
            for ip in re.findall(r"\b192\.168\.\d+\.\d+\b", text):
                self.assertEqual(ip, "192.168.1.10", f"{ip} in {path.relative_to(ADDON_DIR)}")
            self.assertIsNone(re.search(r"[\w.+-]+@[\w-]+\.[a-z]{2,}\b", text.replace("@media", "")
                                        .replace("@keyframes", "").replace("@import", "")),
                              f"an email address in {path.relative_to(ADDON_DIR)}")
            checked += 1
        self.assertGreater(checked, 15)


if __name__ == "__main__":
    unittest.main()
