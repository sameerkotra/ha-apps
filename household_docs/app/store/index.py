"""Keeping the index right (SPEC §5.5).

- Walk every root every `scan_minutes` (housekeeping), on Rescan now, and one folder (a cheap scandir of that
  folder only) whenever it is opened. Unchanged entries (same size + mtime + inode) aren't re-read. Depth ≤ 10,
  50 000 entries per root; hidden entries and symbolic links are skipped.
- New file → new node (`created_by` NULL = "added outside the app"). Changed outside (size/mtime/hash differ)
  → content re-indexed, `updated_by` NULL.
- Renamed or moved outside → matched within the same root only by (dev, inode) — a folder also by the same
  name or modification time, a file by the same modification time and size or the same SHA-256, so a reused
  inode isn't mistaken for it — and, when the inode changed (copied and deleted), by size + SHA-256 with the
  same name or the same folder; the node keeps its id, shares, ticks, favourites and versions. Something that
  turns up in another root is always new there (`_match`).
- Deleted outside → `gone_at` set; it comes back if the file does (same place, or matched as above); after
  30 days the node and its shares go (`purge_gone`).
- Opening a document stats the file first (`refresh_node`); a change is picked up before it is read.

While the documents folder isn't usable (or an admin shared folder's storage is missing) nothing is touched.
"""
import logging
import os
import threading
import time

from .. import config, db, settings
from . import kinds, marker, nodes, paths, roots
from .fileio import sha256_file

logger = logging.getLogger("index")

MAX_DEPTH = 10
MAX_ENTRIES = 50_000
HASH_LIMIT = 64 * 1024 * 1024
GONE_DAYS = 30
MATCH_HOURS = 24                  # how long a node marked gone can still be matched to a file that turns up
_locks: dict[str, threading.Lock] = {}
_locks_guard = threading.Lock()


def _root_lock(root_id: str) -> threading.Lock:
    with _locks_guard:
        lk = _locks.get(root_id)
        if lk is None:
            lk = _locks[root_id] = threading.Lock()
        return lk


class Entry:
    __slots__ = ("rel", "name", "is_dir", "st", "real")

    def __init__(self, rel, name, is_dir, st, real):
        self.rel, self.name, self.is_dir, self.st, self.real = rel, name, is_dir, st, real


def _scandir(folder_real: str, rel: str) -> list[Entry]:
    out = []
    try:
        it = os.scandir(folder_real)
    except OSError:
        return out
    with it:
        for e in it:
            if e.name.startswith("."):
                continue
            try:
                if e.is_symlink():
                    continue
                is_dir = e.is_dir(follow_symlinks=False)
                if not is_dir and not e.is_file(follow_symlinks=False):
                    continue
                if is_dir and marker.foreign_store(e.path):     # another app's store: never indexed (§9.3)
                    continue
                st = e.stat(follow_symlinks=False)
            except OSError:
                continue
            out.append(Entry(paths.join_rel(rel, e.name), e.name, is_dir, st, e.path))
    return out


def walk(root_real: str) -> tuple[list[Entry], bool]:
    """Every entry under the root, parents before children; (entries, capped)."""
    out: list[Entry] = []
    stack = [("", root_real, 0)]
    capped = False
    while stack:
        rel, real, depth = stack.pop()
        level = _scandir(real, rel)
        level.sort(key=lambda e: e.name)
        for e in level:
            if len(out) >= MAX_ENTRIES:
                capped = True
                break
            out.append(e)
            if e.is_dir and depth + 1 < MAX_DEPTH:
                stack.append((e.rel, e.real, depth + 1))
        if capped:
            break
    out.sort(key=lambda e: (e.rel.count("/"), e.rel))
    return out, capped


def _content_limit(conn) -> int:
    return int(settings.get("content_index_mb", conn)) * 1024 * 1024


def read_file_info(path: str, name: str, st, previous_kind: str | None, limit: int):
    """(kind, sha256, searchable text, search columns) of a file on disk."""
    kind = kinds.kind_for_file(path, name, st.st_size, previous_kind)
    sha = None
    if st.st_size <= HASH_LIMIT:
        try:
            sha = sha256_file(path)
        except OSError:
            sha = None
    body = kinds.index_text_for(kind, path, name, st.st_size, limit)
    return kind, sha, body, kinds.stats_for(kind, path, st.st_size, limit)


def _changed(row, st) -> bool:
    return (row["mtime_ns"] != st.st_mtime_ns or row["inode"] != st.st_ino
            or (row["kind"] != "folder" and row["size"] != st.st_size))


def _recently_gone(c) -> bool:
    """A node marked gone may still be matched for MATCH_HOURS (a move seen by a one-folder scan first); after
    that an inode or a copy that turns up is something new (inodes are reused)."""
    if c["gone_at"] is None:
        return True
    return c["gone_at"] >= config.ago_iso(hours=MATCH_HOURS)


