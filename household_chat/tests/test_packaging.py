"""The app as it's published: config.yaml, docs, icons, cache-busting versions, and nothing personal."""
import _env  # noqa: F401

import os
import re
import struct
import unittest

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))     # the app folder
VERSION = "2.0.1"
REPO_URL = "https://github.com/sameerkotra/ha-apps"
TEXT_EXT = (".py", ".js", ".css", ".html", ".md", ".yaml", ".yml", ".txt", ".json", ".cfg", ".toml", ".ini", ".sh")
TEXT_NAMES = ("Dockerfile", ".dockerignore", ".gitignore")


def read(*parts):
    with open(os.path.join(HERE, *parts), encoding="utf-8") as f:
        return f.read()


def simple_yaml(text):
    """Enough YAML for config.yaml and translations/en.yaml (mappings, lists, block scalars, comments),
    so the test needs nothing beyond the standard library."""
    root = {}
    stack = [(-1, root)]          # (indent, container)
    lines = text.splitlines()
    i = 0
    while i < len(lines):
        raw = lines[i]
        i += 1
        if not raw.strip() or raw.lstrip().startswith("#"):
            continue
        indent = len(raw) - len(raw.lstrip(" "))
        line = raw.strip()
        while stack[-1][0] >= indent:
            stack.pop()
        parent = stack[-1][1]
        if line.startswith("- "):
            if isinstance(parent, list):
                parent.append(line[2:].strip().strip('"'))
            continue
        key, _, value = line.partition(":")
        key, value = key.strip(), value.strip()
        if value in (">-", ">", "|", "|-"):
            block = []
            while i < len(lines) and (not lines[i].strip() or len(lines[i]) - len(lines[i].lstrip(" ")) > indent):
                block.append(lines[i].strip())
                i += 1
            parent[key] = " ".join(b for b in block if b)
        elif value == "":
            nxt = next((ln for ln in lines[i:] if ln.strip() and not ln.lstrip().startswith("#")), "")
            child = [] if nxt.strip().startswith("- ") else {}
            parent[key] = child
            stack.append((indent, child))
        elif value == "[]":
            parent[key] = []
        else:
            v = value.split(" #")[0].strip()
            if v.startswith(("'", '"')):
                v = v[1:-1]
            elif v in ("true", "false"):
                v = v == "true"
            parent[key] = v
    return root


def png_size(path):
    with open(path, "rb") as f:
        head = f.read(24)
    assert head[:8] == b"\x89PNG\r\n\x1a\n", path
    return struct.unpack(">II", head[16:24])


