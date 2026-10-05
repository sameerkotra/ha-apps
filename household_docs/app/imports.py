"""Bringing things in (SPEC §17.11): Google Keep notes from a Google Takeout .zip, and any .zip of files and folders.

**Two steps.** The .zip is sent once (`receive`: the body is the file, streamed into the person's hidden `.tmp/`,
at most `upload_mb`) and checked; the answer is a preview with counts and a token. *Import* starts a background
job with that token (`start`); the page polls it (`job`). Tokens and jobs live in memory for an hour.

**Every .zip is untrusted** (`check_member`): at most MAX_MEMBERS entries and MAX_UNPACKED bytes unpacked (by the
central directory, and counted again while reading — a member is never read past its declared size), no
encrypted members, no links, no absolute paths, no "..", hidden entries and `__MACOSX` skipped, each part of a
path cleaned for Windows (`paths.clean_name`), at most MAX_DEPTH folders deep; a member that inflates more than
MAX_RATIO times (and past 10 MB) is skipped as a possible zip bomb.

**Google Keep** (the `Keep/*.json` files of a Takeout): each note becomes a `.txt` note or, for a list, a
checklist `.md` (ticks kept) in My docs → Google Keep; archived notes go to its *Keep archive* subfolder;
trashed notes are skipped. The title (else the first line) is the name, the edited time the file's time, the
created time the item's; labels → tags, the colour → the item's colour, pinned → ⭐ Favourite; attached pictures
are saved beside the note. Keep's own id (its created time in microseconds, with the file name when there is
none) is kept in `keep_imports`, so importing the same Takeout again skips what is already here.

**A .zip of files** is unpacked into a new folder named after it ("Photos.zip" → "Photos", " (2)" on a clash) in
the folder the person picked (one they can edit).
"""
import json
import logging
import os
import re
import secrets
import stat
import threading
import time
import zipfile
from datetime import datetime, timezone

from fastapi import HTTPException

from . import config, db, docops, files, settings, sharing, tags
from .store import fileio, nodes, paths, roots

logger = logging.getLogger("imports")

MAX_MEMBERS = 10_000
MAX_UNPACKED = 4 * 1024 ** 3
MAX_RATIO = 1000
MAX_DEPTH = 10
MAX_JSON = 2 * 1024 * 1024
MAX_NOTES = 5000
TOKEN_SECONDS = 3600
KEEP_FOLDER = "Google Keep"
ARCHIVE_FOLDER = "Keep archive"
KEEP_COLOURS = {"RED": "red", "ORANGE": "orange", "YELLOW": "yellow", "GREEN": "green", "TEAL": "teal",
                "BLUE": "blue", "CERULEAN": "blue", "DARK_BLUE": "blue", "PURPLE": "purple", "PINK": "purple",
                "BROWN": "orange", "GRAY": "grey", "GREY": "grey"}

_uploads: dict = {}          # token -> {user, kind, path, at, preview}
_jobs: dict = {}             # job id -> {user, kind, state, done, total, …}
_lock = threading.Lock()


class ZipRefused(Exception):
    pass


# ---------------------------------------------------------------- receiving the .zip
def prepare(user: dict, kind: str, length: int | None) -> dict:
    if kind == "keep" and not settings.get("keep_import"):
        raise HTTPException(403, "An admin has turned off importing from Google Keep.")
    with db.get_conn() as conn:
        root = docops.my_root(conn, user)
        limit = files.upload_limit(conn)
        if length is not None and length > limit:
            raise HTTPException(413, f"That .zip is bigger than the {settings.get('upload_mb', conn)} MB upload limit.")
        fileio.ensure_writable()
        tmp_dir = roots.hidden_dir(root, ".tmp")
    return {"limit": limit, "tmp_dir": tmp_dir}


def new_temp(prep: dict) -> tuple[int, str]:
    import tempfile
    return tempfile.mkstemp(prefix="import-", suffix=".zip", dir=prep["tmp_dir"])


def receive(user: dict, kind: str, tmp: str, name: str | None) -> dict:
    """Check the received .zip and make its preview. Returns {token, preview}."""
    _expire()
    try:
        preview = keep_preview(user, tmp) if kind == "keep" else zip_preview(tmp, name)
    except ZipRefused as e:
        _remove(tmp)
        raise HTTPException(422, str(e))
    except Exception:
        _remove(tmp)
        raise
    token = secrets.token_hex(16)
    with _lock:
        _uploads[token] = {"user": user["id"], "kind": kind, "path": tmp, "at": time.monotonic(), "preview": preview,
                           "name": name}
    return {"token": token, "preview": preview}