def _match(conn, root_id: str, e: Entry, missing: dict, limit: int):
    """A node this new entry is (renamed or moved outside), or None.

    Only nodes of the same root are candidates — a file that turns up in another person's folder or in an admin
    shared folder is always new there, so shares, versions, tags and activity never cross owners or roots. The
    candidate must be missing from its place (seen missing in this scan, or marked gone in the last
    MATCH_HOURS), and must plausibly be the same thing:
    - by inode: a folder with the same name, the same modification time, or still holding an entry the index
      knows inside it (same name and inode); a file with the same modification time and size, or the same
      content (SHA-256);
    - by content alone (the inode changed: copied, then deleted): the same SHA-256 and size, and the same name or
      the same folder.
    """
    st = e.st
    cands = conn.execute("SELECT * FROM nodes WHERE root_id = ? AND dev = ? AND inode = ? AND trash_id IS NULL",
                         (root_id, st.st_dev, st.st_ino)).fetchall()
    sha = None
    for c in cands:
        if not (c["id"] in missing or (c["gone_at"] is not None and _recently_gone(c))
                or (c["gone_at"] is None and not _exists_at(conn, c))):
            continue
        if (c["kind"] == "folder") != e.is_dir:
            continue
        if e.is_dir:
            if c["name"] == e.name or c["mtime_ns"] == st.st_mtime_ns or _same_children(conn, c, e.real):
                return c
            continue
        if c["mtime_ns"] == st.st_mtime_ns and c["size"] == st.st_size:
            return c
        if c["sha256"] and c["size"] == st.st_size and st.st_size <= HASH_LIMIT:
            if sha is None:
                try:
                    sha = sha256_file(e.real)
                except OSError:
                    return None
            if sha == c["sha256"]:
                return c
    if e.is_dir or st.st_size > HASH_LIMIT or st.st_size == 0:
        return None
    if sha is None:
        try:
            sha = sha256_file(e.real)
        except OSError:
            return None
    pid = _parent_id(conn, root_id, e.rel)
    for c in conn.execute("SELECT * FROM nodes WHERE root_id = ? AND sha256 = ? AND size = ? AND trash_id IS NULL "
                          "AND kind != 'folder'", (root_id, sha, st.st_size)).fetchall():
        if not (c["name"] == e.name or c["parent_id"] == pid):
            continue
        if c["id"] in missing or (c["gone_at"] is not None and _recently_gone(c)) \
                or (c["gone_at"] is None and not _exists_at(conn, c)):
            return c
    return None


def _same_children(conn, cand, real: str) -> bool:
    """A renamed folder still holds (some of) the same entries: a name and inode the index knows inside the
    candidate. A reused inode — a new, unrelated folder — doesn't."""
    known = {(r["name"], r["inode"]) for r in conn.execute(
        "SELECT name, inode FROM nodes WHERE parent_id = ? AND trash_id IS NULL", (cand["id"],))}
    if not known:
        return False
    try:
        with os.scandir(real) as it:
            for n, ent in enumerate(it):
                if n > 1000:
                    break
                try:
                    if (ent.name, ent.inode()) in known:
                        return True
                except OSError:
                    continue
    except OSError:
        return False
    return False


def _exists_at(conn, row) -> bool:
    try:
        _root, real = nodes.real_path(conn, row)
    except Exception:
        return True                  # can't tell (storage away): never steal it
    return os.path.lexists(real)


def _parent_id(conn, root_id: str, rel: str):
    prel = paths.parent_rel(rel)
    if not prel:
        return None
    p = nodes.by_rel(conn, root_id, prel)
    return p["id"] if p is not None else None


