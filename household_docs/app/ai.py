"""Optional AI (SPEC §17.6). Off until an admin turns it on and sets it up (Admin → App settings → AI); nothing is ever
sent before that, and every action is started by a person after a dialog that names the provider (the folder
opt-in below is such a choice, made once per folder).

What each action sends (DOCS.md lists the same):
- **Read text** (`ocr`) — the picture (a PNG / JPEG / GIF / WebP file), or the JPEG pages of a scanned PDF (at
  most OCR_PAGES), and an instruction to write out the text. The answer is kept in `ocr_text` (never written
  into the file), searchable, shown in the file panel as "Text read by AI" and editable there by editors.
  **Per folder** ("Read text from scans in this folder", editors of the folder): pictures and scanned PDFs in it
  (and its subfolders) are read in the background, a few a minute (`run_folder_jobs`).
- **Summarise** — the text of one note, checklist or PDF (or text read by AI), at most MAX_CHARS characters.
- **Turn text into a checklist** — the text the person pasted or the note they chose; the answer is a list of
  items they check before the checklist is made.
- **Table → sheet** — pasted text, or one photo of a table (with the vision model); the answer is rows of cells
  the person checks before the sheet is made.
- **Ask about a folder** — the question and the searchable text of at most ASK_FILES files in that folder
  (only files the person can open), at most ASK_CHARS characters in all, each with its name; the answer cites
  the files it used ([1], [2] …), which the page links.

Every request goes through `_call`: AI must be on and set up, the month's token limit not used up, and each
request is written down for Admin → AI usage (ai_usage.py) — what for, the model, tokens and time, never content.
"""
from __future__ import annotations

import base64
import json
import logging
import re
import time

from fastapi import HTTPException

from . import ai_client, ai_usage, config, db, pdftext, settings, sharing
from .store import fileio, kinds, nodes, roots

logger = logging.getLogger("ai")

MAX_CHARS = 100_000
ASK_FILES = 30
ASK_CHARS = 200_000
OCR_PAGES = 10
IMAGE_MAX = 8 * 1024 * 1024
MAX_ITEMS = 200
MAX_ROWS, MAX_COLS = 200, 26
FOLDER_JOBS_PER_RUN = 3
_failed: dict = {}            # node id -> sha256 the background reading failed on (not tried again until it changes)


# ---------------------------------------------------------------- state
def public_state(conn) -> dict:
    """For every page: is AI usable, and which provider would get what is sent."""
    problem = settings.ai_problem(conn)
    v = settings.all_values(conn)
    label = settings.PROVIDER_LABELS.get(v["ai_provider"], v["ai_provider"])
    return {"on": problem is None, "provider": label if problem is None else None,
            "address": settings.ai_url(conn) if problem is None else None,
            "model": v["ai_model"] if problem is None else None,
            "visionModel": (v["ai_vision_model"] or v["ai_model"]) if problem is None else None}


def require(conn=None) -> None:
    problem = settings.ai_problem(conn)
    if problem:
        raise HTTPException(409, problem)


def _call(purpose: str, prompt: str, *, images=None, system: str | None = None, want_json: bool = False,
          max_tokens: int = 4000) -> str:
    """One request to the model (raises HTTPException with a plain message). Checks the switch and the limit first."""
    with db.get_conn() as conn:
        require(conn)
        left = ai_usage.limit_left(conn)
        cfg = ai_client.current(vision=bool(images), conn=conn)
    if left is not None and left <= 0:
        raise HTTPException(429, "This month's AI limit is used up (Admin → App settings → AI).")
    t0 = time.monotonic()
    try:
        reply = ai_client.generate(prompt, images=images, system=system, want_json=want_json, max_tokens=max_tokens, cfg=cfg)
    except ai_client.AIError as e:
        ai_usage.record(purpose=purpose, provider=cfg.provider, model=cfg.model, ok=False, error=str(e),
                        ms=int((time.monotonic() - t0) * 1000))
        raise HTTPException(502, str(e))
    ai_usage.record(purpose=purpose, provider=cfg.provider, model=cfg.model, ok=True, tokens_in=reply.input_tokens,
                    tokens_out=reply.output_tokens, ms=int((time.monotonic() - t0) * 1000))
    return reply.text or ""


