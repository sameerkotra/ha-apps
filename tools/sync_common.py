#!/usr/bin/env python3
"""Copy the shared code in common/ into every app that uses it.

Home Assistant builds each app from its own folder only, so an app can't import
from common/ at run time: each app carries copies, committed with the app. This
script makes those copies, and is the only way they should change.

    python tools/sync_common.py            copy common/ into the apps; lists what changed
    python tools/sync_common.py --check    change nothing; exit 1 if any copy differs or is missing
    python tools/sync_common.py --adopt household_todo/app/common/ha_client.py
                                           take an edited copy back into common/ (header removed),
                                           then copy it to every app

Which app gets which file, and where, is in common/manifest.json. Each copy gets
one header line naming its source and the SHA-256 of the body, so an app folder
on its own can tell (tests/test_shared_copies.py) when a copy was edited.

Standard library only.
"""
import argparse
import hashlib
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
COMMON = os.path.join(ROOT, "common")
MARK = "Shared file: edit common/"

COMMENT = {".py": ("# ", ""), ".js": ("// ", ""), ".css": ("/* ", " */")}


def load_manifest():
    with open(os.path.join(COMMON, "manifest.json"), encoding="utf-8") as f:
        return json.load(f)


def body_hash(body: bytes) -> str:
    return hashlib.sha256(body).hexdigest()


def header_for(src_rel: str, body: bytes) -> bytes:
    ext = os.path.splitext(src_rel)[1]
    start, end = COMMENT[ext]
    text = (f"{start}{MARK}{src_rel[len('common/'):]} and run tools/sync_common.py; "
            f"don't edit this copy. sha256={body_hash(body)}{end}\n")
    return text.encode("utf-8")


def split_header(data: bytes):
    """(src_rel, sha, body) of a copy, or None when it has no valid header line."""
    nl = data.find(b"\n")
    if nl < 0:
        return None
    first = data[:nl].decode("utf-8", "replace")
    if MARK not in first or "sha256=" not in first:
        return None
    src = "common/" + first.split(MARK, 1)[1].split(" and run", 1)[0]
    sha = first.split("sha256=", 1)[1][:64]
    return src, sha, data[nl + 1:]


def planned_copies(manifest):
    """[(app, source path relative to the repo, destination relative to the repo)]"""
    out = []
    for app, spec in sorted(manifest["apps"].items()):
        for group, files in spec["files"].items():
            dest_dir = spec["dest"][group]
            for name in files:
                src = f"common/{group}/{name}"
                dest = f"{app}/{dest_dir}/{name}"
                out.append((app, src, dest))
    return out


def read(path):
    with open(os.path.join(ROOT, path), "rb") as f:
        return f.read()


def expected_copy(src):
    body = read(src)
    if b"\r\n" in body:
        sys.exit(f"{src} has CRLF line endings; shared files must use LF")
    return header_for(src, body) + body


def stale_copies(manifest):
    """Copies in app folders that carry a header but are no longer in the manifest."""
    planned = {d for _, _, d in planned_copies(manifest)}
    out = []
    for app in manifest["apps"]:
        for dirpath, dirnames, filenames in os.walk(os.path.join(ROOT, app)):
            dirnames[:] = [d for d in dirnames if d not in ("__pycache__", "node_modules")]
            for n in filenames:
                if os.path.splitext(n)[1] not in COMMENT:
                    continue
                rel = os.path.relpath(os.path.join(dirpath, n), ROOT).replace(os.sep, "/")
                with open(os.path.join(dirpath, n), "rb") as f:
                    head = f.read(400)
                if MARK.encode() in head.split(b"\n", 1)[0] and rel not in planned:
                    out.append(rel)
    return out


def check(manifest):
    problems = []
    for app, src, dest in planned_copies(manifest):
        if not os.path.exists(os.path.join(ROOT, src)):
            problems.append(f"{src}: listed for {app} but missing from common/")
            continue
        if not os.path.exists(os.path.join(ROOT, dest)):
            problems.append(f"{dest}: missing (run tools/sync_common.py)")
        elif read(dest) != expected_copy(src):
            problems.append(f"{dest}: differs from {src}")
    for rel in stale_copies(manifest):
        problems.append(f"{rel}: a shared copy no longer listed in common/manifest.json")
    return problems


def sync(manifest):
    changed_apps, written = set(), []
    for app, src, dest in planned_copies(manifest):
        want = expected_copy(src)
        path = os.path.join(ROOT, dest)
        if os.path.exists(path) and read(dest) == want:
            continue
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "wb") as f:
            f.write(want)
        written.append(dest)
        changed_apps.add(app)
    for rel in stale_copies(manifest):
        os.remove(os.path.join(ROOT, rel))
        written.append(f"{rel} (removed: no longer in the manifest)")
        changed_apps.add(rel.split("/", 1)[0])
    return written, sorted(changed_apps)


def adopt(manifest, copy_rel):
    data = read(copy_rel)
    parts = split_header(data)
    if not parts:
        sys.exit(f"{copy_rel} has no shared-file header")
    src, _sha, body = parts
    with open(os.path.join(ROOT, src), "wb") as f:
        f.write(body)
    print(f"{copy_rel} -> {src}")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--check", action="store_true", help="change nothing; exit 1 on any difference")
    ap.add_argument("--adopt", metavar="COPY", help="take an edited copy back into common/, then sync")
    args = ap.parse_args()
    manifest = load_manifest()
    if args.check:
        problems = check(manifest)
        for p in problems:
            print(p)
        print("shared copies: " + ("OK" if not problems else f"{len(problems)} problem(s)"))
        sys.exit(1 if problems else 0)
    if args.adopt:
        adopt(manifest, args.adopt.replace(os.sep, "/"))
    written, apps = sync(manifest)
    for w in written:
        print("wrote", w)
    if apps:
        print("\nApps that changed (bump their version and add a CHANGELOG line):")
        for a in apps:
            print("  -", a)
    else:
        print("Everything already up to date.")


if __name__ == "__main__":
    main()
