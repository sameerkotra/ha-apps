"""Packaging of the app for the public app repository: config.yaml,
translations, versions, docs, the changelog, icons, the spec folder, and a scan of every
text file for personal details. Standard library only (no PyYAML/Pillow).
The checks every app shares are in common_tests/packaging_core.py."""
import os
import re
import unittest

from common_tests import packaging_core as pk

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))     # the app folder
REPO_URL = pk.REPO_URL
VERSION = "2.1.1"

NEEDLES = pk.NEEDLES + ("c:\\" + "users",)
ALLOWED_ADDRESSES = {"192.168.1.10", "169.254.1.2", "10.0.0.5", "10.0.0.9", "10.9.9.9", "172.30.32.2", "172.30.33.7"}
TEXT_EXT = (".py", ".html", ".md", ".yaml", ".yml", ".js", ".css", ".sql", ".txt", ".json")
TEXT_NAMES = ("Dockerfile", ".dockerignore")
EMAIL = re.compile(r"(?<![:/\w.+-])[\w.+-]+@[\w-]+\.[a-z]{2,}")


def read(*parts):
    return pk.read(HERE, *parts)


class Packaging(unittest.TestCase):
    def test_config_yaml(self):
        pk.check_config_basics(HERE, VERSION)
        cfg = read("config.yaml")
        self.assertEqual(pk.section_keys(cfg, "options"), [("admin_users", "[]")])      # empty default
        for word in ("switch_admins", "ollama_url", "ollama_model", "ai_url", "ai_api_key"):
            self.assertNotIn(word, cfg)
            self.assertNotIn(word, read("translations", "en.yaml"))

    def test_every_version_string_matches(self):
        pk.check_cache_busting(HERE, VERSION, at_least=3)

    def test_docs_and_layout(self):
        pk.check_files(HERE)
        self.assertTrue(os.path.isfile(os.path.join(HERE, "spec", "calorie_tracker_schema.sql")))
        docs = read("DOCS.md")
        for phrase in (REPO_URL, "admin_users", "No admin yet", "Setting up the AI", "App settings", "Anthropic",
                       "OpenAI-compatible", "Ollama", "LM Studio", "Backups", "Troubleshooting"):
            self.assertIn(phrase, docs)
        pk.check_dockerignore(HERE)

    def test_changelog(self):
        pk.check_changelog(HERE, VERSION)
        self.assertIn(REPO_URL, read("CHANGELOG.md"))

    def test_icon_and_logo_sizes(self):
        pk.check_icons(HERE)

    def test_no_version_history_in_texts(self):
        for rel in ("README.md", "DOCS.md", os.path.join("spec", "SPEC.md"), os.path.join("translations", "en.yaml")):
            text = read(rel)
            # no earlier release numbers of this app (pinned dependencies like 0.141.1 are fine)
            self.assertNotRegex(text, r"(?<![\d.=])1\.\d+\.\d+(?![\d.])", rel)
            self.assertNotRegex(text, r"(?i)version history|upgrading (from|to) \d", rel)

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
