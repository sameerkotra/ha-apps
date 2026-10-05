"""Reusable checks for an app's tests/test_packaging.py (shared: common/tests/packaging_core.py).

Every household app is published the same way, so most packaging rules are the same: config.yaml
basics, the CHANGELOG's top heading, `?v=` cache-busting strings, icon and logo sizes,
.dockerignore entries and a scan of every text file for personal details. Each app's
test_packaging.py calls these with its own values and keeps its app-specific checks itself.

Standard library only (no PyYAML or Pillow). The check_* functions raise AssertionError with a
message, so they work from unittest test methods and from plain pytest functions alike; the find_*
functions return a list of problems for the caller to assert empty.

    from common_tests import packaging_core as pk
    pk.check_config_basics(APP_DIR, VERSION)
    pk.check_changelog(APP_DIR, VERSION)
    hits, checked = pk.personal_details(APP_DIR)
"""
import ipaddress
import os
import re
import struct

REPO_URL = "https://github.com/sameerkotra/ha-apps"

# Text files the personal-details scan reads (by extension, plus these exact names).
TEXT_EXT = (".py", ".js", ".css", ".html", ".md", ".yaml", ".yml", ".txt", ".json", ".cfg", ".toml", ".ini", ".sh")
TEXT_NAMES = ("Dockerfile", ".dockerignore", ".gitignore")
SKIP_DIRS = ("__pycache__", ".git")

# Built from pieces so this file doesn't match a plain grep for them itself.
NEEDLES = ("sa" + "meer", "ko" + "tra", "mil" + "vet", "g" + "mail", "192.168.1." + "104",
           "america/" + "den" + "ver", "den" + "ver", "colo" + "rado", "e-" + "470", "x" + "cel",
           "par" + "ker", "au" + "rora")
EMAIL = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9-]+\.[A-Za-z]{2,}")
LAN = re.compile(r"\b(?:192\.168|10\.\d{1,3}|172\.(?:1[6-9]|2\d|3[01]))\.\d{1,3}\.\d{1,3}\b")
ALLOWED_IPS = frozenset({"192.168.1.10", "172.30.32.2", "10.0.0.5"})   # an example, Supervisor's proxy, a test client
WINDOWS_PATH = re.compile(r"[A-Za-z]:\\\\?Users", re.I)

# What every app's .dockerignore keeps out of the image.
DOCKERIGNORE = ("tests", "spec", "*.md", "!README.md", "icon.png", "logo.png", "translations",
                "requirements-dev.txt", "__pycache__")

CACHE_BUST = re.compile(r"\?v=([^\"'&>\s]+)")
CACHE_BUST_IN_CODE = re.compile(r"\?v=([0-9][^\"'&>\s`]*)")     # in .js/.css: only values starting with a digit


# --------------------------------------------------------------------------- reading

def read(root, *parts):
    with open(os.path.join(root, *parts), encoding="utf-8") as f:
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
            item = line[2:].strip()
            if isinstance(parent, list):
                if re.match(r"^[A-Za-z_][\w-]*:(\s|$)", item):    # "- key: value": a mapping in the list
                    child = {}
                    parent.append(child)
                    stack.append((indent, child))
                    parent, indent, line = child, indent + 2, item
                else:
                    parent.append(item.strip('"'))
                    continue
            else:
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


def config(root):
    """config.yaml as a dict (simple_yaml)."""
    return simple_yaml(read(root, "config.yaml"))


def top_level(text):
    """Top-level `key: value` pairs of a YAML file, as raw strings (quotes removed)."""
    return {m.group(1): m.group(2).strip().strip('"')
            for m in re.finditer(r"^([A-Za-z_][A-Za-z0-9_]*):[ \t]*(.*)$", text, re.M)}


def section_keys(text, section):
    """[(key, rest of line)] directly under a top-level YAML section (two-space indent), in order."""
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


def png_size(path):
    """(width, height) from a PNG header."""
    with open(path, "rb") as f:
        head = f.read(24)
    assert head[:8] == b"\x89PNG\r\n\x1a\n", f"{path} isn't a PNG"
    return struct.unpack(">II", head[16:24])


def _eq(got, want, what):
    assert got == want, f"{what}: {got!r} != {want!r}"