def _json_answer(text: str) -> dict:
    t = (text or "").strip()
    t = re.sub(r"^```(?:json)?\s*|\s*```$", "", t)
    m = re.search(r"\{.*\}", t, re.S)
    try:
        out = json.loads(m.group(0) if m else t)
    except (ValueError, AttributeError):
        raise HTTPException(502, "The AI model's answer wasn't in the expected form — try again.")
    if not isinstance(out, dict):
        raise HTTPException(502, "The AI model's answer wasn't in the expected form — try again.")
    return out


# ---------------------------------------------------------------- what a file holds as text
def _file_text(conn, node, limit: int = MAX_CHARS) -> str | None:
    """A document's or PDF's text for the model: notes / checklists from the file, everything else from the
    search index (sheets, PDFs, text read by AI, other text files)."""
    if node["kind"] in ("note", "markdown", "checklist"):
        from . import documents
        from .formats import text as text_fmt
        _r, _p, data, _e, _st, _sha = documents._read(conn, node)
        try:
            return text_fmt.decode(data)[0][:limit]
        except text_fmt.NotText:
            return None
    derived, _pages = pdftext.derived_text(conn, node)
    if derived:
        return derived[:limit]
    r = conn.execute("SELECT body FROM fts WHERE node_id = ?", (node["id"],)).fetchone()
    return (r["body"] or "")[:limit] if r and r["body"] else None


# ---------------------------------------------------------------- read text (OCR)
OCR_PROMPT = ("Write out all the text you can read in this picture, exactly as written, keeping its lines and "
              "order. Do not describe the picture and do not add anything else. If there is no text, answer with "
              "nothing.")


def _images_of(conn, node) -> list[str]:
    """Base64 pictures to read: the image file itself, or a scanned PDF's JPEG pages."""
    if (node["size"] or 0) <= 0:
        return []
    if node["preview"]:
        if node["size"] > IMAGE_MAX:
            raise HTTPException(413, "This picture is too big to send (at most 8 MB).")
        data = pdftext.read_node_bytes(conn, node, IMAGE_MAX)
        if data is None or kinds.raster_type(data[:16]) is None:
            raise HTTPException(415, "This file isn't a picture the AI can read.")
        return [base64.b64encode(data).decode()]
    if (node["ext"] or "").lower() == "pdf":
        limit = max(int(settings.get("pdf_index_mb", conn)), 20) * 1024 * 1024
        data = pdftext.read_node_bytes(conn, node, limit)
        if data is None:
            raise HTTPException(413, "This PDF is too big to read.")
        try:
            out = pdftext.run_worker(data, mode="images", images=OCR_PAGES, image_bytes=IMAGE_MAX)
        except pdftext.WorkerError as e:
            raise HTTPException(422, str(e))
        if out.get("status") == "encrypted":
            raise HTTPException(422, "This PDF is password-protected.")
        imgs = [i["b64"] for i in out.get("images") or [] if isinstance(i, dict) and i.get("b64")]
        if not imgs:
            raise HTTPException(422, "No scanned pages were found in this PDF (only JPEG scans can be read).")
        return imgs
    raise HTTPException(415, "AI can read text in pictures and scanned PDFs.")