def _remove(path: str) -> None:
    try:
        os.remove(path)
    except OSError:
        pass


def _expire() -> None:
    now = time.monotonic()
    with _lock:
        for t, u in list(_uploads.items()):
            if now - u["at"] > TOKEN_SECONDS:
                _remove(u["path"])
                _uploads.pop(t, None)
        for j, job in list(_jobs.items()):
            if job["state"] in ("done", "failed") and now - job["at"] > TOKEN_SECONDS:
                _jobs.pop(j, None)


def _take(user: dict, token: str, kind: str) -> dict:
    with _lock:
        u = _uploads.get(token)
        if u is None or u["user"] != user["id"] or u["kind"] != kind:
            raise HTTPException(404, "That upload has expired — choose the .zip again.")
        _uploads.pop(token, None)
    return u


# ---------------------------------------------------------------- the checks
def open_zip(path: str) -> zipfile.ZipFile:
    try:
        z = zipfile.ZipFile(path)
    except (zipfile.BadZipFile, OSError):
        raise ZipRefused("This isn't a .zip file.")
    infos = z.infolist()
    if len(infos) > MAX_MEMBERS:
        raise ZipRefused(f"This .zip has more than {MAX_MEMBERS} entries — split it into smaller ones.")
    if sum(i.file_size for i in infos) > MAX_UNPACKED:
        raise ZipRefused(f"This .zip unpacks to more than {MAX_UNPACKED // 1024 ** 3} GB.")
    return z


def clean_parts(name: str) -> list[str] | None:
    """A member's path as cleaned folder / file names, or None when it must be skipped (zip-slip, hidden, …)."""
    n = name.replace("\\", "/")
    if n.startswith("/") or re.match(r"^[A-Za-z]:", n):
        return None
    parts = [p for p in n.split("/") if p not in ("", ".")]
    if not parts or any(p == ".." for p in parts):
        return None
    if any(p.startswith(".") or p == "__MACOSX" for p in parts):
        return None
    cleaned = [paths.clean_name(p, "") for p in parts]
    if any(not c for c in cleaned) or len(cleaned) > MAX_DEPTH:
        return None
    return cleaned


def check_member(info: zipfile.ZipInfo) -> str | None:
    """Why a member is skipped, or None."""
    if info.flag_bits & 0x1:
        return "password-protected"
    mode = (info.external_attr >> 16) & 0o170000
    if mode == stat.S_IFLNK:
        return "a link"
    if not info.is_dir() and info.file_size > 10 * 1024 * 1024 and info.compress_size and \
            info.file_size / max(1, info.compress_size) > MAX_RATIO:
        return "unpacks too much (a possible zip bomb)"
    if clean_parts(info.filename) is None:
        return "an unsafe or hidden name"
    return None


def read_member(z: zipfile.ZipFile, info: zipfile.ZipInfo, limit: int) -> bytes:
    """A member's bytes, never more than it declares (or `limit`)."""
    cap = min(info.file_size, limit)
    with z.open(info) as f:
        data = f.read(cap + 1)
    if len(data) > cap:
        raise ZipRefused("A file in the .zip is bigger than it says.")
    return data


def copy_member(z: zipfile.ZipFile, info: zipfile.ZipInfo, out, limit: int) -> int:
    size = 0
    cap = min(info.file_size, limit)
    with z.open(info) as f:
        while True:
            chunk = f.read(1024 * 1024)
            if not chunk:
                break
            size += len(chunk)
            if size > cap:
                raise ZipRefused("A file in the .zip is bigger than it says.")
            out.write(chunk)
    return size


# ---------------------------------------------------------------- a .zip of files
def zip_preview(path: str, name: str | None) -> dict:
    with open_zip(path) as z:
        files_, folders, size, skipped = 0, set(), 0, []
        for i in z.infolist():
            why = check_member(i)
            if why:
                skipped.append({"name": i.filename[:200], "why": why})
                continue
            parts = clean_parts(i.filename)
            if i.is_dir():
                folders.add(tuple(parts))
                continue
            files_ += 1
            size += i.file_size
            for k in range(1, len(parts)):
                folders.add(tuple(parts[:k]))
    stem = paths.split_ext(paths.clean_name((name or "Imported").replace("\\", "/").rsplit("/", 1)[-1], "Imported"))[0]
    return {"files": files_, "folders": len(folders), "bytes": size, "skipped": skipped[:50], "skippedCount": len(skipped),
            "folderName": stem or "Imported"}


