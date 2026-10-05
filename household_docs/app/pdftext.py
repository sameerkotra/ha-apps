"""PDF text search (SPEC §17.5) and putting derived text into search.

**The job** (`run_pending`, housekeeping every minute, a few files at a time): every live PDF anywhere, up to
`pdf_index_mb` (0 = off), whose text wasn't read for this content yet (`pdf_text.sha256`) is read by
`formats/pdf_worker.py` — a separate `python -I` process that limits its own memory and CPU, killed after
TIMEOUT seconds, at most one at a time. Up to 500 pages. The result is kept in `pdf_text` (status ok /
encrypted "password-protected" / scanned "scanned — no text" / too_big / failed), and the text goes into
`fts` with `nodes.pdf_pages` (the line where each page starts), so search snippets say "page 3".

**Text that isn't in the file itself** — a PDF's text, or text read by AI from an image or a scanned PDF
(`ocr_text`, §17.6) — is put back into `fts` by `restore_bodies()` whenever the index has re-indexed the file
(the index knows only text files, so it leaves such files' text empty: `content_indexed = 0`).
"""
import json
import logging
import os
import subprocess
import sys
import threading
import time

from . import config, db, settings
from .search import fts
from .store import fileio, nodes, paths

logger = logging.getLogger("pdftext")

WORKER = os.path.join(os.path.dirname(__file__), "formats", "pdf_worker.py")
TIMEOUT = 30
MAX_PAGES = 500
PER_RUN = 5
RUN_SECONDS = 40
_slot = threading.BoundedSemaphore(1)

STATUS_TEXT = {"ok": "Text found", "encrypted": "Password-protected — its text isn't searchable",
               "scanned": "Scanned — no text (AI can read it)", "too_big": "Too big for text search",
               "failed": "Couldn't be read"}


class WorkerError(Exception):
    pass


def run_worker(data: bytes, mode: str = "text", timeout: float | None = None, **limits) -> dict:
    """Run the worker on a PDF's bytes; raises WorkerError when it was stopped or broke."""
    lim = {"mode": mode, "pages": MAX_PAGES, **limits}
    timeout = TIMEOUT if timeout is None else timeout
    with _slot:
        try:
            p = subprocess.run([sys.executable, "-I", WORKER, json.dumps(lim)], input=data, capture_output=True,
                               timeout=timeout, env={"PATH": os.environ.get("PATH", "")})
        except subprocess.TimeoutExpired:
            raise WorkerError(f"Reading this PDF took longer than {int(timeout)} seconds, so it was stopped.")
    if p.returncode != 0:
        raise WorkerError("Reading this PDF was stopped (it needed too much memory or time).")
    try:
        out = json.loads(p.stdout.decode("utf-8", "replace") or "{}")
    except ValueError:
        raise WorkerError("Reading this PDF failed.")
    if not isinstance(out, dict):
        raise WorkerError("Reading this PDF failed.")
    return out


def _limit_bytes(conn) -> int:
    return int(settings.get("pdf_index_mb", conn)) * 1024 * 1024


def read_node_bytes(conn, node, limit: int) -> bytes | None:
    """The file's bytes (≤ limit), realpath-checked inside its root and opened without following links."""
    _root, real = nodes.real_path(conn, node)
    with fileio.open_read(real) as f:
        data = f.read(limit + 1)
    return None if len(data) > limit else data


def candidates(conn, n: int = PER_RUN):
    limit = _limit_bytes(conn)
    return conn.execute(
        f"SELECT n.* FROM nodes n LEFT JOIN pdf_text p ON p.node_id = n.id JOIN roots r ON r.id = n.root_id "
        f"WHERE n.kind = 'file' AND lower(n.ext) = 'pdf' AND {nodes.LIVE} AND r.missing = 0 AND n.size > 0 "
        f"AND (p.node_id IS NULL OR p.sha256 IS NOT n.sha256) "
        f"ORDER BY CASE WHEN n.size <= ? THEN 0 ELSE 1 END, n.mtime DESC LIMIT ?", (limit, n)).fetchall()


