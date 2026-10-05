"""Regular-expression search in a separate worker process (SPEC §10.4, risk 7: a slow expression can't stall the app).

The app never runs a person's regular expression itself. `run()` starts this file as its own Python process
(`python -I -S regex_worker.py`: isolated, standard library only), sends the pattern and the candidates — names, or
files the app has already realpath-checked and the person may open — one per line on stdin, and reads matches as
they come back on stdout. After `REGEX_SECONDS` (5 s) the worker is killed; the matches found so far come back with
`stopped: True` ("Search stopped after 5 s — narrow it down"). It is also stopped as soon as enough matches are in.
One regex search per person at a time (`busy()`), and at most MAX_WORKERS at once for the whole app.

Protocol (JSON lines):
  in   {"pattern": "...", "mode": "name" | "content", "limit": bytes}   then  [id, name]  or  [id, path]  …
  out  {"id": ..., "s": start, "e": end}                     (name: the match's span in the name)
       {"id": ..., "line": n, "snip": "…⁅match⁆…"}          (content: the first matching line, 1-based)
"""
import json
import os
import re
import subprocess
import sys
import threading
import time

REGEX_SECONDS = 5.0
MAX_WORKERS = 3
SNIP = 80
_busy: set = set()
_busy_lock = threading.Lock()
_slots = threading.BoundedSemaphore(MAX_WORKERS)


class Busy(Exception):
    """This person already has a regex search running."""


class Result:
    def __init__(self):
        self.matches: list[dict] = []
        self.stopped = False          # killed at the time limit
        self.ms = 0
        self.error: str | None = None


def busy(user_id: str):
    """A context manager: one regex search per person at a time (Busy when one is running)."""
    class _Ctx:
        def __enter__(self):
            with _busy_lock:
                if user_id in _busy:
                    raise Busy()
                _busy.add(user_id)
            return self

        def __exit__(self, *exc):
            with _busy_lock:
                _busy.discard(user_id)
            return False
    return _Ctx()


def run(pattern: str, mode: str, items, *, limit_bytes: int = 0, max_matches: int = 501,
        seconds: float | None = None) -> Result:
    """Match `items` ([(id, text-or-path)]) in a worker process; at most `seconds` (REGEX_SECONDS)."""
    seconds = REGEX_SECONDS if seconds is None else seconds
    res = Result()
    t0 = time.monotonic()
    if not _slots.acquire(timeout=seconds):
        res.error = "Too many pattern searches are running — try again in a moment."
        return res
    try:
        proc = subprocess.Popen([sys.executable, "-I", "-S", os.path.abspath(__file__)], stdin=subprocess.PIPE,
                                stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, close_fds=True)
        done = threading.Event()

        def feed():
            try:
                head = {"pattern": pattern, "mode": mode, "limit": limit_bytes, "base": _share_base()}
                proc.stdin.write((json.dumps(head) + "\n").encode())
                for item in items:
                    if done.is_set():
                        break
                    proc.stdin.write((json.dumps(list(item)) + "\n").encode())
                proc.stdin.close()
            except (BrokenPipeError, OSError, ValueError):
                pass

        def read():
            try:
                for line in proc.stdout:
                    try:
                        m = json.loads(line)
                    except ValueError:
                        continue
                    if "error" in m:
                        res.error = m["error"]
                        continue
                    res.matches.append(m)
                    if len(res.matches) >= max_matches:
                        break
            except (OSError, ValueError):
                pass
            done.set()

        writer = threading.Thread(target=feed, daemon=True)
        reader = threading.Thread(target=read, daemon=True)
        writer.start()
        reader.start()
        reader.join(seconds)
        if reader.is_alive():
            res.stopped = True
        done.set()
        try:
            proc.kill()
        except OSError:
            pass
        try:
            proc.wait(2)
        except subprocess.TimeoutExpired:  # pragma: no cover
            pass
        reader.join(1)
        writer.join(1)
        for f in (proc.stdin, proc.stdout):
            try:
                f.close()
            except Exception:
                pass
    finally:
        _slots.release()
    res.matches = res.matches[:max_matches]
    res.ms = int((time.monotonic() - t0) * 1000)
    return res


# ---------- the worker process ----------
def _snip(line: str, s: int, e: int) -> str:
    a = max(0, s - SNIP)
    b = min(len(line), e + SNIP)
    pre = ("… " if a > 0 else "") + line[a:s]
    post = line[e:b] + (" …" if b < len(line) else "")
    return (pre + "⁅" + line[s:e] + "⁆" + post).strip()


def _share_base() -> str | None:
    try:
        from .. import config
        return config.share_root()
    except Exception:
        return None


def _read_text(path: str, limit: int, base: str | None = None) -> str | None:
    """The file's text — opened without following a link at the end, and only when what was opened really is
    inside /share (`base`; a folder swapped for a link after the path check is refused: security review 2026-10)."""
    try:
        fd = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    except OSError:
        return None
    if base:
        try:
            where = os.readlink(f"/proc/self/fd/{fd}")
        except OSError:
            where = os.path.realpath(path)
        if not (where == base or where.startswith(base.rstrip("/") + "/")):
            os.close(fd)
            return None
    with os.fdopen(fd, "rb") as f:
        data = f.read(limit if limit > 0 else 1024 * 1024)
    if data.startswith(b"\xef\xbb\xbf"):
        data = data[3:]
    return data.decode("utf-8", errors="replace")


def main() -> None:                    # pragma: no cover - runs in the worker process (tested through run())
    out = sys.stdout
    head = json.loads(sys.stdin.readline() or "{}")
    try:
        rx = re.compile(head.get("pattern", ""), re.IGNORECASE | re.MULTILINE)
    except re.error as e:
        out.write(json.dumps({"error": f"That pattern doesn't work: {e}."}) + "\n")
        out.flush()
        return
    mode = head.get("mode")
    limit = int(head.get("limit") or 0)
    for line in sys.stdin:
        try:
            nid, value = json.loads(line)
        except (ValueError, TypeError):
            continue
        if mode == "name":
            m = rx.search(value)
            if m:
                out.write(json.dumps({"id": nid, "s": m.start(), "e": m.end()}) + "\n")
                out.flush()
            continue
        text = _read_text(value, limit, head.get("base"))
        if text is None:
            continue
        m = rx.search(text)
        if not m:
            continue
        start = text.rfind("\n", 0, m.start()) + 1
        end = text.find("\n", m.start())
        end = len(text) if end == -1 else end
        ln = text.count("\n", 0, m.start()) + 1
        e = min(m.end(), end)
        out.write(json.dumps({"id": nid, "line": ln, "snip": _snip(text[start:end], m.start() - start, e - start)}) + "\n")
        out.flush()


if __name__ == "__main__":
    main()