def _import_zip(user: dict, u: dict, parent_ref: str | None, job: dict) -> dict:
    with db.get_conn() as conn:
        _root, _folder, _role = docops.target_folder(conn, user, parent_ref)
        top = docops.create(conn, user, "folder", u["preview"]["folderName"], parent_ref)
    made: dict = {(): top}
    added, skipped = 0, 0
    with open_zip(u["path"]) as z:
        infos = z.infolist()
        job["total"] = sum(1 for i in infos if not i.is_dir())
        limit = None
        with db.get_conn() as conn:
            limit = files.upload_limit(conn)
        for i in infos:
            if check_member(i):
                skipped += 1 if not i.is_dir() else 0
                continue
            parts = clean_parts(i.filename)
            folder_parts = tuple(parts if i.is_dir() else parts[:-1])
            fid = _ensure_folders(user, made, folder_parts)
            if i.is_dir():
                continue
            if i.file_size > limit:
                skipped += 1
                job["done"] += 1
                continue
            try:
                _save_member(user, z, i, fid, parts[-1], limit)
                added += 1
            except (HTTPException, ZipRefused, OSError) as e:
                logger.info("Skipped a file while importing a .zip: %s", getattr(e, "detail", e))
                skipped += 1
                if isinstance(e, HTTPException) and e.status_code in (423, 507):
                    raise
            job["done"] += 1
    return {"folderId": top, "added": added, "skipped": skipped}


def _ensure_folders(user: dict, made: dict, parts: tuple) -> str:
    for k in range(1, len(parts) + 1):
        key = parts[:k]
        if key in made:
            continue
        parent = made[key[:-1]]
        with db.get_conn() as conn:
            existing = conn.execute(f"SELECT id FROM nodes n WHERE n.parent_id = ? AND n.kind = 'folder' AND n.name = ? "
                                    f"AND {nodes.LIVE}", (parent, key[-1])).fetchone()
            made[key] = existing["id"] if existing else docops.create(conn, user, "folder", key[-1], parent)
    return made[parts]


def _save_member(user: dict, z, info, folder_id: str, name: str, limit: int) -> str:
    prep = files.prepare_upload(user, folder_id, name, info.file_size)
    fd, tmp = files.new_temp(prep)
    try:
        with os.fdopen(fd, "wb") as f:
            size = copy_member(z, info, f, limit)
            f.flush()
            os.fsync(f.fileno())
    except BaseException:
        _remove(tmp)
        raise
    return files.finish_upload(user, prep, tmp, size, "keep")


# ---------------------------------------------------------------- Google Keep
def _usec_iso(v) -> str | None:
    try:
        return datetime.fromtimestamp(int(v) / 1e6, timezone.utc).isoformat(timespec="seconds")
    except (TypeError, ValueError, OverflowError, OSError):
        return None


def keep_notes(z: zipfile.ZipFile) -> list[dict]:
    """The Keep notes in a Takeout .zip: (member, parsed JSON) for every JSON that looks like a Keep note."""
    infos = z.infolist()
    jsons = [i for i in infos if i.filename.lower().endswith(".json") and not check_member(i)]
    keepish = [i for i in jsons if "/keep/" in "/" + i.filename.replace("\\", "/").lower()]
    out = []
    for i in (keepish or jsons):
        if i.file_size > MAX_JSON:
            continue
        try:
            note = json.loads(read_member(z, i, MAX_JSON).decode("utf-8", "replace"))
        except (ValueError, ZipRefused):
            continue
        if not isinstance(note, dict) or not ("textContent" in note or "listContent" in note):
            continue
        out.append({"member": i, "note": note})
        if len(out) >= MAX_NOTES:
            break
    return out


def keep_id(item: dict) -> str:
    n = item["note"]
    created = n.get("createdTimestampUsec")
    if isinstance(created, (int, str)) and str(created).isdigit():
        return f"c{created}"
    return "f" + item["member"].filename.replace("\\", "/").rsplit("/", 1)[-1][:200]