def ocr_node(node_id: str, user: dict | None) -> dict:
    """Read a picture's or scanned PDF's text and keep it. `user` None: the folder's background reading."""
    with db.get_conn() as conn:
        require(conn)
        if user is not None:
            node, _role = sharing.require(conn, user, node_id, "editor")
        else:
            node = nodes.get(conn, node_id)
            if node is None:
                return {}
        if node["kind"] != "file":
            raise HTTPException(415, "AI can read text in pictures and scanned PDFs.")
        imgs = _images_of(conn, node)
        sha = node["sha256"]
    texts = []
    for i, b64 in enumerate(imgs):
        t = _call("ocr", OCR_PROMPT, images=[b64], max_tokens=4000).strip()
        texts.append(t if len(imgs) == 1 else f"— page {i + 1} —\n{t}")
    text = "\n\n".join(texts).strip()
    cfg_model = ai_client.current(vision=True).model
    with db.get_conn() as conn:
        if user is not None:
            fileio.ensure_writable()
        conn.execute("INSERT INTO ocr_text (node_id, sha256, text, model, edited_by, updated_at) VALUES (?, ?, ?, ?, NULL, ?) "
                     "ON CONFLICT(node_id) DO UPDATE SET sha256 = excluded.sha256, text = excluded.text, "
                     "model = excluded.model, edited_by = NULL, updated_at = excluded.updated_at",
                     (node_id, sha, text, cfg_model, config.now_iso()))
        pdftext.apply_body(conn, node_id)
        if user is not None:
            return pdftext.text_info(conn, user, node_id)
    return {"text": text}


# ---------------------------------------------------------------- the folder opt-in
def _folder_target(conn, user: dict, ref: str, wanted: str):
    if ref.startswith("root:"):
        root = roots.root_row(conn, ref[5:])
        role = sharing.root_role(conn, user, root) if root is not None and root["kind"] == "shared" else None
        if role is None:
            raise HTTPException(404, sharing.NOT_FOUND)
        if not sharing.at_least(role, wanted):
            raise HTTPException(403, "Only people who can change this folder can do this.")
        return root, None
    node, _role = sharing.require(conn, user, ref, wanted)
    if node["kind"] != "folder":
        raise HTTPException(422, "Choose a folder.")
    return roots.root_row(conn, node["root_id"]), node


def folder_opted_in(conn, ref: str | None) -> bool:
    if not ref:
        return False
    return conn.execute("SELECT 1 FROM ai_folders WHERE target = ?", (ref,)).fetchone() is not None


def folder_state(conn, user: dict, ref: str) -> dict:
    root, folder = _folder_target(conn, user, ref, "viewer")
    where, params = _subtree_sql(root, folder)
    waiting = conn.execute(f"SELECT COUNT(*) FROM nodes n LEFT JOIN pdf_text p ON p.node_id = n.id WHERE {where} "
                           f"AND n.kind = 'file' AND {nodes.LIVE} AND (n.preview IS NOT NULL OR p.status = 'scanned') "
                           "AND NOT EXISTS (SELECT 1 FROM ocr_text o WHERE o.node_id = n.id)", params).fetchone()[0]
    on = folder_opted_in(conn, ref)
    return {"on": on, "waiting": waiting, "ai": public_state(conn),
            "canChange": _can(conn, user, ref, not on)}


def _can(conn, user, ref, on: bool = True) -> bool:
    wanted = "editor"
    if on and not ref.startswith("root:"):
        node = nodes.get(conn, ref)
        r = roots.root_row(conn, node["root_id"]) if node is not None else None
        if r is not None and r["kind"] == "person":
            wanted = "manager"
    try:
        _folder_target(conn, user, ref, wanted)
        return True
    except HTTPException:
        return False


def set_folder(conn, user: dict, ref: str, on: bool) -> dict:
    """Let the AI read a folder's pictures, or stop it. Turning it on sends pictures to the AI provider, so in a
    person's folder only its owner or a manager may (never an editor — security review); in an admin shared
    folder anyone with Read and write. Anyone who can change the folder may turn it off."""
    if on:
        require(conn)
        node = None if ref.startswith("root:") else nodes.get(conn, ref)
        if node is not None:
            r = roots.root_row(conn, node["root_id"])
            if r is not None and r["kind"] == "shared":
                _folder_target(conn, user, ref, "editor")      # an admin shared folder's folder: Read and write
            else:
                _folder_target(conn, user, ref, "viewer")      # 404 first for people who can't see it
                if not sharing.at_least(sharing.role_of(conn, user, node), "manager"):
                    raise HTTPException(403, "Only its owner (or a manager) can let the AI read this folder's pictures.")
        else:
            _folder_target(conn, user, ref, "editor")
    else:
        _folder_target(conn, user, ref, "editor")                  # turning it off: anyone who can change it
    if on:
        conn.execute("INSERT OR IGNORE INTO ai_folders (target, added_by, created_at) VALUES (?, ?, ?)",
                     (ref, user["id"], config.now_iso()))
    else:
        conn.execute("DELETE FROM ai_folders WHERE target = ?", (ref,))
    db.audit(conn, "ai_folder_on" if on else "ai_folder_off", user["id"], None if ref.startswith("root:") else ref)
    return folder_state(conn, user, ref)


