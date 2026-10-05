"""Packaging for other installs (SPEC.md section 24).
The checks every app shares are in common_tests/packaging_core.py."""
import os

import pytest

from common_tests import packaging_core as pk

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))     # the app folder
REPO = os.path.dirname(HERE)          # the app repository (or the local "ha addon" folder)
yaml = pytest.importorskip("yaml")


def _load(path):
    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f)


def test_repository_and_addon_files():
    if os.path.exists(os.path.join(REPO, "repository.yaml")):      # only in the published repository
        repo = _load(os.path.join(REPO, "repository.yaml"))
        assert repo["name"] and repo["url"].startswith("https://") and "maintainer" in repo
    assert os.path.getsize(os.path.join(HERE, "spec", "SPEC.md")) > 10000
    cfg = _load(os.path.join(HERE, "config.yaml"))
    assert cfg["ingress"] is True and cfg["panel_admin"] is False and "ports" not in cfg
    assert set(cfg["options"]) == {"admin_users", "trusted_client_ips"}
    assert cfg["options"]["admin_users"] == [] and cfg["options"]["trusted_client_ips"] == []
    assert set(cfg["schema"]) == set(cfg["options"])
    pk.check_config_basics(HERE, str(cfg["version"]), options=("admin_users", "trusted_client_ips"))
    tr = _load(os.path.join(HERE, "translations", "en.yaml"))
    assert set(tr["configuration"]) == set(cfg["options"])
    for name in ("README.md", "DOCS.md", "Dockerfile"):
        assert os.path.getsize(os.path.join(HERE, name)) > 200, name


def test_changelog_starts_at_the_release_version():
    path = os.path.join(HERE, "CHANGELOG.md")
    assert os.path.exists(path)
    with open(path, encoding="utf-8") as f:
        text = f.read()
    assert text.startswith("# Changelog")
    headings = [line[3:].strip() for line in text.splitlines() if line.startswith("## ")]
    # newest first: the top entry is this version (later releases add theirs above it)
    assert headings and headings[0] == str(_load(os.path.join(HERE, "config.yaml"))["version"])
    pk.check_changelog(HERE, headings[0])


def test_64_bit_only():
    assert _load(os.path.join(HERE, "config.yaml"))["arch"] == ["amd64", "aarch64"]
    with open(os.path.join(HERE, "README.md"), encoding="utf-8") as f:
        assert "64-bit only" in f.read()


def test_icon_and_logo_sizes():
    from PIL import Image
    assert Image.open(os.path.join(HERE, "icon.png")).size == (128, 128)
    assert Image.open(os.path.join(HERE, "logo.png")).size == (250, 100)
    pk.check_icons(HERE)


def test_nothing_personal_in_the_shipped_files():
    # every text file the app ships (and, in the published repository, the root files too)
    roots = [HERE] + ([REPO] if os.path.exists(os.path.join(REPO, "repository.yaml")) else [])
    files = []
    for root in roots:
        for dirpath, dirnames, names in os.walk(root):
            dirnames[:] = [d for d in dirnames if d not in (".git", "__pycache__", ".pytest_cache", "tests")]
            if root == REPO and dirpath == REPO:
                dirnames[:] = [d for d in dirnames if d in ("docs",)]
            files += [os.path.join(dirpath, n) for n in names]
    for path in files:
        rel = os.path.relpath(path, REPO)
        if not rel.endswith((".py", ".html", ".md", ".yaml", ".yml", ".js", ".css", ".sql", ".txt")):
            continue
        with open(path, encoding="utf-8", errors="replace") as f:
            text = f.read().lower()
        for needle in ("sameer.kotra", "192.168.1.104", "@gmail.com", "your-name"):
            assert needle not in text, f"{needle} in {rel}"


def test_first_run_without_admins_says_what_to_do(make_env):
    env = make_env(ADMIN_USERS="")
    page = env.get("accounts").text
    assert "No admin yet" in page and "<strong>Tester</strong>" in page and "admin_users" in page
    words = " ".join(page.split())
    assert "on the app's Configuration tab" in words and "add-on" not in words.lower()
    assert env.get("settings").status_code == 403
    env2 = make_env()
    assert "No admin yet" not in env2.get("accounts").text
