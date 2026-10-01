"""Shared folders (SPEC §12): an admin shares an existing folder in Home Assistant's
/share into any chats — groups, direct chats, personal rooms, several at once — each read-only or
read-write, and members browse it from the chat. New files (found by a scan every 5 minutes) are
announced in each chat that has announcements on (not in personal rooms, which get no system messages).

`folders` holds one row per folder; `folder_links` one per (folder, chat) with its access. Members only
ever name a link id, which ties the folder to a chat they must be in.

Every path a member sends is resolved under the mapped folder and `realpath`-checked, so `..` and
symlinks pointing elsewhere are refused. Hidden files and folders are skipped. The chat's own folder
(/share/household_chat) can't be mapped.
"""
import logging
import os
from datetime import datetime

from fastapi import HTTPException

from . import chats, config, db, files

logger = logging.getLogger("shared_folders")
MAX_SCAN_FILES = 5000
MAX_DEPTH = 8


def share_root() -> str:
    return os.path.realpath(config.SHARE_ROOT)


def check_mapping(rel: str) -> str:
    """A folder an admin may map: an existing directory inside /share, not /share itself, not the chat's folder."""
    rel = (rel or "").strip().strip("/")
    if not rel or any(p in ("", ".", "..") or p.startswith(".") for p in rel.split("/")):
        raise HTTPException(422, "Choose a folder inside /share.")
    root = share_root()
    full = os.path.realpath(os.path.join(root, rel))
    chat_root = os.path.realpath(files.configured())
    if not full.startswith(root + os.sep) or not os.path.isdir(full):
        raise HTTPException(422, "That folder doesn't exist in /share.")
    if full == chat_root or full.startswith(chat_root + os.sep) or chat_root.startswith(full + os.sep):
        raise HTTPException(422, "Household Chat's own folder can't be shared this way.")
    return os.path.relpath(full, root).replace(os.sep, "/")


def browse_share(rel: str = "") -> dict:
    """For the admin's folder picker: the folders (only) inside /share/<rel>."""
    root = share_root()
    rel = (rel or "").strip().strip("/")
    full = os.path.realpath(os.path.join(root, rel)) if rel else root
    if full != root and not full.startswith(root + os.sep):
        raise HTTPException(404, "No such folder.")
    if not os.path.isdir(full):
        raise HTTPException(404, "No such folder.")
    chat_root = os.path.realpath(files.configured())
    dirs = []
    for name in sorted(os.listdir(full), key=str.lower):
        p = os.path.join(full, name)
        if name.startswith(".") or not os.path.isdir(p) or os.path.realpath(p) == chat_root:
            continue
        dirs.append(name)
    return {"path": os.path.relpath(full, root).replace(os.sep, "/") if full != root else "", "folders": dirs[:500]}


LINK_SQL = ("SELECT l.id, l.folder_id, l.conversation_id, l.mode, f.path, f.label, f.announce, f.last_scan_at "
            "FROM folder_links l JOIN folders f ON f.id = l.folder_id")


def links_of(conn, conversation_id: str) -> list[dict]:
    """The folders shared into a chat, as members see them (the id is the link's)."""
    return [{"id": r["id"], "label": r["label"], "mode": r["mode"]} for r in conn.execute(
        LINK_SQL + " WHERE l.conversation_id = ? ORDER BY f.label COLLATE NOCASE", (conversation_id,))]


def folder_row(conn, link_id: str, user: dict):
    """The folder as shared into one chat, if you're in that chat (404 otherwise, admins included)."""
    f = conn.execute(LINK_SQL + " WHERE l.id = ?", (link_id,)).fetchone()
    if f is None or chats.member_row(conn, f["conversation_id"], user["id"]) is None:
        raise HTTPException(404, "That folder doesn't exist.")
    still_allowed(f)
    return f


def still_allowed(f) -> None:
    """Re-checked on every use: the mapped folder may have been replaced (say, by a symlink) since it was shared."""
    root = share_root()
    base = base_of(f)
    chat_root = os.path.realpath(files.configured())
    if (not base.startswith(root + os.sep) or base == chat_root or base.startswith(chat_root + os.sep)
            or chat_root.startswith(base + os.sep)):
        raise HTTPException(404, "That folder isn't available.")


def base_of(f) -> str:
    return os.path.realpath(os.path.join(share_root(), f["path"]))


def resolve_in(f, rel: str) -> str:
    base = base_of(f)
    rel = (rel or "").strip().lstrip("/")
    if any(p.startswith(".") for p in rel.split("/") if p):
        raise HTTPException(404, "Not found.")
    full = os.path.realpath(os.path.join(base, rel)) if rel else base
    if full != base and not full.startswith(base + os.sep):
        raise HTTPException(404, "Not found.")
    return full


def listing(f, rel: str) -> dict:
    full = resolve_in(f, rel)
    if not os.path.isdir(full):
        raise HTTPException(404, "That folder is gone.")
    base = base_of(f)
    entries = []
    for name in os.listdir(full):
        if name.startswith("."):
            continue
        p = os.path.join(full, name)
        real = os.path.realpath(p)
        if real != base and not real.startswith(base + os.sep):
            continue                          # a symlink out of the shared folder
        try:
            st = os.stat(p)
        except OSError:
            continue
        is_dir = os.path.isdir(p)
        ext = files.ext_of(name)
        entries.append({"name": name, "dir": is_dir, "size": None if is_dir else st.st_size,
                        "modified": config.iso(datetime.fromtimestamp(st.st_mtime, config.tz())),
                        "mime": None if is_dir else files.EXT_MIME.get(ext, "application/octet-stream")})
    entries.sort(key=lambda x: (not x["dir"], x["name"].lower()))
    return {"path": os.path.relpath(full, base).replace(os.sep, "/") if full != base else "", "entries": entries[:2000],
            "mode": f["mode"], "label": f["label"]}


