"""Checks for the repository as a whole: the add-on repository file, each add-on's
packaging basics, the "unofficial, built with Claude" notice, and a scan that keeps
personal details out of every published text file. Standard library only."""
import codecs
import os
import re
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REPO_URL = "https://github.com/sameerkotra/ha-apps"
ADDONS = ("calorie_tracker", "family_tree", "household_chat", "household_todo", "household_vault", "splitpot")
VERSION = "2.0.0"
# Apps released again since 2.0.0, at their own version.
VERSIONS = {"calorie_tracker": "2.2.0", "family_tree": "2.3.0", "household_chat": "2.6.0", "household_todo": "2.4.1",
            "household_vault": "2.1.3", "splitpot": "2.4.1"}
# Finance Dashboard is published as it is, at its own version, with its own packaging tests
# (finance/tests/test_packaging.py); only the repository-wide basics are checked here.
FINANCE = "finance"
FINANCE_VERSION = "1.2.0"
# Household Arcade: a newer app, built the same way as the six above, at its own version.
ARCADE = "household_arcade"
ARCADE_VERSION = "1.9.0"
# Receipt Price Intelligence: brought in line with the others at 1.0.0, at its own version.
RECEIPTS = "receipt_price_intelligence"
RECEIPTS_VERSION = "1.2.0"
# Household Docs: the tenth app, built the same way, at its own version.
DOCS = "household_docs"
DOCS_VERSION = "1.2.0"
NEWER = ((ARCADE, ARCADE_VERSION), (RECEIPTS, RECEIPTS_VERSION), (DOCS, DOCS_VERSION))
# Paths that .gitignore keeps out of the repository.
IGNORED_DIRS = {"Claude outputs", "__pycache__", ".git", ".venv", "venv", ".pytest_cache"}
TEXT_EXT = {".py", ".js", ".css", ".html", ".md", ".yaml", ".yml", ".txt", ".json", ".sql", ".mermaid",
            ".cfg", ".toml", ".ini", ""}
# Stored rot13-encoded so this file doesn't spell the words out itself.
NEEDLES = re.compile(codecs.decode(
    r"fnzrre|xbgen|zvyirg|tznvy|192\.168\.1\.104|nzrevpn/qraire|\oqraire\o|pbybenqb|r-470|\okpry\o|cnexre|nheben",
    "rot13"), re.I)


# A table of US state names and their codes (address reading) names every state, the needles' too.
STATE_TABLE = re.compile(r'^\s*(?:"[a-z ]+": "[A-Z]{2}",\s*){4,}')


def allow_repo_handle(line):
    """The GitHub handle may appear only as part of the repository: its URL, the same URL encoded
    for the my.home-assistant.io badge link, repository.yaml's maintainer line and the LICENSE
    copyright line."""
    handle = REPO_URL.split("/")[3]
    line = line.replace(REPO_URL, "")
    line = line.replace(REPO_URL.replace(":", "%3A").replace("/", "%2F"), "")
    if line.strip() in (f"maintainer: {handle}", f"Copyright (c) 2026 {handle}"):
        return ""
    return line


def read(*parts):
    with open(os.path.join(ROOT, *parts), encoding="utf-8") as f:
        return f.read()


def published_files():
    for dirpath, dirnames, filenames in os.walk(ROOT):
        dirnames[:] = [d for d in dirnames if d not in IGNORED_DIRS]
        for name in filenames:
            if name.endswith((".pyc", ".db", ".db-wal", ".db-shm", ".tar")):
                continue
            yield os.path.join(dirpath, name)