def _subtree_sql(root, folder) -> tuple[str, list]:
    if folder is None:
        return "n.root_id = ?", [root["id"]]
    return "n.root_id = ? AND substr(n.rel, 1, ?) = ?", [folder["root_id"], len(folder["rel"]) + 1, folder["rel"] + "/"]


def folder_candidates(conn, limit: int = FOLDER_JOBS_PER_RUN) -> list[str]:
    out = []
    for f in conn.execute("SELECT target FROM ai_folders").fetchall():
        ref = f["target"]
        if ref.startswith("root:"):
            root = roots.root_row(conn, ref[5:])
            folder = None
            if root is None or root["missing"]:
                continue
        else:
            folder = nodes.get(conn, ref)
            if folder is None:
                continue
            root = roots.root_row(conn, folder["root_id"])
        where, params = _subtree_sql(root, folder)
        for r in conn.execute(f"SELECT n.id, n.sha256 FROM nodes n LEFT JOIN pdf_text p ON p.node_id = n.id WHERE {where} "
                              f"AND n.kind = 'file' AND {nodes.LIVE} AND n.size > 0 AND "
                              "((n.preview IS NOT NULL AND n.size <= ?) OR (p.status = 'scanned' AND p.sha256 IS n.sha256)) "
                              "AND NOT EXISTS (SELECT 1 FROM ocr_text o WHERE o.node_id = n.id AND "
                              "(o.sha256 IS n.sha256 OR o.edited_by IS NOT NULL)) ORDER BY n.mtime DESC LIMIT 20",
                              (*params, IMAGE_MAX)):
            if _failed.get(r["id"]) == r["sha256"] or r["id"] in out:
                continue
            out.append(r["id"])
            if len(out) >= limit:
                return out
    return out


def run_folder_jobs() -> int:
    """Housekeeping: read a few pictures / scanned PDFs in opted-in folders. Nothing while AI is off."""
    if db.RESTORING.is_set():
        return 0
    with db.get_conn() as conn:
        if settings.ai_problem(conn) is not None:
            return 0
        left = ai_usage.limit_left(conn)
        if left is not None and left <= 0:
            return 0
        todo = folder_candidates(conn)
        shas = {i: (nodes.get(conn, i) or {"sha256": None})["sha256"] for i in todo}
    n = 0
    for nid in todo:
        try:
            ocr_node(nid, None)
            n += 1
        except HTTPException as e:
            _failed[nid] = shas.get(nid)
            logger.info("Reading text in a scan failed: %s", e.detail)
            if e.status_code in (409, 429):
                break
        except Exception:
            _failed[nid] = shas.get(nid)
            logger.exception("Reading text in a scan failed")
    return n


def on_activity(conn, action, node, actor_id) -> None:
    """Nothing to do at once: the folder job finds new pictures by itself (kept as a hook for symmetry)."""
    return None


# ---------------------------------------------------------------- summarise, checklist, sheet, ask
def summarise(user: dict, node_id: str) -> dict:
    with db.get_conn() as conn:
        require(conn)
        node, _role = sharing.require(conn, user, node_id, "viewer")
        text = _file_text(conn, node)
        name = node["name"]
    if not text or not text.strip():
        raise HTTPException(422, "There's no text in this file to summarise.")
    prompt = (f"Summarise the document below in a few short bullet points (Markdown), in the language it is "
              f"written in. Only use what the document says.\n\nDocument: {name}\n\n{text}")
    return {"summary": _call("summarise", prompt, max_tokens=1500).strip(), "chars": len(text)}