class PackagingTests(unittest.TestCase):
    def test_config_yaml(self):
        cfg = simple_yaml(read("config.yaml"))
        self.assertEqual(cfg["version"], VERSION)
        self.assertEqual(cfg["slug"], "household_chat")
        self.assertEqual(cfg["url"], REPO_URL)
        self.assertIs(cfg["ingress"], True)
        self.assertIs(cfg["panel_admin"], False)
        self.assertNotIn("ports", cfg)
        self.assertNotIn("ports_description", cfg)
        self.assertTrue(10 < len(cfg["description"]) < 300)
        self.assertEqual(set(cfg["options"]), {"admin_users"})
        self.assertEqual(set(cfg["options"]), set(cfg["schema"]))
        for key, default in cfg["options"].items():
            self.assertIn(default, ([], ""), key)
        tr = simple_yaml(read("translations", "en.yaml"))
        self.assertEqual(set(tr["configuration"]), set(cfg["options"]))
        for key, entry in tr["configuration"].items():
            self.assertTrue(entry.get("name") and entry.get("description"), key)

    def test_versions_match(self):
        from app import config
        self.assertEqual(config.APP_VERSION, VERSION)
        index = read("app", "static", "index.html")
        found = re.findall(r"\?v=([^\"'&>\s]+)", index)
        self.assertGreaterEqual(len(found), 10)
        self.assertEqual(set(found), {VERSION})
        for name in os.listdir(os.path.join(HERE, "app", "static")):
            if name.endswith((".js", ".css", ".html")) and name != "index.html":
                for v in re.findall(r"\?v=([0-9][^\"'&>\s`]*)", read("app", "static", name)):
                    self.assertEqual(v, VERSION, name)

    def test_docs_and_layout(self):
        for name in ("README.md", "DOCS.md", "Dockerfile", "config.yaml"):
            self.assertGreater(os.path.getsize(os.path.join(HERE, name)), 200, name)
        changelog = read("CHANGELOG.md")
        self.assertTrue(changelog.startswith("# Changelog\n"))
        versions = re.findall(r"(?m)^## (.+?)\s*$", changelog)     # newest first: the top entry is this version
        self.assertEqual(versions[0], simple_yaml(read("config.yaml"))["version"])
        self.assertEqual(versions[0], VERSION)
        self.assertTrue(os.path.isfile(os.path.join(HERE, "spec", "SPEC.md")))
        self.assertFalse(os.path.exists(os.path.join(HERE, "data model")))
        docs = read("DOCS.md")
        self.assertIn(REPO_URL, docs)
        self.assertIn("admin", docs.lower())
        ignore = read(".dockerignore").split()
        for entry in ("tests", "spec", "*.md", "!README.md", "icon.png", "logo.png", "translations",
                      "requirements-dev.txt", "__pycache__"):
            self.assertIn(entry, ignore)

    def test_no_history_in_user_facing_text(self):
        for name in ("README.md", "DOCS.md", os.path.join("translations", "en.yaml"), os.path.join("spec", "SPEC.md")):
            text = read(name)
            # section numbers (§15.3.1 and "### 15.3.1" headings) aren't versions
            text = re.sub(r"§ ?\d+(\.\d+)*", "", text)
            text = re.sub(r"(?m)^#+ [\d.]+", "", text)
            self.assertIsNone(re.search(r"(?<![\d.])\d+\.\d+\.\d+(?![\d.])|\bv\d+\.\d+|\brev \d+|\bUpgrad(e|ing) to\b|\bsince v?\d+\.\d", text), name)

    def test_icon_and_logo_sizes(self):
        self.assertEqual(png_size(os.path.join(HERE, "icon.png")), (128, 128))
        self.assertEqual(png_size(os.path.join(HERE, "logo.png")), (250, 100))

    def test_nothing_personal(self):
        # built from pieces so this file doesn't match itself
        needles = ["sa" + "meer", "ko" + "tra", "mil" + "vet", "g" + "mail", "192.168.1." + "104",
                   "america/" + "den" + "ver", "den" + "ver", "colo" + "rado", "e-" + "470", "x" + "cel",
                   "par" + "ker", "au" + "rora"]
        email = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9-]+\.[A-Za-z]{2,}")
        lan = re.compile(r"\b(?:192\.168|10\.\d{1,3}|172\.(?:1[6-9]|2\d|3[01]))\.\d{1,3}\.\d{1,3}\b")
        allowed_ips = {"192.168.1.10", "172.30.32.2", "10.0.0.5"}   # an example, Supervisor's proxy, a test client
        windows = re.compile(r"[A-Za-z]:\\\\?Users", re.I)
        checked = 0
        for dirpath, dirnames, names in os.walk(HERE):
            dirnames[:] = [d for d in dirnames if d not in ("__pycache__", ".git")]
            for n in names:
                if not (n.endswith(TEXT_EXT) or n in TEXT_NAMES):
                    continue
                path = os.path.join(dirpath, n)
                rel = os.path.relpath(path, HERE)
                with open(path, encoding="utf-8", errors="replace") as f:
                    text = f.read().replace(REPO_URL, "")
                low = text.lower()
                for needle in needles:        # as a word, so "excel" is fine
                    self.assertIsNone(re.search(r"(?<![a-z])" + re.escape(needle) + r"(?![a-z])", low), f"{needle!r} in {rel}")
                self.assertIsNone(email.search(text), f"an email address in {rel}")
                self.assertIsNone(windows.search(text), f"a Windows user path in {rel}")
                for ip in lan.findall(text):
                    self.assertIn(ip, allowed_ips, f"{ip} in {rel}")
                checked += 1
        self.assertGreater(checked, 30)


if __name__ == "__main__":
    unittest.main()