def keep_preview(user: dict, path: str) -> dict:
    with open_zip(path) as z:
        notes = keep_notes(z)
    if not notes:
        raise ZipRefused("No Google Keep notes were found in this .zip. In Google Takeout choose Keep, then upload "
                         "the .zip it gives you.")
    with db.get_conn() as conn:
        done = {r["keep_id"] for r in conn.execute("SELECT keep_id FROM keep_imports WHERE user_id = ?", (user["id"],))}
    c = {"notes": 0, "checklists": 0, "archived": 0, "trashed": 0, "pinned": 0, "attachments": 0, "already": 0,
         "empty": 0}
    labels = set()
    for it in notes:
        n = it["note"]
        if n.get("isTrashed"):
            c["trashed"] += 1
            continue
        if keep_id(it) in done:
            c["already"] += 1
            continue
        if not _has_content(n):
            c["empty"] += 1
            continue
        c["checklists" if isinstance(n.get("listContent"), list) and n.get("listContent") else "notes"] += 1
        c["archived"] += bool(n.get("isArchived"))
        c["pinned"] += bool(n.get("isPinned"))
        c["attachments"] += len([a for a in n.get("attachments") or [] if isinstance(a, dict)])
        for lb in n.get("labels") or []:
            if isinstance(lb, dict) and isinstance(lb.get("name"), str):
                labels.add(lb["name"])
    c["labels"] = sorted(labels, key=str.casefold)[:50]
    c["toImport"] = c["notes"] + c["checklists"]
    return c


def _has_content(n: dict) -> bool:
    return bool((n.get("title") or "").strip() or (n.get("textContent") or "").strip() or n.get("listContent")
                or n.get("attachments"))


def _note_title(n: dict) -> str:
    title = (n.get("title") or "").strip()
    if not title:
        text = (n.get("textContent") or "").strip()
        if not text and isinstance(n.get("listContent"), list) and n["listContent"]:
            text = str((n["listContent"][0] or {}).get("text") or "")
        title = text.split("\n", 1)[0].strip()[:60]
    if not title:
        when = _usec_iso(n.get("createdTimestampUsec"))
        title = f"Keep note {when[:10]}" if when else "Keep note"
    return paths.clean_name(title, "Keep note")


def _note_body(n: dict) -> tuple[str, bytes]:
    items = n.get("listContent")
    if isinstance(items, list) and items:
        lines = []
        for it in items:
            if not isinstance(it, dict):
                continue
            text = " ".join(str(it.get("text") or "").split()) or "…"
            lines.append(f"- [{'x' if it.get('isChecked') else ' '}] {text}")
        return "checklist", ("\n".join(lines) + "\n").encode("utf-8")
    return "note", (n.get("textContent") or "").replace("\r\n", "\n").encode("utf-8")


def _keep_folder(user: dict, archived: bool, made: dict) -> str:
    key = "archive" if archived else "top"
    if key in made:
        return made[key]
    with db.get_conn() as conn:
        root = docops.my_root(conn, user)
        top = conn.execute(f"SELECT id FROM nodes n WHERE n.root_id = ? AND n.parent_id IS NULL AND n.kind = 'folder' "
                           f"AND n.name_folded = ? AND {nodes.LIVE}", (root["id"], KEEP_FOLDER.casefold())).fetchone()
        top_id = top["id"] if top else docops.create(conn, user, "folder", KEEP_FOLDER, None)
        made["top"] = top_id
        if archived:
            sub = conn.execute(f"SELECT id FROM nodes n WHERE n.parent_id = ? AND n.kind = 'folder' AND n.name_folded = ? "
                               f"AND {nodes.LIVE}", (top_id, ARCHIVE_FOLDER.casefold())).fetchone()
            made["archive"] = sub["id"] if sub else docops.create(conn, user, "folder", ARCHIVE_FOLDER, top_id)
    return made[key]


def _set_times(conn, nid: str, user_id: str, edited: str | None, created: str | None) -> None:
    node = nodes.get(conn, nid)
    if node is None:
        return
    _root, real = nodes.real_path(conn, node)
    if edited:
        t = config.parse_iso(edited).timestamp()
        try:
            os.utime(real, (t, t), follow_symlinks=False)
        except (OSError, NotImplementedError):
            pass
        st = os.stat(real, follow_symlinks=False)
        nodes.update_stat(conn, nid, st, node["sha256"], updated_by=user_id)
    if created:
        conn.execute("UPDATE nodes SET ctime = ? WHERE id = ?", (created, nid))