# --------------------------------------------------------------------------- checks

def check_config_basics(root, version, *, options=("admin_users",), url=REPO_URL):
    """config.yaml: the version, the repository url, ingress on, panel_admin off, no ports, apparmor on and
    hassio_api / auth_api / docker_api / full_access written out as false, the option
    names (empty defaults) equal to the schema's and to translations/en.yaml's configuration keys, each
    translated with a name and a description. Returns the parsed config for app-specific checks."""
    cfg = config(root)
    _eq(cfg.get("version"), version, "config.yaml version")
    _eq(cfg.get("url"), url, "config.yaml url")
    assert cfg.get("ingress") is True, "config.yaml: ingress must be true"
    assert cfg.get("panel_admin") is False, "config.yaml: panel_admin must be false"
    for key in ("ports", "ports_description"):
        assert key not in cfg, f"config.yaml must not have {key} (ingress is the only way in)"
    # the hardening lines, spelled out even where they are the Supervisor's defaults
    assert cfg.get("apparmor") is True, "config.yaml: apparmor must be true"
    for key in ("hassio_api", "auth_api", "docker_api", "full_access"):
        assert cfg.get(key) is False, f"config.yaml: {key} must be false (and written out)"
    _eq(set(cfg.get("options") or {}), set(options), "config.yaml options")
    _eq(set(cfg.get("schema") or {}), set(options), "config.yaml schema")
    for key, default in cfg["options"].items():
        assert default in ([], ""), f"option {key} must default to empty, not {default!r}"
    tr = simple_yaml(read(root, "translations", "en.yaml"))
    _eq(set(tr.get("configuration") or {}), set(options), "translations/en.yaml configuration")
    for key, entry in tr["configuration"].items():
        assert isinstance(entry, dict) and entry.get("name") and entry.get("description"), \
            f"translations/en.yaml: {key} needs a name and a description"
    return cfg


def check_changelog(root, version):
    """CHANGELOG.md starts "# Changelog" and its top (newest) heading is the config.yaml version, which is
    `version`. Returns every "## x" heading, newest first."""
    log = read(root, "CHANGELOG.md")
    assert log.startswith("# Changelog\n"), "CHANGELOG.md must start with '# Changelog'"
    headings = re.findall(r"(?m)^## (.+?)\s*$", log)
    assert headings, "CHANGELOG.md has no version heading"
    _eq(headings[0], str(config(root)["version"]), "CHANGELOG.md top heading vs config.yaml version")
    _eq(headings[0], version, "CHANGELOG.md top heading")
    return headings


def cache_busting(html):
    """Every ?v=... value in a page."""
    return CACHE_BUST.findall(html)


def check_cache_busting(root, version, *, page=("app", "static", "index.html"), count=None, at_least=None,
                        static_files=None):
    """Every ?v= in the page equals `version` (`count`: exactly that many; `at_least`: no fewer).
    static_files: also check the ?v= values inside the other .js/.css/.html files of app/static —
    "top" (that folder only) or "all" (with subfolders). Returns the page's values."""
    found = cache_busting(read(root, *page))
    if count is not None:
        _eq(len(found), count, "number of ?v= strings in " + "/".join(page))
    if at_least is not None:
        assert len(found) >= at_least, f"only {len(found)} ?v= strings in {'/'.join(page)} (want {at_least}+)"
    _eq(set(found), {version}, "?v= versions in " + "/".join(page))
    if static_files:
        static = os.path.join(root, "app", "static")
        for dirpath, dirnames, names in os.walk(static):
            if static_files == "top":
                dirnames[:] = []
            for name in names:
                if name.endswith((".js", ".css", ".html")) and not (dirpath == static and name == page[-1]):
                    with open(os.path.join(dirpath, name), encoding="utf-8") as f:
                        for v in CACHE_BUST_IN_CODE.findall(f.read()):
                            _eq(v, version, f"?v= in {os.path.relpath(os.path.join(dirpath, name), root)}")
    return found


def check_icons(root):
    """icon.png is 128x128 and logo.png 250x100."""
    _eq(png_size(os.path.join(root, "icon.png")), (128, 128), "icon.png size")
    _eq(png_size(os.path.join(root, "logo.png")), (250, 100), "logo.png size")