def _apply(conn, root, entries: list[Entry], scope_rows: list, now: str) -> dict:
    """Reconcile `entries` (what is on disk) with `scope_rows` (what the index has for the same place)."""
    root_id = root["id"]
    limit = _content_limit(conn)
    stats = {"new": 0, "changed": 0, "moved": 0, "gone": 0, "back": 0}
    events: list = []                       # (action, node id, detail) for 🕑 Activity (§17.4)
    on_disk = {e.rel: e for e in entries}
    by_rel = {r["rel"]: r for r in scope_rows}
    missing = {r["id"]: r for r in scope_rows if r["rel"] not in on_disk and r["gone_at"] is None}
    for e in entries:                       # parents first
        row = nodes.by_rel(conn, root_id, e.rel)
        if row is not None and row["trash_id"] is None:
            if (row["kind"] == "folder") != e.is_dir:
                nodes.delete_rows(conn, [row["id"]] + nodes.descendant_ids(conn, row))
                row = None
        if row is not None and row["trash_id"] is None:
            back = row["gone_at"] is not None
            pid = _parent_id(conn, root_id, e.rel)
            if back or row["parent_id"] != pid:
                conn.execute("UPDATE nodes SET gone_at = NULL, parent_id = ? WHERE id = ?", (pid, row["id"]))
                stats["back"] += back
            missing.pop(row["id"], None)
            if _changed(row, e.st) or back:
                if e.is_dir:
                    nodes.update_stat(conn, row["id"], e.st, None, updated_by=row["updated_by"], kind="folder")
                else:
                    kind, sha, body, cols = read_file_info(e.real, e.name, e.st, row["kind"], limit)
                    who = row["updated_by"] if (sha is not None and sha == row["sha256"]) else None
                    nodes.update_stat(conn, row["id"], e.st, sha, updated_by=who, kind=kind, body=body, set_body=True,
                                      stats=cols)
                    stats["changed"] += who is None
                    if who is None and not back:
                        events.append(("edited", row["id"], None))
            continue
        if row is not None:                 # a trashed row still on this rel (older data): let it go
            nodes.free_rel(conn, root_id, e.rel)
        cand = _match(conn, root_id, e, missing, limit)
        pid = _parent_id(conn, root_id, e.rel)
        if cand is not None:
            nodes.free_rel(conn, root_id, e.rel)
            events.append(("moved" if paths.base_name(cand["rel"]) == e.name else "renamed", cand["id"],
                           _move_detail(conn, cand, e)))
            nodes.relocate(conn, cand, root_id=root_id, rel=e.rel, parent_id=pid)
            conn.execute("UPDATE nodes SET gone_at = NULL WHERE id = ?", (cand["id"],))
            missing.pop(cand["id"], None)
            fresh = nodes.get(conn, cand["id"], live=False)
            if _changed(fresh, e.st):
                if e.is_dir:
                    nodes.update_stat(conn, cand["id"], e.st, None, updated_by=fresh["updated_by"], kind="folder")
                else:
                    kind, sha, body, cols = read_file_info(e.real, e.name, e.st, fresh["kind"], limit)
                    who = fresh["updated_by"] if sha == fresh["sha256"] else None
                    nodes.update_stat(conn, cand["id"], e.st, sha, updated_by=who, kind=kind, body=body, set_body=True,
                                      stats=cols)
            stats["moved"] += 1
            continue
        if e.is_dir:
            nid = nodes.insert(conn, root_id=root_id, rel=e.rel, parent_id=pid, kind="folder", st=e.st)
        else:
            kind, sha, body, cols = read_file_info(e.real, e.name, e.st, None, limit)
            nid = nodes.insert(conn, root_id=root_id, rel=e.rel, parent_id=pid, kind=kind, st=e.st, sha=sha, body=body,
                               stats=cols)
        stats["new"] += 1
        events.append(("created", nid, None))
    for nid in list(missing):
        r = nodes.get(conn, nid, live=False)
        if r is None or r["gone_at"] is not None or r["trash_id"] is not None:
            continue
        if r["root_id"] == root_id and r["rel"] in on_disk:
            continue
        conn.execute("UPDATE nodes SET gone_at = ? WHERE id = ?", (now, nid))
        stats["gone"] += 1
        events.append(("deleted", nid, None))
    _report(conn, root, events)
    return stats


def _move_detail(conn, cand, e) -> dict:
    """{"from", "to"}: folder names for a move found on disk, file names for a rename."""
    if paths.base_name(cand["rel"]) != e.name:
        return {"from": cand["name"], "to": e.name}             # (activity.record adds who could see the old name)
    old = conn.execute("SELECT name FROM nodes WHERE id = ?", (cand["parent_id"],)).fetchone() if cand["parent_id"] else None
    new_parent = paths.base_name(paths.parent_rel(e.rel)) if paths.parent_rel(e.rel) else ""
    return {"from": old["name"] if old else "", "to": new_parent, "fromId": cand["parent_id"] or "",
            "toId": _parent_id(conn, cand["root_id"], e.rel) or ""}


def _report(conn, root, events: list) -> None:
    """Changes found on disk → 🕑 Activity ("outside the app"). Not for the first scan of a folder (that is
    everything already there), and at most INDEX_MAX_EVENTS per scan."""
    if not events or root["last_scan_at"] is None:
        return
    from .. import activity
    if len(events) > activity.INDEX_MAX_EVENTS:
        logger.info("%s changes found outside the app in %s; Activity lists the first %s.", len(events), root["label"],
                    activity.INDEX_MAX_EVENTS)
    for action, nid, detail in events[:activity.INDEX_MAX_EVENTS]:
        activity.record_id(conn, action, None, nid, detail=detail)


