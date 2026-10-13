"""Makes a note's PDF (*Download as PDF*, SPEC §17.12) in a separate, time-limited process (review 2026-10 D2).

Run as `python -I docpdf_worker.py '<limits JSON>'` with {"title", "sub", "kind", "text"} as JSON on stdin; writes
the number of characters the fonts can't show, a newline, then the PDF's bytes to stdout. It limits its own
memory and CPU time and writes no files; the app also stops it after a wall-clock limit, so a note made to be
slow to lay out costs at most that.
"""
import json
import os
import sys

try:
    import resource
except ImportError:      # not on Linux
    resource = None

DEFAULT_LIMITS = {"mem": 1024 * 1024 * 1024, "cpu": 60}


def _limit_self(lim: dict) -> None:
    if resource is None:
        return
    for what, value in ((getattr(resource, "RLIMIT_AS", None), lim["mem"]),
                        (getattr(resource, "RLIMIT_CPU", None), lim["cpu"]),
                        (getattr(resource, "RLIMIT_FSIZE", None), 0),
                        (getattr(resource, "RLIMIT_NOFILE", None), 64)):
        if what is None:
            continue
        try:
            resource.setrlimit(what, (value, value))
        except (ValueError, OSError):
            pass


def main() -> None:
    lim = dict(DEFAULT_LIMITS)
    if len(sys.argv) > 1:
        try:
            lim.update(json.loads(sys.argv[1]))
        except ValueError:
            pass
    # the folder that holds the `app` package (python -I leaves the script's own folder off sys.path)
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
    from app.formats import doc_layout
    job = json.loads(sys.stdin.buffer.read().decode("utf-8"))
    _limit_self(lim)
    data, bad = doc_layout.render(str(job["title"]), str(job["sub"]), str(job["kind"]), str(job["text"]))
    sys.stdout.buffer.write(b"%d\n" % bad + data)
    sys.stdout.buffer.flush()


if __name__ == "__main__":
    main()
