"""Packaging of the app for the public app repository: config.yaml (this app differs from the others: an
admin-only panel, port 11434 declared but not published, a watchdog, models left out of backups, hassio_api for
one call), translations, versions, docs, the changelog, icons, the Dockerfile and a scan of every text file for
personal details. Standard library only. The checks every app shares are in common_tests/packaging_core.py."""
import os
import re
import unittest

from common_tests import packaging_core as pk

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))     # the app folder
REPO_URL = pk.REPO_URL
VERSION = "1.0.2"

NEEDLES = pk.NEEDLES + ("c:\\" + "users",)
ALLOWED_ADDRESSES = {"192.0.2.1", "192.168.1.10", "169.254.1.2", "10.0.0.5", "10.0.0.9", "10.9.9.9", "172.30.32.2", "172.30.33.7"}
TEXT_EXT = (".py", ".html", ".md", ".yaml", ".yml", ".js", ".css", ".sql", ".txt", ".json")
TEXT_NAMES = ("Dockerfile", ".dockerignore")
EMAIL = re.compile(r"(?<![:/\w.+-])[\w.+-]+@[\w-]+\.[a-z]{2,}")


def read(*parts):
    return pk.read(HERE, *parts)


class Packaging(unittest.TestCase):
    def test_config_yaml(self):
        cfg = pk.config(HERE)
        text = read("config.yaml")
        self.assertEqual(cfg["version"], VERSION)
        self.assertEqual(cfg["url"], REPO_URL)
        self.assertEqual(cfg["slug"], "household_ai")
        self.assertIs(cfg["ingress"], True)
        self.assertIs(cfg["panel_admin"], True)                   # admins only
        self.assertEqual(re.findall(r"(?m)^  - (\w+)$", text.split("arch:")[1].split("startup")[0]),
                         ["amd64", "aarch64"])                     # Ollama is 64-bit only
        # 11434 declared (the Network tab offers it) but not published by default
        self.assertRegex(text, r"(?m)^ports:\n  11434/tcp: null$")
        self.assertIn("ports_description:", text)
        self.assertIn("watchdog: http://[HOST]:[PORT:11434]/api/version", text)
        self.assertRegex(text, r'(?m)^backup_exclude:\n  - "models"\n  - "models/\*\*"$')
        self.assertIs(cfg["homeassistant_api"], True)
        self.assertIs(cfg["hassio_api"], True)
        self.assertNotIn("hassio_role", cfg)                       # the default role
        self.assertIs(cfg["apparmor"], True)
        for key in ("auth_api", "docker_api", "full_access"):
            self.assertIs(cfg[key], False, key)
        self.assertEqual(pk.section_keys(text, "options"), [("admin_users", "[]")])
        tr = pk.simple_yaml(read("translations", "en.yaml"))
        self.assertEqual(set(tr["configuration"]), {"admin_users"})

    def test_every_version_string_matches(self):
        pk.check_cache_busting(HERE, VERSION, count=9)
        from app import config
        self.assertEqual(config.APP_VERSION, VERSION)

    def test_dockerfile(self):
        df = read("Dockerfile")
        self.assertRegex(df, r"(?m)^FROM ollama/ollama:\d+\.\d+\.\d+ AS ollama$")      # pinned
        self.assertRegex(df, r"(?m)^FROM python:3\.\d+-slim")                          # glibc, not Alpine
        # Ollama 0.40 runs models in a separate llama-server process found in /usr/lib/ollama: the whole top level of
        # that folder is copied (llama-server, its libraries, the CPU backends), never its GPU subfolders.
        self.assertIn("find /usr/lib/ollama -maxdepth 1 ! -type d -exec cp -a {} /ollama-cpu/", df)
        self.assertIn("COPY --from=ollama /ollama-cpu/ /usr/lib/ollama/", df)
        self.assertIn("COPY --from=ollama /usr/bin/ollama /usr/bin/ollama", df)
        self.assertNotRegex(df, r"COPY --from=ollama /usr/lib/ollama[/ ]")      # not the GPU subfolders

    def test_docs_and_layout(self):
        pk.check_files(HERE)
        docs = read("DOCS.md")
        for phrase in (REPO_URL, "admin_users", "No admin yet", "Use it in other apps", "Ollama", "OpenAI-compatible",
                       "Timeouts", "Keep loaded", "Require an access key", "Network", "Backups", "Troubleshooting",
                       "left out of Home Assistant's backups", "qwen2.5:3b"):
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
