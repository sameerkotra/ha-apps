#!/usr/bin/env python3
"""Run every app's tests (or some of them) with one command, and say which passed.

    python3 tools/run_tests.py                 # everything: each app, then the repository-wide checks
    python3 tools/run_tests.py household_docs family_tree
    python3 tools/run_tests.py --jobs 4        # four suites at a time (each app has its own scratch folders)
    python3 tools/run_tests.py --list          # what it would run
    python3 tools/run_tests.py --failfast -v   # stop a suite at its first failure; show every suite's output

Each app's suite runs from the app's own folder, the way its spec says: `python3 -m unittest discover -s tests`,
or `python3 -m pytest` for an app with a pytest.ini (Finance Dashboard). The repository-wide checks are
`python3 -m unittest discover -s tests` from the repository's top folder. The Home Assistant integration tests
(integration_tests/, which need pytest-homeassistant-custom-component) run only when asked for by name:
`python3 tools/run_tests.py integration_tests`.

Install each app's requirements-dev.txt first (CI does: .github/workflows/tests.yml). Node.js is needed for the
apps whose browser code has tests (they're skipped without it). Exit status: 0 when everything passed.
"""
import argparse
import os
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REPO = "repository"                      # the repository-wide checks (tests/ at the top)
INTEGRATION = "integration_tests"


def apps() -> list[str]:
    """Every folder at the top with a tests/ folder and a requirements-dev.txt, alphabetically."""
    out = []
    for name in sorted(os.listdir(ROOT)):
        d = os.path.join(ROOT, name)
        if os.path.isdir(os.path.join(d, "tests")) and os.path.isfile(os.path.join(d, "requirements-dev.txt")):
            out.append(name)
    return out


def command(name: str) -> tuple[list[str], str]:
    """(argv, folder to run it in) for one suite."""
    if name == REPO:
        return [sys.executable, "-m", "unittest", "discover", "-s", "tests"], ROOT
    if name == INTEGRATION:
        return [sys.executable, "-m", "pytest"], os.path.join(ROOT, INTEGRATION)
    folder = os.path.join(ROOT, name)
    if os.path.isfile(os.path.join(folder, "pytest.ini")):
        return [sys.executable, "-m", "pytest"], folder
    return [sys.executable, "-m", "unittest", "discover", "-s", "tests"], folder


def summary_line(output: str) -> str:
    """The last line that says how it went ("Ran 120 tests …" + OK/FAILED, or pytest's "5 passed in 2s")."""
    lines = [ln.strip() for ln in output.splitlines() if ln.strip()]
    ran = next((ln for ln in reversed(lines) if ln.startswith("Ran ")), None)
    verdict = next((ln for ln in reversed(lines) if ln.startswith(("OK", "FAILED"))), None)
    if ran:
        return f"{ran}: {verdict or '?'}"
    return next((ln.strip("= ") for ln in reversed(lines) if " passed" in ln or " failed" in ln or " error" in ln), "")


def run(name: str, failfast: bool) -> dict:
    argv, cwd = command(name)
    if failfast:
        argv = argv + (["-x"] if "pytest" in argv else ["-f"])
    env = dict(os.environ, PYTHONDONTWRITEBYTECODE="1")
    t0 = time.monotonic()
    p = subprocess.run(argv, cwd=cwd, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    return {"name": name, "ok": p.returncode == 0, "code": p.returncode, "seconds": time.monotonic() - t0,
            "output": p.stdout, "summary": summary_line(p.stdout)}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Run the apps' tests.")
    ap.add_argument("suites", nargs="*", help=f"app folders (default: every app, then '{REPO}')")
    ap.add_argument("-j", "--jobs", type=int, default=1, help="suites to run at the same time (default 1)")
    ap.add_argument("-f", "--failfast", action="store_true", help="stop each suite at its first failure")
    ap.add_argument("-v", "--verbose", action="store_true", help="print every suite's output, not only failing ones")
    ap.add_argument("--list", action="store_true", help="list the suites and their commands, run nothing")
    a = ap.parse_args(argv)
    known = apps()
    suites = a.suites or known + [REPO]
    unknown = [s for s in suites if s not in known + [REPO, INTEGRATION]]
    if unknown:
        ap.error(f"no such suite: {', '.join(unknown)} (known: {', '.join(known + [REPO, INTEGRATION])})")
    if a.list:
        for s in suites:
            argv_, cwd = command(s)
            print(f"{s:28} (cd {os.path.relpath(cwd, ROOT)} && {' '.join(['python3'] + argv_[1:])})")
        return 0
    results = []
    with ThreadPoolExecutor(max_workers=max(1, a.jobs)) as pool:
        for r in pool.map(lambda s: run(s, a.failfast), suites):
            mark = "ok  " if r["ok"] else "FAIL"
            print(f"{mark} {r['name']:28} {r['seconds']:6.1f}s  {r['summary']}", flush=True)
            if a.verbose or not r["ok"]:
                print("\n".join("     | " + ln for ln in r["output"].rstrip().splitlines()[-60:]), flush=True)
            results.append(r)
    failed = [r["name"] for r in results if not r["ok"]]
    total = sum(r["seconds"] for r in results)
    print(f"\n{len(results) - len(failed)} of {len(results)} suites passed ({total:.0f}s of test time)."
          + (f" Failed: {', '.join(failed)}." if failed else ""))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
