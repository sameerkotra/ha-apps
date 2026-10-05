#!/usr/bin/env python3
"""Check every app's build files against the shared pattern (common/build/).

    python tools/check_build.py            report problems; exit 1 if there are any
    python tools/check_build.py --list     also list, per app, the pins and what its image would contain

Checks, per app (the folders in common/manifest.json):
- requirements.txt / requirements-dev.txt: every package that common/build/requirements-base.txt pins
  is pinned to exactly that version (extras like uvicorn[standard] allowed); fastapi, uvicorn and
  python-multipart are in every app's requirements.txt; no two apps pin a package differently.
- Dockerfile: a pinned python:3.x base image, requirements.txt copied and installed with pip, the app
  copied (`COPY app ./app` or `COPY . .`), a CMD.
- The start command turns uvicorn's proxy headers off: the ingress check trusts the TCP peer address,
  which must never be rewritten from X-Forwarded-For. A CMD that runs uvicorn passes --no-proxy-headers;
  a CMD that runs a Python file (Finance's bootstrap.py, Receipt's `-m app.main`) starts uvicorn there
  with --no-proxy-headers or proxy_headers=False. --proxy-headers / --forwarded-allow-ips never appear.
- An app that ships the shared sandbox_run.py (PDF tools as an unprivileged user) makes that user in its
  Dockerfile (useradd / adduser … pdfworker).
- The build context, simulated with the app's .dockerignore (Docker's rules: patterns relative to the
  app folder, `*` / `?` / `**`, `!` exceptions, the last matching line wins, an excluded folder
  excludes what is inside it): the image gets requirements.txt, app/main.py and every shared copy the
  manifest lists for app/common and app/static/common; .dockerignore never excludes them.

Docker itself isn't needed. Standard library only.
"""
import json
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BASE = os.path.join(ROOT, "common", "build", "requirements-base.txt")
EVERY_APP = ("fastapi", "uvicorn", "python-multipart")
REQ_RE = re.compile(r"^([A-Za-z0-9_.-]+)(\[[^\]]*\])?\s*(==\s*([^\s;#]+))?")