def _store(conn, node, status: str, pages=None, texts=None) -> None:
    text = None
    page_lines = None
    if texts is not None and status == "ok":
        lines, starts = [], []
        for t in texts:
            starts.append(len(lines))
            lines += [x for x in t.split("\n") if x] or [""]
        text = "\n".join(lines)
        page_lines = json.dumps(starts)
    conn.execute("INSERT INTO pdf_text (node_id, sha256, status, pages, text, page_lines, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?) "
                 "ON CONFLICT(node_id) DO UPDATE SET sha256 = excluded.sha256, status = excluded.status, "
                 "pages = excluded.pages, text = excluded.text, page_lines = excluded.page_lines, "
                 "updated_at = excluded.updated_at",
                 (node["id"], node["sha256"], status, pages, text, page_lines, config.now_iso()))
    apply_body(conn, node["id"])


def index_one(node_id: str) -> str | None:
    """Read one PDF's text now. Returns its status (None when it isn't a PDF that can be read now)."""
    with db.get_conn() as conn:
        node = nodes.get(conn, node_id)
        if node is None or (node["ext"] or "").lower() != "pdf":
            return None
        limit = _limit_bytes(conn)
        if limit <= 0:
            return None
        if (node["size"] or 0) > limit:
            _store(conn, node, "too_big")
            return "too_big"
        try:
            data = read_node_bytes(conn, node, limit)
        except (OSError, paths.PathError, Exception) as e:
            logger.info("Couldn't read a PDF for search: %s", type(e).__name__)
            return None
        if data is None:
            _store(conn, node, "too_big")
            return "too_big"
    try:
        out = run_worker(data)
        status = out.get("status") if out.get("status") in ("ok", "encrypted", "scanned", "failed") else "failed"
    except WorkerError as e:
        logger.info("%s", e)
        out, status = {}, "failed"
    with db.get_conn() as conn:
        fresh = nodes.get(conn, node_id)
        if fresh is None or fresh["sha256"] != node["sha256"]:
            return None                          # changed meanwhile: the next run reads the new one
        _store(conn, fresh, status, out.get("pages"), out.get("texts") if status == "ok" else None)
    return status


def run_pending(budget: float = RUN_SECONDS) -> int:
    """The background job: a few PDFs per run, then the derived text the index dropped."""
    if db.RESTORING.is_set():
        return 0
    t0 = time.monotonic()
    done = 0
    with db.get_conn() as conn:
        if _limit_bytes(conn) > 0:
            todo = [r["id"] for r in candidates(conn)]
        else:
            todo = []
    for nid in todo:
        if time.monotonic() - t0 > budget:
            break
        try:
            if index_one(nid) is not None:
                done += 1
        except Exception:
            logger.exception("Reading a PDF's text failed")
    restore_bodies()
    return done


# ---------------------------------------------------------------- derived text into search
def derived_text(conn, node) -> tuple[str | None, list | None]:
    """(text, page starts) for search: AI-read text first (a scan), else the PDF's own text."""
    o = conn.execute("SELECT text FROM ocr_text WHERE node_id = ?", (node["id"],)).fetchone()
    if o is not None and o["text"]:
        return o["text"], None
    p = conn.execute("SELECT * FROM pdf_text WHERE node_id = ? AND status = 'ok'", (node["id"],)).fetchone()
    if p is not None and p["text"] is not None and p["sha256"] == node["sha256"]:
        try:
            return p["text"], json.loads(p["page_lines"] or "null")
        except ValueError:
            return p["text"], None
    return None, None


def apply_body(conn, node_id: str) -> bool:
    node = nodes.get(conn, node_id, live=False)
    if node is None:
        return False
    text, pages = derived_text(conn, node)
    if text is None:
        return False
    fts.set_entry(conn, node_id, node["name"], text)
    conn.execute("UPDATE nodes SET content_indexed = 1, pdf_pages = ? WHERE id = ?",
                 (json.dumps(pages) if pages is not None else None, node_id))
    return True


def restore_bodies(limit: int = 500) -> int:
    with db.get_conn() as conn:
        ids = [r["id"] for r in conn.execute(
            "SELECT n.id FROM nodes n WHERE n.content_indexed = 0 AND n.kind = 'file' AND "
            "(EXISTS (SELECT 1 FROM ocr_text o WHERE o.node_id = n.id) OR "
            " EXISTS (SELECT 1 FROM pdf_text p WHERE p.node_id = n.id AND p.status = 'ok' AND p.sha256 IS n.sha256)) "
            "LIMIT ?", (limit,))]
        n = 0
        for nid in ids:
            n += apply_body(conn, nid)
    return n