def search(f, query: str, limit: int = 200) -> dict:
    """Files and folders anywhere in the shared folder whose name contains every word typed (case and
    accents ignored). Same walk rules as the scan: no hidden entries, no symlinks out, at most MAX_DEPTH
    levels and MAX_SCAN_FILES entries looked at. → {results: [...], truncated}."""
    import unicodedata

    def fold(s: str) -> str:
        return "".join(c for c in unicodedata.normalize("NFKD", s.casefold()) if not unicodedata.combining(c))
    words = [fold(w) for w in (query or "").split() if w.strip()][:8]
    base = base_of(f)
    if not words or not os.path.isdir(base):
        return {"results": [], "truncated": False}
    results, seen, truncated = [], 0, False
    for dirpath, dirnames, filenames in os.walk(base):
        rel_dir = os.path.relpath(dirpath, base)
        depth = 0 if rel_dir == "." else rel_dir.count(os.sep) + 1
        dirnames[:] = sorted([d for d in dirnames if not d.startswith(".") and depth < MAX_DEPTH
                              and not os.path.islink(os.path.join(dirpath, d))], key=str.lower)
        for name, is_dir in [(d, True) for d in dirnames] + [(n, False) for n in sorted(filenames, key=str.lower)]:
            if name.startswith(".") or name.endswith(".part"):
                continue
            seen += 1
            if seen > MAX_SCAN_FILES or len(results) >= limit:
                truncated = True
                break
            if not all(w in fold(name) for w in words):
                continue
            p = os.path.join(dirpath, name)
            real = os.path.realpath(p)
            if real != base and not real.startswith(base + os.sep):
                continue                      # a symlink out of the shared folder
            try:
                st = os.stat(p)
            except OSError:
                continue
            folder = "" if rel_dir == "." else rel_dir.replace(os.sep, "/")
            results.append({"name": name, "folder": folder, "dir": is_dir, "size": None if is_dir else st.st_size,
                            "modified": config.iso(datetime.fromtimestamp(st.st_mtime, config.tz())),
                            "mime": None if is_dir else files.EXT_MIME.get(files.ext_of(name), "application/octet-stream")})
        if truncated:
            break
    return {"results": results, "truncated": truncated}


def scan_one(conn, out, f) -> int:
    """Remember what's in the folder (a `folders` row); announce what's new since the last scan (not on the
    first) in every chat it's shared into, if announcements are on."""
    base = base_of(f)
    try:
        still_allowed(f)
    except HTTPException:
        return 0
    if not os.path.isdir(base):
        return 0
    found = {}
    for dirpath, dirnames, filenames in os.walk(base):
        rel_dir = os.path.relpath(dirpath, base)
        depth = 0 if rel_dir == "." else rel_dir.count(os.sep) + 1
        dirnames[:] = [d for d in dirnames if not d.startswith(".") and depth < MAX_DEPTH
                       and not os.path.islink(os.path.join(dirpath, d))]
        for name in filenames:
            if name.startswith(".") or name.endswith(".part"):
                continue
            p = os.path.join(dirpath, name)
            try:
                st = os.stat(p)
            except OSError:
                continue
            found[os.path.relpath(p, base).replace(os.sep, "/")] = (st.st_size, st.st_mtime)
            if len(found) >= MAX_SCAN_FILES:
                break
        if len(found) >= MAX_SCAN_FILES:
            break
    old = {r["rel"] for r in conn.execute("SELECT rel FROM folder_files WHERE folder_id = ?", (f["id"],))}
    new = sorted(set(found) - old)
    conn.execute("DELETE FROM folder_files WHERE folder_id = ?", (f["id"],))
    conn.executemany("INSERT INTO folder_files (folder_id, rel, size, mtime) VALUES (?, ?, ?, ?)",
                     [(f["id"], k, v[0], v[1]) for k, v in found.items()])
    conn.execute("UPDATE folders SET last_scan_at = ? WHERE id = ?", (config.now_iso(), f["id"]))
    if f["last_scan_at"] and new and f["announce"]:
        for link in conn.execute("SELECT id, conversation_id FROM folder_links WHERE folder_id = ?", (f["id"],)).fetchall():
            conv = chats.conv_row(conn, link["conversation_id"])
            if conv is not None:
                chats.add_system(conn, out, conv, {"event": "folder_files", "label": f["label"], "folder": link["id"],
                                                   "count": len(new), "names": [os.path.basename(x) for x in new[:5]]})
    return len(new)


def scan_all() -> int:
    n = 0
    with db.get_conn() as conn:
        ids = [r["id"] for r in conn.execute("SELECT id FROM folders")]
    for fid in ids:
        out = chats.Outbox()
        try:
            with db.get_conn() as conn:
                f = conn.execute("SELECT * FROM folders WHERE id = ?", (fid,)).fetchone()
                if f:
                    n += scan_one(conn, out, f)
            out.flush()
        except Exception:
            logger.exception("Scanning a shared folder failed")
    return n