def norm(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def read_requirements(path: str) -> dict:
    """{package: version or None (unpinned)} from a requirements file (-r lines and comments skipped)."""
    out = {}
    if not os.path.exists(path):
        return out
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.split("#", 1)[0].strip()
            if not line or line.startswith("-"):
                continue
            m = REQ_RE.match(line)
            if m:
                out[norm(m.group(1))] = m.group(4)
    return out


def apps() -> list:
    with open(os.path.join(ROOT, "common", "manifest.json"), encoding="utf-8") as f:
        return list(json.load(f)["apps"])


# ---- .dockerignore ------------------------------------------------------------------------------------

def _pattern_re(pattern: str):
    out, i = "", 0
    while i < len(pattern):
        c = pattern[i]
        if pattern.startswith("**", i):
            i += 2
            if pattern.startswith("/", i):
                i += 1
                out += "(?:.*/)?"
            else:
                out += ".*"
            continue
        if c == "*":
            out += "[^/]*"
        elif c == "?":
            out += "[^/]"
        elif c == "[":
            j = pattern.find("]", i)
            if j == -1:
                out += re.escape(c)
            else:
                out += "[" + pattern[i + 1:j].replace("!", "^", 1) + "]"
                i = j
        else:
            out += re.escape(c)
        i += 1
    return re.compile(out + r"\Z")


def read_dockerignore(app_dir: str) -> list:
    """[(regex, exclude?)] in file order."""
    path = os.path.join(app_dir, ".dockerignore")
    rules = []
    if not os.path.exists(path):
        return rules
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            keep = line.startswith("!")
            p = os.path.normpath(line[1:].strip() if keep else line).replace(os.sep, "/").lstrip("/")
            rules.append((_pattern_re(p), not keep))
    return rules


def ignored(rel: str, rules: list) -> bool:
    """Docker's rule: the last pattern that matches the path or one of its parent folders decides."""
    parts = rel.split("/")
    prefixes = ["/".join(parts[:n]) for n in range(1, len(parts) + 1)]
    result = False
    for rx, exclude in rules:
        if any(rx.match(p) for p in prefixes):
            result = exclude
    return result


def context_files(app_dir: str) -> list:
    """Every file of the build context after .dockerignore (paths relative to the app folder)."""
    rules = read_dockerignore(app_dir)
    out = []
    for dirpath, dirnames, filenames in os.walk(app_dir):
        for n in filenames:
            rel = os.path.relpath(os.path.join(dirpath, n), app_dir).replace(os.sep, "/")
            if not ignored(rel, rules):
                out.append(rel)
    return sorted(out)


# ---- Dockerfile ---------------------------------------------------------------------------------------

def dockerfile(app_dir: str) -> list:
    """[(INSTRUCTION, arguments)] with continuation lines joined and comments dropped."""
    with open(os.path.join(app_dir, "Dockerfile"), encoding="utf-8") as f:
        text = f.read()
    text = re.sub(r"\\\n", " ", "\n".join(l for l in text.splitlines() if not l.lstrip().startswith("#")))
    out = []
    for line in text.splitlines():
        if line.strip():
            word, _, rest = line.strip().partition(" ")
            out.append((word.upper(), rest.strip()))
    return out


def image_files(app_dir: str) -> list:
    """Paths under WORKDIR that the COPY instructions put in the image (COPY <src>… <dest>)."""
    files = context_files(app_dir)
    out = set()
    for word, args in dockerfile(app_dir):
        if word != "COPY" or args.startswith("--from"):
            continue
        *srcs, dest = args.split()
        into_folder = dest.endswith("/") or dest in (".", "./") or len(srcs) > 1
        dest = dest.rstrip("/")
        dest = "" if dest in (".", "") else dest.removeprefix("./")
        for src in srcs:
            src = src.rstrip("/").removeprefix("./")
            for f in files:
                if src in (".", ""):
                    target = "/".join(p for p in (dest, f) if p)
                elif f == src:                    # a file: into the folder, or as `dest`
                    target = "/".join(p for p in (dest, os.path.basename(f)) if p) if into_folder else dest
                elif f.startswith(src + "/"):     # a folder: its contents go into `dest`
                    target = "/".join(p for p in (dest, f[len(src) + 1:]) if p)
                else:
                    continue
                out.add(target)
    return sorted(out)


def shared_copies(app: str) -> list:
    with open(os.path.join(ROOT, "common", "manifest.json"), encoding="utf-8") as f:
        entry = json.load(f)["apps"][app]
    out = []
    for group in ("python", "static"):
        for name in entry["files"].get(group, []):
            out.append(f"{entry['dest'][group]}/{name}")
    return out


# ---- the start command ---------------------------------------------------------------------------------

PROXY_OFF_RE = re.compile(r"--no-proxy-headers|proxy_headers\s*=\s*False")
PROXY_ON_RE = re.compile(r"--proxy-headers\b|--forwarded-allow-ips|forwarded_allow_ips|proxy_headers\s*=\s*True")


def start_command(app_dir: str) -> str:
    """The last CMD's arguments as one string (exec form joined with spaces)."""
    cmds = [a for w, a in dockerfile(app_dir) if w == "CMD"]
    if not cmds:
        return ""
    cmd = cmds[-1]
    try:
        parts = json.loads(cmd)
        if isinstance(parts, list):
            return " ".join(str(p) for p in parts)
    except ValueError:
        pass
    return cmd


def start_file(app_dir: str, cmd: str):
    """The Python file a CMD runs that starts uvicorn itself (`python3 x.py` or `python3 -m pkg.mod`)."""
    m = re.search(r"python3?\s+-m\s+([\w.]+)", cmd)
    if m and m.group(1) != "uvicorn":
        return os.path.join(app_dir, *m.group(1).split(".")) + ".py"
    m = re.search(r"python3?\s+([\w./-]+\.py)\b", cmd)
    if m:
        return os.path.join(app_dir, m.group(1))
    return None


def proxy_header_problems(app: str, app_dir: str) -> list:
    """[] when the app starts uvicorn with proxy headers off (see the module docstring)."""
    cmd = start_command(app_dir)
    if not cmd:
        return []                       # "no CMD" is reported on its own
    if re.search(r"(^|[\s/])uvicorn(\s|$)", cmd) and not start_file(app_dir, cmd):
        text, where = cmd, f"{app}/Dockerfile CMD"
    else:
        path = start_file(app_dir, cmd)
        if path is None or not os.path.exists(path):
            return [f"{app}/Dockerfile CMD: can't tell how uvicorn is started ({cmd})"]
        with open(path, encoding="utf-8") as f:
            text = f.read()
        where = f"{app}/{os.path.relpath(path, app_dir)}"
    problems = []
    if not PROXY_OFF_RE.search(text):
        problems.append(f"{where}: uvicorn isn't started with proxy headers off "
                        "(--no-proxy-headers / proxy_headers=False)")
    if PROXY_ON_RE.search(text):
        problems.append(f"{where}: proxy headers or forwarded-allow-ips turned on")
    return problems


# ---- checks -------------------------------------------------------------------------------------------

def check() -> list:
    problems = []
    base = read_requirements(BASE)
    seen: dict = {}
    for app in apps():
        app_dir = os.path.join(ROOT, app)
        runtime = read_requirements(os.path.join(app_dir, "requirements.txt"))
        dev = read_requirements(os.path.join(app_dir, "requirements-dev.txt"))
        for pkg in EVERY_APP:
            if pkg not in runtime:
                problems.append(f"{app}/requirements.txt: {pkg} is missing")
        for fname, reqs in (("requirements.txt", runtime), ("requirements-dev.txt", dev)):
            for pkg, ver in reqs.items():
                if ver is not None:
                    seen.setdefault(pkg, {}).setdefault(ver, []).append(f"{app}/{fname}")
                if pkg in base and ver != base[pkg]:
                    if ver is None and fname == "requirements-dev.txt":
                        continue          # a test-only tool left unpinned
                    problems.append(f"{app}/{fname}: {pkg}=={ver} but common/build/requirements-base.txt "
                                    f"pins {base[pkg]}")
        steps = dockerfile(app_dir)
        words = [w for w, _ in steps]
        froms = [a for w, a in steps if w == "FROM"]
        if not froms or not re.match(r"python:3\.\d+", froms[-1]):
            problems.append(f"{app}/Dockerfile: base image is not a pinned python:3.x ({froms})")
        if not any(w == "COPY" and a.split()[0] == "requirements.txt" for w, a in steps):
            problems.append(f"{app}/Dockerfile: requirements.txt isn't copied on its own")
        if not any(w == "RUN" and "pip install" in a and "-r requirements.txt" in a for w, a in steps):
            problems.append(f"{app}/Dockerfile: no pip install -r requirements.txt")
        if not any(w == "COPY" and a.split()[:2] in (["app", "./app"], [".", "."]) for w, a in steps):
            problems.append(f"{app}/Dockerfile: the app isn't copied (COPY app ./app or COPY . .)")
        if "CMD" not in words:
            problems.append(f"{app}/Dockerfile: no CMD")
        problems += proxy_header_problems(app, app_dir)
        if "app/common/sandbox_run.py" in shared_copies(app) and not any(
                w == "RUN" and re.search(r"\b(useradd|adduser)\b.*\bpdfworker\b", a) for w, a in steps):
            problems.append(f"{app}/Dockerfile: sandbox_run.py runs PDF tools as pdfworker, but the "
                            "Dockerfile doesn't make that user (useradd / adduser)")
        image = set(image_files(app_dir))
        for need in ["requirements.txt", "app/main.py"] + shared_copies(app):
            if need not in image:
                problems.append(f"{app}: {need} would not be in the image (Dockerfile COPY / .dockerignore)")
        rules = read_dockerignore(app_dir)
        for folder in ("app", "app/common", "app/static/common"):
            if ignored(folder, rules) or ignored(folder + "/x.py", rules):
                problems.append(f"{app}/.dockerignore excludes {folder}")
    for pkg, versions in sorted(seen.items()):
        if len(versions) > 1:
            problems.append(f"{pkg} is pinned to different versions: " +
                            "; ".join(f"{v} ({', '.join(w)})" for v, w in sorted(versions.items())))
    return problems


def pins_table() -> str:
    names = sorted({p for a in apps() for p in read_requirements(os.path.join(ROOT, a, "requirements.txt"))})
    rows = []
    for a in apps():
        r = read_requirements(os.path.join(ROOT, a, "requirements.txt"))
        rows.append(f"{a}: " + ", ".join(f"{p}=={r[p]}" for p in names if p in r))
    return "\n".join(rows)


def main(argv) -> int:
    if "--list" in argv:
        print(pins_table())
        for a in apps():
            files = image_files(os.path.join(ROOT, a))
            print(f"\n{a}: {len(files)} files in the image, e.g. " +
                  ", ".join(f for f in files if "/common/" in f)[:300])
    problems = check()
    for p in problems:
        print(p)
    print("build files: OK" if not problems else f"build files: {len(problems)} problem(s)")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