def suggest_filing(user: dict, node_id: str) -> dict:
    """§17.18: a name and a folder for a new scan (or any file) from its text — a suggestion the person accepts or
    not, never applied automatically. Sends the file's text (PDF text or text read by AI, ≤ 20 000 characters) and
    the names of the folders in the same space that the person can change (≤ 150)."""
    with db.get_conn() as conn:
        require(conn)
        node, _role = sharing.require(conn, user, node_id, "editor")
        if node["kind"] == "folder":
            raise HTTPException(422, "Choose a file.")
        text = _file_text(conn, node, 20_000)
        root = conn.execute("SELECT * FROM roots WHERE id = ?", (node["root_id"],)).fetchone()
        folders = []
        for f in conn.execute(f"SELECT * FROM nodes n WHERE n.root_id = ? AND n.kind = 'folder' AND {nodes.LIVE} "
                              "ORDER BY n.rel LIMIT 600", (root["id"],)).fetchall():
            if sharing.at_least(sharing.role_of(conn, user, f), "editor"):
                folders.append((f["id"], f["rel"]))
            if len(folders) >= 150:
                break
        name = node["name"]
        ext = node["ext"]
    if not text or not text.strip():
        raise HTTPException(422, "There's no text in this file yet — read its text first (PDF text, or ✨ Read text).")
    listing = "\n".join(f"{i + 1}. {rel}" for i, (_fid, rel) in enumerate(folders)) or "(none)"
    prompt = ("Suggest a short, clear file name (without the extension, at most 60 characters, in the document's "
              "language; start with the date as yyyy-mm-dd if the document has one) and the best folder for this "
              'document from the numbered list. Answer with JSON: {"name": "…", "folder": <number or 0 for none>}.'
              f"\n\nCurrent name: {name}\n\nFolders:\n{listing}\n\nDocument text:\n{text}")
    out = _json_answer(_call("filing", prompt, want_json=True, max_tokens=300))
    from .store import paths
    suggested = " ".join(str(out.get("name") or "").split())[:60].strip(" .")
    try:
        suggested = paths.check_name(f"{suggested}.{ext}" if ext else suggested) if suggested else None
    except paths.PathError:
        suggested = paths.clean_name(suggested) + (f".{ext}" if ext else "") if suggested else None
    pick = out.get("folder")
    folder = folders[pick - 1] if isinstance(pick, int) and not isinstance(pick, bool) and 1 <= pick <= len(folders) else None
    return {"name": suggested, "folderId": folder[0] if folder else None,
            "folderPath": folder[1] if folder else None, "provider": public_state_name()}


def public_state_name() -> str | None:
    with db.get_conn() as conn:
        return public_state(conn)["provider"]


def to_checklist(user: dict, text: str | None, node_id: str | None) -> dict:
    with db.get_conn() as conn:
        require(conn)
        if node_id:
            node, _role = sharing.require(conn, user, node_id, "viewer")
            text = _file_text(conn, node)
    text = (text or "").strip()[:MAX_CHARS]
    if not text:
        raise HTTPException(422, "Paste or choose some text first.")
    prompt = ('Turn the text below into a checklist of short, separate things to do or get (for a recipe: the '
              'ingredients, then the steps). Answer with JSON: {"items": ["…", "…"]}. Keep the text\'s language.'
              f"\n\nText:\n{text}")
    out = _json_answer(_call("checklist", prompt, want_json=True, max_tokens=3000))
    items = [" ".join(str(x).split())[:500] for x in out.get("items") or [] if str(x).strip()][:MAX_ITEMS]
    if not items:
        raise HTTPException(502, "The AI model found no items in that text.")
    return {"items": items}