class RepositoryTests(unittest.TestCase):
    def test_repository_yaml(self):
        text = read("repository.yaml")
        self.assertRegex(text, r"(?m)^name: \S")
        self.assertIn(f"url: {REPO_URL}", text)
        self.assertRegex(text, r"(?m)^maintainer: \S")

    def test_root_readme_notice(self):
        text = read("README.md")
        self.assertIn("Unofficial apps", text[:600])
        self.assertIn("Claude", text[:600])
        self.assertIn(REPO_URL, text)
        for slug in ADDONS + (FINANCE, ARCADE, RECEIPTS, DOCS):
            self.assertIn(f"]({slug})", text, slug)
        self.assertIn("](LICENSE)", text)
        self.assertIn("](SECURITY.md)", text)
        self.assertIn("Settings → Apps → Install app", text)       # Home Assistant 2026.2 names
        self.assertIn("Finance Dashboard is 64-bit only", text)

    def test_security_policy(self):
        text = read("SECURITY.md")
        self.assertIn("Report a", text)
        self.assertIn("vulnerability", text)
        self.assertIn("Household Vault is experimental", text)

    def test_gitattributes_keeps_lf_line_endings(self):
        lines = [l.split() for l in read(".gitattributes").splitlines() if l.strip() and not l.startswith("#")]
        self.assertIn(["*", "text=auto", "eol=lf"], lines)
        self.assertIn(["*.png", "binary"], lines)

    def test_changelogs_start_at_the_release(self):
        for slug, version in [(s, VERSIONS.get(s, VERSION)) for s in ADDONS] + [(FINANCE, FINANCE_VERSION), *NEWER]:
            with self.subTest(slug):
                log = read(slug, "CHANGELOG.md")
                self.assertTrue(log.startswith("# Changelog\n"))
                self.assertEqual(re.findall(r"(?m)^## (\S+)", log)[0], version)   # newest first

    def test_shared_files_are_identical(self):
        """Files shared between apps live in common/ and are copied into each app by
        tools/sync_common.py: every copy must match its source (and the manifest)."""
        import subprocess, sys
        r = subprocess.run([sys.executable, os.path.join(ROOT, "tools", "sync_common.py"), "--check"],
                           capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)

    def test_user_facing_text_says_app_not_add_on(self):
        """Home Assistant 2026.2 renamed add-ons to apps; what people read says "app"."""
        for slug in ADDONS + (FINANCE, ARCADE, RECEIPTS, DOCS):
            for name in ("README.md", "DOCS.md", "CHANGELOG.md", os.path.join("translations", "en.yaml")):
                with self.subTest(f"{slug}/{name}"):
                    text = read(slug, name).replace("ha-apps", "")
                    self.assertNotRegex(text, r"(?i)add-?ons?\b", f"{slug}/{name}")

    def test_license(self):
        text = read("LICENSE")
        self.assertTrue(text.startswith("MIT License"))
        self.assertIn("Copyright (c) 2026 ", text)

    def test_gitignore_keeps_private_folders_out(self):
        lines = read(".gitignore").splitlines()
        for entry in ("Claude outputs/", "__pycache__/", "*.db"):
            self.assertIn(entry, lines)
        self.assertNotIn("finance/", lines)  # Finance Dashboard is published too
        self.assertNotIn("data/", lines)    # would hide household_vault/app/data

    def test_each_addon(self):
        for slug, version in [(s, VERSIONS.get(s, VERSION)) for s in ADDONS] + list(NEWER):
            with self.subTest(slug):
                config = read(slug, "config.yaml")
                self.assertIn(f'version: "{version}"', config)
                self.assertIn(f"slug: {slug}", config)
                self.assertIn(f"url: {REPO_URL}", config)
                self.assertNotRegex(config, r"(?m)^ports:")
                self.assertRegex(config, r"(?m)^panel_admin: false")
                self.assertTrue(os.path.isfile(os.path.join(ROOT, slug, "CHANGELOG.md")))
                self.assertFalse(os.path.exists(os.path.join(ROOT, slug, "data model")))
                self.assertTrue(os.path.isfile(os.path.join(ROOT, slug, "spec", "SPEC.md")))
                for name in ("README.md", "DOCS.md", "Dockerfile", "icon.png", "logo.png",
                             ".dockerignore", os.path.join("translations", "en.yaml")):
                    self.assertTrue(os.path.isfile(os.path.join(ROOT, slug, name)), f"{slug}/{name}")
                readme = read(slug, "README.md")
                self.assertIn("Unofficial app.", readme[:600])
                self.assertIn("Claude", readme[:600])

    def test_finance(self):
        config = read(FINANCE, "config.yaml")
        self.assertIn(f'version: "{FINANCE_VERSION}"', config)
        self.assertIn('slug: "finance"', config)
        self.assertIn(f"url: {REPO_URL}", config)
        for path in published_files():          # no links to Finance's former repository
            if os.path.relpath(path, ROOT).split(os.sep)[0] == FINANCE and path.endswith((".md", ".yaml", ".py", ".html")):
                with open(path, encoding="utf-8", errors="ignore") as f:
                    self.assertNotIn("finance-dashboard", f.read(), path)
        self.assertNotRegex(config, r"(?m)^ports:")
        self.assertRegex(config, r"(?m)^panel_admin: false")
        for name in ("README.md", "DOCS.md", "Dockerfile", "icon.png", "logo.png",
                     ".dockerignore", os.path.join("translations", "en.yaml")):
            self.assertTrue(os.path.isfile(os.path.join(ROOT, FINANCE, name)), f"{FINANCE}/{name}")
        readme = read(FINANCE, "README.md")
        self.assertIn("Unofficial app.", readme[:600])
        self.assertIn("Claude", readme[:600])
        self.assertIn("64-bit only", readme[:1200])
        self.assertEqual(re.findall(r"(?m)^  - (\w+)$", config.split("arch:", 1)[1].split("\n\n")[0])[:2],
                         ["amd64", "aarch64"])

    def test_arcade_is_under_development(self):
        self.assertRegex(read(ARCADE, "config.yaml"), r"(?m)^stage: experimental")
        self.assertIn("Under development", read(ARCADE, "README.md"))
        self.assertIn("[Household Arcade](household_arcade) | 🤖 Optional | **Under development.**", read("README.md"))

    def test_docs_is_a_normal_release_and_says_files_are_plain(self):
        self.assertNotRegex(read(DOCS, "config.yaml"), r"(?m)^stage:")
        row = [l for l in read("README.md").splitlines() if l.startswith("| [Household Docs](household_docs)")]
        self.assertEqual(len(row), 1)
        self.assertNotIn("Under development", row[0])
        self.assertIn("plain files", row[0])
        self.assertIn("/share", row[0])

    def test_vault_is_experimental(self):
        self.assertRegex(read("household_vault", "config.yaml"), r"(?m)^stage: experimental")

    def test_no_personal_details(self):
        hits = []
        for path in published_files():
            # Finance Dashboard is published as it is; its own test checks it for personal details.
            if os.path.relpath(path, ROOT).split(os.sep)[0] == FINANCE:
                continue
            if os.path.splitext(path)[1].lower() not in TEXT_EXT:
                continue
            with open(path, encoding="utf-8", errors="ignore") as f:
                for n, line in enumerate(f, 1):
                    if NEEDLES.search(allow_repo_handle(line)) and not STATE_TABLE.match(line):
                        hits.append(f"{os.path.relpath(path, ROOT)}:{n}")
        self.assertEqual(hits, [])


if __name__ == "__main__":
    unittest.main()
