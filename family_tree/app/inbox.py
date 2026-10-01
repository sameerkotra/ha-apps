"""Inbox folder import (§13.10).

Drop photos or PDFs into `<photo folder>/inbox/` (from a PC over Samba, the
NAS, or a phone's file app). Every 2 minutes — and on *Check now* — files that
haven't changed for 30 seconds go through the normal upload pipeline
(magic-byte check, re-encoding, date taken, EXIF stripped) and land in the
**Unsorted** queue. The same file twice is skipped (SHA-256 of the original).
Rejected files move to `inbox/_rejected/` with a .txt note saying why. A
subfolder's name becomes the title prefix and a suggested person
("inbox/Venkat/…"). Each scan is one history batch, "Inbox". Nothing happens
while photo storage is offline or the Inbox folder switch (Features) is off.
"""
import asyncio
import logging
import os
import shutil
import time

from starlette.concurrency import run_in_threadpool

from . import config, db, features, media
from .history import Batch

logger = logging.getLogger("inbox")

INBOX = "inbox"
REJECTED = "_rejected"
STABLE_SECONDS = 30
INTERVAL_SECONDS = 120
SKIP_SUFFIXES = (".tmp", ".part", ".crdownload", ".partial", ".download", ".txt", ".ds_store", ".ini", ".db")

_sizes: dict[str, int] = {}


def inbox_dir() -> str:
    return os.path.join(media.root(), INBOX)


def _candidates(base: str):
    for dirpath, dirnames, files in os.walk(base):
        dirnames[:] = [d for d in dirnames if not d.startswith(("_", "."))]
        for fn in files:
            if fn.startswith((".", "~$")) or fn.lower().endswith(SKIP_SUFFIXES):
                continue
            yield os.path.join(dirpath, fn)


def _unique(path: str) -> str:
    base, ext = os.path.splitext(path)
    n = 1
    while os.path.exists(path):
        path = f"{base} ({n}){ext}"
        n += 1
    return path


def _reject(base: str, path: str, why: str) -> None:
    rel = os.path.relpath(path, base)
    dest = _unique(os.path.join(base, REJECTED, rel))
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    shutil.move(path, dest)
    with open(dest + ".txt", "w", encoding="utf-8") as f:
        f.write(f"Family Tree couldn't import {rel} ({config.now_iso()}):\n{why}\n")
    logger.info("Inbox: rejected %s — %s", rel, why)


def title_for(rel: str) -> str:
    parts = rel.replace("\\", "/").split("/")
    name = os.path.splitext(parts[-1])[0].replace("_", " ").strip()
    return (f"{' / '.join(parts[:-1])} — {name}" if len(parts) > 1 else name)[:200]


def scan_blocking(force: bool = False) -> dict:
    """One pass. `force` (Check now) still waits for files that changed in the last 30 s."""
    out = {"imported": 0, "rejected": 0, "duplicates": 0, "waiting": 0}
    if not features.on("inbox") or not media.is_online():
        return out
    base = inbox_dir()
    try:
        os.makedirs(base, exist_ok=True)
    except OSError:
        return out
    now = time.time()
    ready = []
    for path in _candidates(base):
        try:
            st = os.stat(path)
        except OSError:
            continue
        prev = _sizes.get(path)
        _sizes[path] = st.st_size
        if now - st.st_mtime < STABLE_SECONDS or (prev is not None and prev != st.st_size):
            out["waiting"] += 1
            continue
        ready.append(path)
    if not ready:
        return out
    stored = []                                 # (media id, processed, raw bytes, rel)
    for path in ready:
        rel = os.path.relpath(path, base).replace(os.sep, "/")
        try:
            with open(path, "rb") as f:
                data = f.read()
            ctype = media.validate_upload(data, allow_documents=True)
            sha = media.sha256(data)
            with db.get_conn() as conn:
                dup = conn.execute("SELECT id, title FROM media WHERE (orig_sha256 = ? OR sha256 = ?) AND deleted_at IS NULL",
                                   (sha, sha)).fetchone()
            if dup:
                _reject(base, path, f"It's already in Family Tree ({dup['title'] or 'untitled'}).")
                out["duplicates"] += 1
                continue
            if ctype == "application/pdf":
                processed = {"content_type": ctype, "original": data, "thumbs": {}, "width": None, "height": None,
                             "date_text": None, "document": True}
            else:
                processed = media.process_image(data)
            mid = db.new_id()
            if processed.get("document"):
                media.store_document(mid, data)
            else:
                media.store_image(mid, processed)
            stored.append((mid, processed, data, rel, path))
        except media.MediaError as e:
            _reject(base, path, str(e))
            out["rejected"] += 1
        except OSError as e:
            logger.warning("Inbox: couldn't read %s: %s", rel, e)
        finally:
            _sizes.pop(path, None)
    if not stored:
        return out
    try:
        with db.get_conn() as conn:
            b = Batch(conn, None, f"Inbox: {len(stored)} file{'s' if len(stored) != 1 else ''}")
            for mid, p, data, rel, _path in stored:
                b.insert("media", {"id": mid, "kind": "document" if p.get("document") else "photo",
                                   "title": title_for(rel), "date_text": p["date_text"], "content_type": p["content_type"],
                                   "size": len(p["original"]), "sha256": media.sha256(data), "width": p["width"],
                                   "height": p["height"], "created_by": None, "created_at": b.now, "unsorted": 1,
                                   "orig_name": rel[:300], "orig_sha256": media.sha256(data)})
    except Exception:
        for mid, *_ in stored:
            media.remove_files(mid)
        raise
    for _mid, _p, _d, rel, path in stored:
        try:
            os.remove(path)
        except OSError:
            logger.warning("Inbox: imported %s but couldn't remove it from the inbox", rel)
    out["imported"] = len(stored)
    logger.info("Inbox: imported %s file(s)", len(stored))
    return out


async def loop():
    while True:
        try:
            await run_in_threadpool(scan_blocking)
        except Exception:
            logger.exception("Inbox scan failed")
        await asyncio.sleep(INTERVAL_SECONDS)