def to_sheet(user: dict, text: str | None, image: str | None) -> dict:
    with db.get_conn() as conn:
        require(conn)
    images = None
    if image:
        if not isinstance(image, str) or len(image) > IMAGE_MAX * 4 // 3 + 16:
            raise HTTPException(413, "That photo is too big (at most 8 MB).")
        b64 = image.split(",", 1)[1] if image.startswith("data:") else image
        try:
            head = base64.b64decode(b64[:24] + "=" * (-len(b64[:24]) % 4))
        except ValueError:
            raise HTTPException(422, "That isn't a picture.")
        if kinds.raster_type(head) is None:
            raise HTTPException(415, "Send a PNG, JPEG, GIF or WebP photo of the table.")
        images = [b64]
    text = (text or "").strip()[:MAX_CHARS]
    if not images and not text:
        raise HTTPException(422, "Paste the table or choose a photo of it first.")
    prompt = ('Read the table ' + ('in this photo' if images else 'below') + ' and answer with JSON: '
              '{"rows": [["cell", "cell"], …]} — the header row first, one list per row, numbers as numbers '
              '(no thousands separators), empty cells as "". Do not add anything that is not in the table.'
              + ("" if images else f"\n\nTable:\n{text}"))
    out = _json_answer(_call("sheet", prompt, images=images, want_json=True, max_tokens=6000))
    rows = []
    for row in (out.get("rows") or [])[:MAX_ROWS]:
        if not isinstance(row, list):
            continue
        cells = []
        for v in row[:MAX_COLS]:
            if isinstance(v, bool) or v is None:
                v = "" if v is None else str(v)
            cells.append(v if isinstance(v, (int, float)) else str(v)[:1000])
        rows.append(cells)
    if not rows:
        raise HTTPException(502, "The AI model found no table there.")
    return {"rows": rows}


def ask(user: dict, ref: str, question: str) -> dict:
    question = (question or "").strip()
    if not question or len(question) > 2000:
        raise HTTPException(422, "Ask a question (at most 2 000 characters).")
    with db.get_conn() as conn:
        require(conn)
        root, folder = _folder_target(conn, user, ref, "viewer")
        where, params = _subtree_sql(root, folder)
        if folder is None:
            where, params = "n.root_id = ?", [root["id"]]
        acc_sql, acc_params = sharing.access_cte(user["id"])
        rows = conn.execute(acc_sql + f"SELECT n.* FROM nodes n JOIN acc ON acc.node_id = n.id WHERE {where} "
                            f"AND n.kind != 'folder' AND {nodes.LIVE} ORDER BY n.mtime DESC LIMIT 200",
                            (*acc_params, *params)).fetchall()
        if folder is not None:      # the folder's own files first, then deeper ones
            rows = sorted(rows, key=lambda r: (r["parent_id"] != folder["id"], -(_ts(r["mtime"]))))
        used, total = [], 0
        for r in rows:
            if len(used) >= ASK_FILES or total >= ASK_CHARS:
                break
            try:
                t = _file_text(conn, r, ASK_CHARS - total)
            except HTTPException:
                t = None
            if not t or not t.strip():
                continue
            used.append({"n": len(used) + 1, "id": r["id"], "name": r["name"], "kind": r["kind"], "ext": r["ext"],
                         "text": t})
            total += len(t)
        label = folder["name"] if folder is not None else root["label"]
    if not used:
        raise HTTPException(422, "There's no text in this folder's files to ask about.")
    sources = "\n\n".join(f"[{u['n']}] {u['name']}\n{u['text']}" for u in used)
    prompt = (f"Answer the question using only the files below from the folder “{label}”. Cite the files you use "
              "as [1], [2] … right after what they support. If the files don't say, answer that you can't find it. "
              f"Answer in the question's language.\n\nQuestion: {question}\n\nFiles:\n{sources}")
    answer = _call("ask", prompt, max_tokens=2000).strip()
    cited = sorted({int(n) for n in re.findall(r"\[(\d{1,2})\]", answer)})
    files_ = [{k: u[k] for k in ("n", "id", "name", "kind", "ext")} for u in used if u["n"] in cited]
    return {"answer": answer, "files": files_, "sent": [{"n": u["n"], "name": u["name"]} for u in used],
            "chars": total}


def _ts(iso) -> float:
    t = config.parse_iso(iso)
    return t.timestamp() if t else 0.0
