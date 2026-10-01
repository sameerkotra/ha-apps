"""Packaging of the app for the public app repository: config.yaml,
translations, versions, docs, the changelog, icons, the spec folder, and a scan of every
text file for personal details. Standard library only (no PyYAML/Pillow)."""
import ipaddress
import os
import re
import struct
import unittest

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))     # the app folder
REPO_URL = "https://github.com/sameerkotra/ha-apps"
VERSION = "2.0.0"

# Split up so this file doesn't match a plain grep for them itself.
NEEDLES = ["same" + "er", "kot" + "ra", "mil" + "vet", "gm" + "ail", "america/den" + "ver", "den" + "ver",
           "colo" + "rado", "e-" + "470", "xc" + "el", "par" + "ker", "aur" + "ora", "c:\\" + "users"]
IPV4 = re.compile(r"(?<![\d.])\d{1,3}(?:\.\d{1,3}){3}(?![\d.])")
ALLOWED_ADDRESSES = {"192.168.1.10", "169.254.1.2", "10.0.0.5", "10.0.0.9", "10.9.9.9", "172.30.32.2", "172.30.33.7"}
TEXT_EXT = (".py", ".html", ".md", ".yaml", ".yml", ".js", ".css", ".sql", ".txt", ".json")
TEXT_NAMES = ("Dockerfile", ".dockerignore")


def read(*parts):
    with open(os.path.join(HERE, *parts), encoding="utf-8") as f:
        return f.read()


def section_keys(text, section):
    """Keys directly under a top-level YAML section (two-space indent)."""
    body = ("\n" + text).split(f"\n{section}:", 1)[1]
    keys = []
    for line in body.splitlines()[1:]:
        if line.strip() == "" or line.lstrip().startswith("#"):
            continue
        if not line.startswith(" "):      # next top-level key
            break
        m = re.match(r"^  ([A-Za-z_][A-Za-z0-9_]*):(.*)$", line)
        if m:
            keys.append((m.group(1), m.group(2).strip()))
    return keys


def top_level(text):
    return {m.group(1): m.group(2).strip().strip('"')
            for m in re.finditer(r"^([A-Za-z_][A-Za-z0-9_]*):[ \t]*(.*)$", text, re.M)}


def _private(address):
    try:
        ip = ipaddress.ip_address(address)
    except ValueError:
        return False
    return ip.is_private and not ip.is_loopback and not ip.is_unspecified


def png_size(path):
    with open(path, "rb") as f:
        head = f.read(24)
    assert head[:8] == b"\x89PNG\r\n\x1a\n", path
    return struct.unpack(">II", head[16:24])


class Packaging(unittest.TestCase):
    def test_config_yaml(self):
        cfg = read("config.yaml")
        top = top_level(cfg)
        self.assertEqual(top["version"], VERSION)
        self.assertEqual(top["url"], REPO_URL)
        self.assertEqual(top["ingress"], "true")
        self.assertEqual(top["panel_admin"], "false")
        self.assertNotIn("ports", top)
        self.assertNotIn("host_network", top)
        self.assertEqual(section_keys(cfg, "options"), [("admin_users", "[]")])      # empty default
        self.assertEqual([k for k, _ in section_keys(cfg, "schema")], ["admin_users"])
        self.assertEqual([k for k, _ in section_keys(read("translations", "en.yaml"), "configuration")], ["admin_users"])
        for word in ("switch_admins", "ollama_url", "ollama_model", "ai_url", "ai_api_key"):
            self.assertNotIn(word, cfg)
            self.assertNotIn(word, read("translations", "en.yaml"))

    def test_every_version_string_matches(self):
        found = re.findall(r"\?v=([0-9A-Za-z.]+)", read("app", "static", "index.html"))
        self.assertGreaterEqual(len(found), 3)
        self.assertEqual(set(found), {VERSION})

    def test_docs_and_layout(self):
        for name in ("README.md", "DOCS.md", "Dockerfile"):
            self.assertGreater(os.path.getsize(os.path.join(HERE, name)), 200, name)
        self.assertTrue(os.path.isfile(os.path.join(HERE, "spec", "SPEC.md")))
        self.assertTrue(os.path.isfile(os.path.join(HERE, "spec", "calorie_tracker_schema.sql")))
        self.assertFalse(os.path.exists(os.path.join(HERE, "data model")))
        docs = read("DOCS.md")
        for phrase in (REPO_URL, "admin_users", "No admin yet", "Setting up the AI", "App settings", "Anthropic",
                       "OpenAI-compatible", "Ollama", "LM Studio", "Backups", "Troubleshooting"):
            self.assertIn(phrase, docs)
        ignore = read(".dockerignore").split()
        for entry in ("tests", "spec", "*.md", "!README.md", "icon.png", "logo.png", "translations",
                      "requirements-dev.txt", "__pycache__"):
            self.assertIn(entry, ignore)

    def test_changelog(self):
        log = read("CHANGELOG.md")
        self.assertTrue(log.startswith("# Changelog\n"))
        headings = re.findall(r"^## (.+?)\s*$", log, re.M)
        # newest first: the top entry is this version (later releases add theirs above it)
        self.assertEqual(headings[0], top_level(read("config.yaml"))["version"])
        self.assertEqual(headings[0], VERSION)
        self.assertIn(REPO_URL, log)

    def test_icon_and_logo_sizes(self):
        self.assertEqual(png_size(os.path.join(HERE, "icon.png")), (128, 128))
        self.assertEqual(png_size(os.path.join(HERE, "logo.png")), (250, 100))

    def test_no_version_history_in_texts(self):
        for rel in ("README.md", "DOCS.md", os.path.join("spec", "SPEC.md"), os.path.join("translations", "en.yaml")):
            text = read(rel)
            # no earlier release numbers of this app (pinned dependencies like 0.141.1 are fine)
            self.assertNotRegex(text, r"(?<![\d.=])1\.\d+\.\d+(?![\d.])", rel)
            self.assertNotRegex(text, r"(?i)version history|upgrading (from|to) \d", rel)

    def test_nothing_personal_in_any_text_file(self):
        hits = []
        for dirpath, dirnames, names in os.walk(HERE):
            dirnames[:] = [d for d in dirnames if d not in (".git", "__pycache__", ".pytest_cache", ".venv")]
            for name in names:
                if not (name.endswith(TEXT_EXT) or name in TEXT_NAMES):
                    continue
                path = os.path.join(dirpath, name)
                rel = os.path.relpath(path, HERE)
                with open(path, encoding="utf-8", errors="replace") as f:
                    text = f.read().replace(REPO_URL, "")
                low = text.lower()
                hits += [f"{rel}: {n}" for n in NEEDLES if n in low]
                hits += [f"{rel}: {a}" for a in IPV4.findall(text)
                         if a not in ALLOWED_ADDRESSES and _private(a)]
                if re.search(r"(?<![:/\w.+-])[\w.+-]+@[\w-]+\.[a-z]{2,}", text):
                    hits.append(f"{rel}: e-mail address")
        self.assertEqual(hits, [])


if __name__ == "__main__":
    unittest.main()