def check_dockerignore(root, entries=DOCKERIGNORE, extra=()):
    lines = read(root, ".dockerignore").split()
    for entry in tuple(entries) + tuple(extra):
        assert entry in lines, f".dockerignore must list {entry}"


def check_files(root, names=("README.md", "DOCS.md", "Dockerfile"), min_size=200):
    """The usual files exist and aren't stubs; spec/SPEC.md exists; no leftover "data model" folder."""
    for name in names:
        assert os.path.getsize(os.path.join(root, name)) > min_size, f"{name} is missing or too short"
    assert os.path.isfile(os.path.join(root, "spec", "SPEC.md")), "spec/SPEC.md is missing"
    assert not os.path.exists(os.path.join(root, "data model")), "leftover 'data model' folder"


# --------------------------------------------------------------------------- personal details

def text_files(root, *, exts=TEXT_EXT, names=TEXT_NAMES, suffixes=None, dotfiles=False, skip_dirs=SKIP_DIRS,
               skip=()):
    """(relative path, path) of every text file under root, sorted. A file counts when its name ends with
    one of `exts` or is one of `names` -- or, when `suffixes` is given instead, when its lower-cased
    extension is in `suffixes` ("" = no extension); `dotfiles`: also every name starting with a dot.
    `skip`: paths to leave out."""
    skip = {os.path.abspath(p) for p in skip}
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(d for d in dirnames if d not in skip_dirs)
        for n in sorted(filenames):
            path = os.path.join(dirpath, n)
            if os.path.abspath(path) in skip:
                continue
            if suffixes is not None:
                wanted = os.path.splitext(n)[1].lower() in suffixes
            else:
                wanted = n.endswith(tuple(exts)) or n in names
            if wanted or (dotfiles and n.startswith(".")):
                yield os.path.relpath(path, root), path


def find_needles(text, needles=NEEDLES, *, whole_words=True):
    """The needles found in text (case-insensitive). whole_words: only where the needle isn't part of a
    longer word."""
    low = text.lower()
    if whole_words:
        return [n for n in needles if re.search(r"(?<![a-z])" + re.escape(n.lower()) + r"(?![a-z])", low)]
    return [n for n in needles if n.lower() in low]


def find_lan_addresses(text, allowed=ALLOWED_IPS, pattern=LAN):
    return [ip for ip in pattern.findall(text) if ip not in allowed]


def find_private_ipv4(text, allowed=ALLOWED_IPS):
    """Any private (RFC 1918 / link-local) IPv4 address not in `allowed`."""
    out = []
    for a in re.findall(r"(?<![\d.])\d{1,3}(?:\.\d{1,3}){3}(?![\d.])", text):
        try:
            ip = ipaddress.ip_address(a)
        except ValueError:
            continue
        if ip.is_private and not ip.is_loopback and not ip.is_unspecified and a not in allowed:
            out.append(a)
    return out


def find_emails(text, allowed=None, pattern=EMAIL):
    """E-mail addresses in text; `allowed`: a regex the documentation-only ones match."""
    return [a for a in pattern.findall(text) if not (allowed and re.search(allowed, a))]


def personal_details(root, *, needles=NEEDLES, whole_words=True, emails=True, allowed_emails=None,
                     allowed_ips=ALLOWED_IPS, windows_paths=True, files=None):
    """The standard scan: every text file (minus the repository URL, the one allowed mention of the
    owner's handle) for the needles, e-mail addresses, LAN addresses other than `allowed_ips` and
    Windows user paths. Returns (hits, number of files checked); assert hits == []."""
    hits, checked = [], 0
    for rel, path in (files if files is not None else text_files(root)):
        with open(path, encoding="utf-8", errors="replace") as f:
            text = f.read().replace(REPO_URL, "")
        hits += [f"{rel}: {n!r}" for n in find_needles(text, needles, whole_words=whole_words)]
        if emails:
            hits += [f"{rel}: an e-mail address" for _ in find_emails(text, allowed_emails)][:1]
        if windows_paths and WINDOWS_PATH.search(text):
            hits.append(f"{rel}: a Windows user path")
        hits += [f"{rel}: {ip}" for ip in find_lan_addresses(text, allowed_ips)]
        checked += 1
    return hits, checked