def _import_keep(user: dict, u: dict, job: dict) -> dict:
    made: dict = {}
    added, skipped, attached = 0, 0, 0
    with open_zip(u["path"]) as z:
        notes = keep_notes(z)
        by_name = {i.filename.replace("\\", "/"): i for i in z.infolist()}
        job["total"] = len(notes)
        with db.get_conn() as conn:
            done = {r["keep_id"] for r in conn.execute("SELECT keep_id FROM keep_imports WHERE user_id = ?", (user["id"],))}
            limit = files.upload_limit(conn)
        for it in notes:
            job["done"] += 1
            n = it["note"]
            kid = keep_id(it)
            if n.get("isTrashed") or kid in done or not _has_content(n):
                skipped += 1
                continue
            folder = _keep_folder(user, bool(n.get("isArchived")), made)
            kind, body = _note_body(n)
            edited = _usec_iso(n.get("userEditedTimestampUsec"))
            created = _usec_iso(n.get("createdTimestampUsec"))
            with db.get_conn() as conn:
                nid = docops.create(conn, user, kind, _note_title(n), folder, body)
                _set_times(conn, nid, user["id"], edited, created)
                labels = []
                for lb in n.get("labels") or []:
                    if isinstance(lb, dict) and isinstance(lb.get("name"), str):
                        try:
                            labels.append(tags.clean_tag(lb["name"]))
                        except HTTPException:
                            pass
                colour = KEEP_COLOURS.get(str(n.get("color") or "").upper())
                if labels or colour:
                    tags.change(conn, user, nid, add=labels[:tags.MAX_TAGS], color=colour if colour else "keep")
                if n.get("isPinned"):
                    conn.execute("INSERT INTO user_state (user_id, node_id, favourite) VALUES (?, ?, 1) "
                                 "ON CONFLICT(user_id, node_id) DO UPDATE SET favourite = 1", (user["id"], nid))
                conn.execute("INSERT OR IGNORE INTO keep_imports (user_id, keep_id, node_id, imported_at) VALUES (?, ?, ?, ?)",
                             (user["id"], kid, nid, config.now_iso()))
                done.add(kid)
            added += 1
            base = it["member"].filename.replace("\\", "/").rsplit("/", 1)[0] if "/" in it["member"].filename.replace("\\", "/") else ""
            for a in n.get("attachments") or []:
                if not isinstance(a, dict) or not isinstance(a.get("filePath"), str):
                    continue
                fp = a["filePath"].replace("\\", "/").rsplit("/", 1)[-1]
                info = by_name.get(f"{base}/{fp}" if base else fp)
                if info is None or check_member(info) or info.file_size > limit:
                    continue
                try:
                    _save_member(user, z, info, folder, paths.clean_name(fp, "attachment"), limit)
                    attached += 1
                except (HTTPException, ZipRefused, OSError) as e:
                    logger.info("Skipped a Keep attachment: %s", getattr(e, "detail", e))
    return {"folderId": made.get("top"), "added": added, "skipped": skipped, "attachments": attached}


# ---------------------------------------------------------------- jobs
def start(user: dict, kind: str, token: str, parent_ref: str | None = None) -> dict:
    u = _take(user, token, kind)
    if kind == "zip":
        with db.get_conn() as conn:                     # may they add things there? (checked again inside)
            docops.target_folder(conn, user, parent_ref)
    job_id = secrets.token_hex(8)
    job = {"id": job_id, "user": user["id"], "kind": kind, "state": "running", "done": 0, "total": 0, "at": time.monotonic()}
    with _lock:
        _jobs[job_id] = job

    def run():
        try:
            res = _import_keep(user, u, job) if kind == "keep" else _import_zip(user, u, parent_ref, job)
            job.update(state="done", result=res)
        except HTTPException as e:
            job.update(state="failed", error=str(e.detail))
        except ZipRefused as e:
            job.update(state="failed", error=str(e))
        except Exception:
            logger.exception("An import failed")
            job.update(state="failed", error="The import stopped with an error — what was imported so far stays.")
        finally:
            job["at"] = time.monotonic()
            _remove(u["path"])
    threading.Thread(target=run, name=f"import-{job_id}", daemon=True).start()
    return public(job)


def public(job: dict) -> dict:
    return {k: v for k, v in job.items() if k not in ("user", "at")}


def job(user: dict, job_id: str) -> dict:
    with _lock:
        j = _jobs.get(job_id)
    if j is None or j["user"] != user["id"]:
        raise HTTPException(404, "That import isn't known (any more).")
    return public(j)


def wait(job_id: str, timeout: float = 30) -> dict:
    """Tests: wait for a job to finish."""
    t0 = time.monotonic()
    while time.monotonic() - t0 < timeout:
        with _lock:
            j = _jobs.get(job_id)
        if j and j["state"] != "running":
            return j
        time.sleep(0.02)
    raise TimeoutError(job_id)