def scan_root(root_id: str) -> dict | None:
    """Walk one root and bring its index up to date. None when the root can't be scanned now."""
    if db.RESTORING.is_set():
        return None
    with _root_lock(root_id):
        t0 = time.monotonic()
        with db.get_conn() as conn:
            root = roots.root_row(conn, root_id)
            if root is None:
                return None
            try:
                real = roots.root_real(root)
            except roots.FolderMissing:
                conn.execute("UPDATE roots SET missing = 1 WHERE id = ?", (root_id,))
                return None
            except (roots.DocsUnavailable, paths.PathError):
                return None
            if not os.path.isdir(real):
                conn.execute("UPDATE roots SET missing = 1 WHERE id = ?", (root_id,))
                return None
        entries, capped = walk(real)
        if capped:
            logger.warning("Folder %s has more than %s entries; only the first are indexed.", root["label"], MAX_ENTRIES)
        with db.get_conn() as conn:
            scope = conn.execute("SELECT * FROM nodes WHERE root_id = ? AND trash_id IS NULL", (root_id,)).fetchall()
            if capped:      # don't call what wasn't reached "gone"
                reached = {e.rel for e in entries}
                scope = [r for r in scope if r["rel"] in reached]
            stats = _apply(conn, root, entries, scope, config.now_iso())
            ms = int((time.monotonic() - t0) * 1000)
            conn.execute("UPDATE roots SET last_scan_at = ?, last_scan_ms = ?, missing = 0 WHERE id = ?",
                         (config.now_iso(), ms, root_id))
        stats["capped"] = capped
        return stats


def scan_folder(root_id: str, folder_rel: str) -> dict | None:
    """One folder only (when it's opened): its direct entries."""
    if db.RESTORING.is_set():
        return None
    with _root_lock(root_id):
        with db.get_conn() as conn:
            root = roots.root_row(conn, root_id)
            if root is None:
                return None
            try:
                base = roots.root_real(root)
                real = paths.resolve(base, folder_rel)
                roots.check_unmarked(base, real)
            except (roots.DocsUnavailable, roots.FolderMissing, paths.PathError):
                return None
            if not os.path.isdir(real):
                return None
            entries = _scandir(real, folder_rel)
            entries.sort(key=lambda e: e.rel)
            if folder_rel:
                parent = nodes.by_rel(conn, root_id, folder_rel)
                if parent is None:
                    return None
                scope = conn.execute("SELECT * FROM nodes WHERE parent_id = ? AND trash_id IS NULL", (parent["id"],)).fetchall()
            else:
                scope = conn.execute("SELECT * FROM nodes WHERE root_id = ? AND parent_id IS NULL AND trash_id IS NULL",
                                     (root_id,)).fetchall()
            return _apply(conn, root, entries, scope, config.now_iso())


def refresh_node(node_id: str):
    """Stat a node's file before it is opened; pick up a change made outside the app. Returns the fresh row, or
    None when it is gone (after looking in its folder for a rename)."""
    with db.get_conn() as conn:
        row = nodes.get(conn, node_id)
        if row is None:
            return None
        try:
            _root, real = nodes.real_path(conn, row)
        except paths.PathError:
            return None
        root_id, parent_rel = row["root_id"], paths.parent_rel(row["rel"])
        st = nodes.stat_quietly(real)
        if st is not None and not os.path.islink(real) and (os.path.isdir(real) == (row["kind"] == "folder")):
            if _changed(row, st) and row["kind"] != "folder":
                kind, sha, body, cols = read_file_info(real, row["name"], st, row["kind"], _content_limit(conn))
                who = row["updated_by"] if sha == row["sha256"] else None
                nodes.update_stat(conn, row["id"], st, sha, updated_by=who, kind=kind, body=body, set_body=True,
                                  stats=cols)
                if who is None:
                    from .. import activity
                    activity.record_id(conn, "edited", None, row["id"])
            return nodes.get(conn, node_id)
    scan_folder(root_id, parent_rel)
    with db.get_conn() as conn:
        return nodes.get(conn, node_id)


def scan_all() -> dict:
    """Every root (person roots only while the documents folder is usable)."""
    out = {}
    if db.RESTORING.is_set():
        return out
    ok = roots.is_ok()
    with db.get_conn() as conn:
        ids = [r["id"] for r in conn.execute("SELECT id, kind FROM roots") if ok or r["kind"] != "person"]
    for rid in ids:
        try:
            out[rid] = scan_root(rid)
        except Exception:
            logger.exception("Scanning a folder failed")
    return out


def purge_gone(days: int = GONE_DAYS) -> int:
    """Nodes not found on disk for `days` go for good (with their shares)."""
    cutoff = config.ago_iso(days=days)
    with db.get_conn() as conn:
        ids = [r["id"] for r in conn.execute("SELECT id FROM nodes WHERE gone_at IS NOT NULL AND gone_at < ? "
                                             "AND trash_id IS NULL", (cutoff,))]
        nodes.delete_rows(conn, ids)
    return len(ids)
