"""The app as it's published: config.yaml (ingress only, `map` exactly share read-write, port 8105), docs, icons,
cache-busting versions, the CHANGELOG, pinned requirements, and nothing personal. The checks every app shares
are in common_tests/packaging_core.py."""
import _env  # noqa: F401

import os
import re
import unittest

from common_tests import packaging_core as pk

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
VERSION = "1.0.1"
REPO_URL = pk.REPO_URL


def read(*parts):
    return pk.read(HERE, *parts)


class PackagingTests(unittest.TestCase):
    def test_config_yaml(self):
        cfg = pk.check_config_basics(HERE, VERSION)
        self.assertEqual(cfg["slug"], "household_docs")
        self.assertEqual(cfg["name"], "Household Docs")
        self.assertEqual(cfg["ingress_port"], "8105")
        self.assertEqual(cfg["panel_icon"], "mdi:file-document-multiple")
        self.assertEqual(cfg["panel_title"], "Docs")
        self.assertIs(cfg["homeassistant_api"], True)
        for flag in ("hassio_api", "auth_api", "docker_api", "full_access"):
            self.assertIs(cfg[flag], False, flag)
        self.assertIs(cfg["apparmor"], True)
        self.assertEqual(cfg["map"], [{"type": "share", "read_only": False}])        # exactly share, read-write
        self.assertEqual(cfg["arch"], ["amd64", "aarch64", "armv7"])
        self.assertEqual(cfg["environment"]["DATA_DIR"], "/data")
        self.assertEqual(cfg["environment"]["SHARE_DIR"], "/share")
        self.assertEqual(cfg["options"], {"admin_users": []})
        self.assertTrue(10 < len(cfg["description"]) < 300)

    def test_versions_match(self):
        index = read("app", "static", "index.html")
        found = pk.check_cache_busting(HERE, VERSION, at_least=14, static_files="all")
        self.assertEqual(len(found), len(re.findall(r"<(?:script|link)\b[^>]*\b(?:src|href)=\"[^\"]+\.(?:js|css)", index)))
        from app import config
        self.assertEqual(config.APP_VERSION, VERSION)

    def test_docs_and_layout(self):
        pk.check_files(HERE, names=("README.md", "DOCS.md", "Dockerfile", "config.yaml"))
        docs = read("DOCS.md")
        self.assertIn(REPO_URL, docs)
        self.assertIn("Settings → Apps → Install app", docs)
        self.assertIn("Repositories", docs)
        self.assertIn("readable by anyone with access to `/share`", docs)          # said plainly
        pk.check_dockerignore(HERE)

    def test_no_history_in_user_facing_text(self):
        for name in ("README.md", "DOCS.md", os.path.join("translations", "en.yaml")):
            text = read(name)
            text = re.sub(r"§ ?\d+(\.\d+)*", "", text)
            text = text.replace(VERSION, "")
            self.assertIsNone(re.search(r"(?<![\d.])\d+\.\d+\.\d+(?![\d.])|\bv\d+\.\d+|\bUpgrad(e|ing) to\b", text), name)

    def test_changelog(self):
        versions = pk.check_changelog(HERE, VERSION)
        self.assertEqual(versions, ["1.0.1", "1.0.0"])
        self.assertIn("First release", read("CHANGELOG.md"))

    def test_icon_and_logo_sizes(self):
        pk.check_icons(HERE)

    def test_readme_notice_and_wording(self):
        readme = read("README.md")
        notice = readme.split("\n\n")[1]
        self.assertTrue(notice.startswith("> ⚠️ **Unofficial app.**"))
        todo_path = os.path.join(HERE, "..", "household_todo", "README.md")
        if os.path.isfile(todo_path):
            with open(todo_path, encoding="utf-8") as f:
                self.assertEqual(notice, f.read().split("\n\n")[1])           # the same notice as the other apps
        self.assertIn("plain files", readme[:1200])
        for name in ("README.md", "DOCS.md", "CHANGELOG.md", os.path.join("translations", "en.yaml")):
            text = read(name).replace("ha-apps", "")
            self.assertIsNone(re.search(r"(?i)add-?ons?\b", text), name)

    def test_shared_files_present(self):
        for rel in (os.path.join("app", "common", "ha_notify.py"), os.path.join("app", "common", "ha_people.py"),
                    os.path.join("app", "common", "settings_core.py"), os.path.join("app", "static", "common", "backnav.js"),
                    os.path.join("app", "static", "common", "people.js")):
            self.assertTrue(os.path.exists(os.path.join(HERE, rel)), rel)

    def test_requirements_pinned(self):
        reqs = [l.strip() for l in read("requirements.txt").splitlines() if l.strip() and not l.startswith("#")]
        self.assertEqual(reqs[:3], ["fastapi==0.141.1", "uvicorn==0.53.0", "python-multipart==0.0.32"])
        for r in reqs:
            self.assertRegex(r, r"^[A-Za-z0-9_.-]+==[0-9][0-9A-Za-z.]*$")           # every one pinned exactly
        # sheets (SPEC §8.4): openpyxl with defusedxml (and openpyxl's one dependency, pinned for a reproducible image)
        for pin in ("openpyxl==3.1.5", "et-xmlfile==2.0.0", "defusedxml==0.7.1"):
            self.assertIn(pin, reqs)
        docker = read("Dockerfile")
        self.assertIn("python:3.12-alpine", docker)
        self.assertIn("tzdata", docker)
        self.assertIn("8105", docker)

    def test_nothing_personal(self):
        hits, checked = pk.personal_details(HERE)
        self.assertEqual(hits, [])
        self.assertGreater(checked, 30)


if __name__ == "__main__":
    unittest.main()
