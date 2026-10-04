"""The app as it's published: config.yaml, docs, icons, cache-busting versions, and nothing personal."""
import _env  # noqa: F401

import os
import re
import struct
import unittest

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))     # the app folder
VERSION = "1.5.0"
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
        self.assertEqual(cfg["slug"], "household_arcade")
        self.assertEqual(cfg["name"], "Household Arcade")
        self.assertEqual(cfg["ingress_port"], "8104")
        self.assertEqual(cfg["panel_icon"], "mdi:gamepad-variant")
        self.assertEqual(cfg["panel_title"], "Arcade")
        self.assertIs(cfg["homeassistant_api"], True)
        for flag in ("hassio_api", "auth_api", "docker_api", "full_access"):
            self.assertIs(cfg[flag], False, flag)
        self.assertNotIn("map", cfg)
        self.assertEqual(cfg["arch"], ["amd64", "aarch64", "armv7", "armhf", "i386"])
        self.assertEqual(cfg["environment"]["DATA_DIR"], "/data")
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
        index = read("app", "static", "index.html")
        found = re.findall(r"\?v=([^\"'&>\s]+)", index)
        # every script and stylesheet of the page carries the version (theme-boot.js, style.css, backnav.js, the game
        # files, app.js, play.js, admin.js, together.js ...): counted from the tags, not by hand
        self.assertEqual(len(found), len(re.findall(r"<(?:script|link)\b[^>]*\b(?:src|href)=\"[^\"]+\.(?:js|css)", index)))
        self.assertGreater(len(found), 40)
        self.assertEqual(set(found), {VERSION})
        for dirpath, _dirs, names in os.walk(os.path.join(HERE, "app", "static")):
            for name in names:
                if name.endswith((".js", ".css", ".html")) and name != "index.html":
                    with open(os.path.join(dirpath, name), encoding="utf-8") as f:
                        for v in re.findall(r"\?v=([0-9][^\"'&>\s`]*)", f.read()):
                            self.assertEqual(v, VERSION, name)
        from app import config
        self.assertEqual(config.APP_VERSION, VERSION)

    def test_docs_and_layout(self):
        for name in ("README.md", "DOCS.md", "Dockerfile", "config.yaml"):
            self.assertGreater(os.path.getsize(os.path.join(HERE, name)), 200, name)
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
            text = re.sub(r"==\d+\.\d+\.\d+", "", text)          # pinned dependencies aren't history
            text = text.replace(VERSION, "")                       # the current version is fine
            self.assertIsNone(re.search(r"(?<![\d.])\d+\.\d+\.\d+(?![\d.])|\bv\d+\.\d+|\brev \d+|\bUpgrad(e|ing) to\b|\bsince v?\d+\.\d", text), name)

    def test_changelog(self):
        # a fresh changelog: one entry, for the version in config.yaml (Home Assistant shows it on update)
        cfg = simple_yaml(read("config.yaml"))
        log = read("CHANGELOG.md")
        self.assertTrue(log.startswith("# Changelog"))
        versions = re.findall(r"(?m)^## (.+?)\s*$", log)
        # newest first: the top entry is this version (later releases add theirs above it)
        self.assertEqual(versions[0], cfg["version"])
        self.assertEqual(versions[-1], "1.0.0")           # newest first, back to the first release
        self.assertEqual(versions, sorted(versions, key=lambda v: tuple(map(int, v.split("."))), reverse=True))
        self.assertIn("First release", log)

    def test_icon_and_logo_sizes(self):
        # drawn by the coordinator; checked once they are there
        for name, size in (("icon.png", (128, 128)), ("logo.png", (250, 100))):
            path = os.path.join(HERE, name)
            if not os.path.exists(path):
                self.skipTest(f"{name} not added yet")
            self.assertEqual(png_size(path), size)

    def test_readme_notice_and_wording(self):
        readme = read("README.md")
        todo_path = os.path.join(HERE, "..", "household_todo", "README.md")
        todo = None
        if os.path.isfile(todo_path):
            with open(todo_path, encoding="utf-8") as f:
                todo = f.read()
        notice = readme.split("\n\n")[1]
        self.assertTrue(notice.startswith("> ⚠️ **Unofficial app.**"))
        if todo:
            self.assertEqual(notice, todo.split("\n\n")[1])           # the same notice as the other apps
        for name in ("README.md", "DOCS.md", "CHANGELOG.md", os.path.join("translations", "en.yaml")):
            text = read(name).replace("ha-apps", "")
            self.assertIsNone(re.search(r"(?i)add-?ons?\b", text), name)
        docs = read("DOCS.md")
        self.assertIn("Settings → Apps → Install app", docs)
        self.assertIn("Repositories", docs)
        for word in ("Tetris", "Nokia", "Arkanoid", "Breakout", "9999"):
            for name in ("README.md", "DOCS.md", os.path.join("spec", "SPEC.md")):
                self.assertNotIn(word, read(name), f"{word} in {name}")

    def test_shared_files_identical_to_household_todo(self):
        other = os.path.join(HERE, "..", "household_todo", "app")
        if not os.path.isdir(other):
            self.skipTest("household_todo isn't next to this app")
        for rel in ("ha_notify.py", "ha_people.py", os.path.join("static", "backnav.js")):
            with open(os.path.join(other, rel), "rb") as a, open(os.path.join(HERE, "app", rel), "rb") as b:
                self.assertEqual(a.read(), b.read(), rel)

    def test_requirements_pure_python_and_pinned(self):
        reqs = [l.strip() for l in read("requirements.txt").splitlines() if l.strip()]
        self.assertEqual(reqs, ["fastapi==0.141.1", "uvicorn==0.53.0", "python-multipart==0.0.32", "wsproto==1.3.2"])   # wsproto: the live link's WebSocket
        docker = read("Dockerfile")
        self.assertIn("python:3.12-alpine", docker)
        self.assertIn("tzdata", docker)
        self.assertIn("8104", docker)

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