def status_for(conn, ids) -> dict:
    """Per file: {"pdfText": status, "pdfPages": n} and {"aiText": True} when AI read its text."""
    ids = [i for i in ids if i]
    out: dict = {}
    for i in range(0, len(ids), 500):
        chunk = ids[i:i + 500]
        q = ",".join("?" * len(chunk))
        for r in conn.execute(f"SELECT p.node_id, p.status, p.pages, p.sha256, n.sha256 AS cur FROM pdf_text p "
                              f"JOIN nodes n ON n.id = p.node_id WHERE p.node_id IN ({q})", chunk):
            if r["sha256"] == r["cur"]:
                out.setdefault(r["node_id"], {}).update(pdfText=r["status"], pdfPages=r["pages"])
        for r in conn.execute(f"SELECT node_id FROM ocr_text WHERE node_id IN ({q})", chunk):
            out.setdefault(r["node_id"], {})["aiText"] = True
    return out


def page_of(conn, row, words: list[str]) -> int | None:
    """The page (1-based) of the first line of a PDF's indexed text holding one of the words."""
    if not words:
        return None
    r = conn.execute("SELECT n.pdf_pages, n.content_indexed, f.body FROM nodes n JOIN fts f ON f.node_id = n.id "
                     "WHERE n.id = ?", (row["id"],)).fetchone()
    if not r or not r["pdf_pages"] or not r["body"] or not r["content_indexed"]:
        return None
    try:
        starts = json.loads(r["pdf_pages"])
    except ValueError:
        return None
    folded = [fts.fold(w) for w in words if w]
    import bisect
    for n, line in enumerate(r["body"].split("\n")):
        fl = fts.fold(line)
        if any(w and w in fl for w in folded):
            return bisect.bisect_right(starts, n)
    return None


def text_info(conn, user: dict, node_id: str) -> dict:
    """The file panel: the PDF's status and AI-read text (`GET /api/nodes/{id}/text`)."""
    from . import sharing
    node, role = sharing.require(conn, user, node_id, "viewer")
    p = conn.execute("SELECT * FROM pdf_text WHERE node_id = ?", (node_id,)).fetchone()
    o = conn.execute("SELECT o.*, u.name AS editor FROM ocr_text o LEFT JOIN users u ON u.id = o.edited_by "
                     "WHERE o.node_id = ?", (node_id,)).fetchone()
    pdf = None
    if p is not None and p["sha256"] == node["sha256"]:
        pdf = {"status": p["status"], "label": STATUS_TEXT.get(p["status"], p["status"]), "pages": p["pages"]}
    elif (node["ext"] or "").lower() == "pdf":
        pdf = {"status": "waiting" if int(settings.get("pdf_index_mb", conn)) > 0 else "off",
               "label": "Waiting to be read" if int(settings.get("pdf_index_mb", conn)) > 0 else "PDF text search is off"}
    ai = None
    if o is not None:
        ai = {"text": o["text"], "model": o["model"], "editedByName": o["editor"], "updatedAt": o["updated_at"],
              "stale": bool(o["sha256"] and o["sha256"] != node["sha256"])}
    return {"pdf": pdf, "ai": ai, "canEdit": sharing.at_least(role, "editor")}


def edit_ai_text(conn, user: dict, node_id: str, text: str) -> dict:
    from fastapi import HTTPException
    from . import sharing
    from .store import fileio
    node, _role = sharing.require(conn, user, node_id, "editor")
    fileio.ensure_writable()
    if not isinstance(text, str) or len(text) > 200_000:
        raise HTTPException(422, "The text can be at most 200 000 characters.")
    cur = conn.execute("SELECT 1 FROM ocr_text WHERE node_id = ?", (node_id,)).fetchone()
    if cur is None:
        raise HTTPException(404, "AI hasn't read this file's text yet.")
    conn.execute("UPDATE ocr_text SET text = ?, edited_by = ?, updated_at = ? WHERE node_id = ?",
                 (text, user["id"], config.now_iso(), node_id))
    apply_body(conn, node_id)
    return text_info(conn, user, node_id)
