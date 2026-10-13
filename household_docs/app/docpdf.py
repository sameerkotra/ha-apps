"""Download as PDF and *Save PDF here* (SPEC §17.12) for notes, Markdown notes and checklists, through the app's own
PDF writer (pdfwriter.py): the title, the owner and the date at the top, then the text — a plain note line by
line, a Markdown note with its headings, lists, tick boxes, quotes, code and tables, a checklist with its boxes
(ticked or empty) and indents. Sheets print from their editor (§8.5)."""
import json
import logging
import os
import subprocess
import sys
import threading

from fastapi import HTTPException

from . import config, documents, files, sharing, workers
from .formats import doc_layout, text as text_fmt
from .store import nodes, paths

logger = logging.getLogger("docpdf")

PDF_KINDS = ("note", "markdown", "checklist")

# The layout lives in formats/doc_layout.py (run in formats/docpdf_worker.py); these names stay here for callers.
build, inline_runs, md_blocks = doc_layout.build, doc_layout.inline_runs, doc_layout.md_blocks

WORKER = os.path.join(os.path.dirname(os.path.abspath(__file__)), "formats", "docpdf_worker.py")
TIMEOUT = 60                     # seconds of wall-clock time for one PDF
WAIT = 20                        # seconds to wait for a free slot
_slot = threading.BoundedSemaphore(1)


def render(title: str, sub: str, kind: str, text: str) -> tuple[bytes, int]:
    """(PDF bytes, unsupported characters), made in the time-limited worker (review 2026-10 D2): a note made to
    be slow to lay out is stopped after TIMEOUT seconds, and only one PDF is made at a time."""
    job = json.dumps({"title": title, "sub": sub, "kind": kind, "text": text}).encode("utf-8")
    if not _slot.acquire(timeout=WAIT):
        raise HTTPException(503, "Another PDF is being made — try again in a moment.")
    try:
        with workers.heavy(WAIT):
            p = subprocess.run([sys.executable, "-I", WORKER, json.dumps({"cpu": TIMEOUT})], input=job,
                               capture_output=True, timeout=TIMEOUT, env={"PATH": os.environ.get("PATH", "")})
    except workers.Busy:
        raise HTTPException(503, "The app is busy reading other files — try again in a moment.")
    except subprocess.TimeoutExpired:
        raise HTTPException(422, f"Making this PDF took longer than {TIMEOUT} seconds, so it was stopped — "
                                 "use Print → Save as PDF instead.")
    finally:
        _slot.release()
    head, sep, data = p.stdout.partition(b"\n")
    if p.returncode != 0 or not sep or not head.isdigit() or not data.startswith(b"%PDF"):
        logger.warning("Making a PDF failed (exit %s)", p.returncode)
        raise HTTPException(422, "This note is too big to make a PDF here — use Print → Save as PDF instead.")
    return data, int(head)


def make(conn, user: dict, node_id: str) -> tuple[str, bytes, int]:
    """(file name, PDF bytes, characters the fonts can't show) of a document the person can open."""
    name, job = prepare(conn, user, node_id)
    data, bad = render(*job)
    return name, data, bad


def prepare(conn, user: dict, node_id: str) -> tuple[str, tuple]:
    """(file name, render() arguments) of a document the person can open — read under `conn`; the PDF itself is
    made afterwards, without holding the database."""
    node, _role = sharing.require(conn, user, node_id, "viewer")
    if node["kind"] not in PDF_KINDS:
        raise HTTPException(415, "Notes and checklists download as PDF; sheets print from their editor.")
    _root, _real, data, _etag, _st, _sha = documents._read(conn, node)
    try:
        text, _meta = text_fmt.decode(data)
    except text_fmt.NotText as e:
        raise HTTPException(415, str(e))
    stem = paths.split_ext(node["name"])[0] if node["ext"] else node["name"]
    owner = sharing.owner_id(conn, node)
    who = documents._name_of(conn, owner) if owner else None
    when = config.now().strftime("%Y-%m-%d %H:%M")
    sub = " · ".join(x for x in (who, f"changed {config.parse_iso(node['mtime']).astimezone(config.now().tzinfo).strftime('%Y-%m-%d %H:%M')}"
                                 if node["mtime"] else None, f"printed {when}") if x)
    return stem + ".pdf", (stem, sub, node["kind"], text)


def save_here(user: dict, node_id: str) -> tuple[str, int]:
    """Write the PDF beside the document (the person must be able to add files there). → (new node id, unsupported)."""
    from . import db
    with db.get_conn() as conn:
        node, _role = sharing.require(conn, user, node_id, "viewer")
        ref = nodes.parent_ref(node, nodes.roots_info(conn))
        if ref is None and sharing.owner_id(conn, node) != user["id"]:
            raise HTTPException(403, "You can't add files beside this document — download the PDF instead.")
        name, job = prepare(conn, user, node_id)
    data, bad = render(*job)
    try:
        prep = files.prepare_upload(user, ref, name, len(data))
    except HTTPException as e:
        if e.status_code in (403, 404):
            raise HTTPException(403, "You can't add files beside this document — download the PDF instead.")
        raise
    fd, tmp = files.new_temp(prep)
    with os.fdopen(fd, "wb") as f:
        f.write(data)
        f.flush()
        os.fsync(f.fileno())
    return files.finish_upload(user, prep, tmp, len(data), "keep"), bad
