"""Admin shared folders (SPEC §9): an admin hands out an existing folder in /share — read only or read and write per
person, plus an Everyone row (people added later get it too). The folder becomes a root of kind 'shared'; its
files are indexed like anyone's documents, and access is the folder's (`root_access`), checked inside every query
(sharing.access_cte) and on every item (sharing.role_of). Admin shared folders have no owner and no per-item
sharing. Deleting puts things in the folder's own hidden `.trash`, versions go to its own `.versions`.

What can't be shared (checked when it's added; on every use (store/roots.root_real) for the folder itself and
the folders above it; a Chat or Docs store that turns up *inside* a shared folder later is left out by the
scanner, folder downloads and every file access (store/marker.foreign_store), and shown to admins as a problem):
- /share itself, anything outside it, hidden folders, a folder reached through a link (the real path is stored);
- the documents folder, anything inside it or containing it (that would show everyone's documents);
- Household Chat's files folder (its `.household_chat_store` marker), inside or containing it;
- another Household Docs documents folder (its `.household_docs` marker), inside or containing it;
- a folder inside or containing another admin shared folder (each file belongs to exactly one root).

*Stop sharing* forgets the folder in the app (its index rows, Trash entries and access); nothing on disk changes.
A folder that disappears (network storage not mounted) is shown with ⚠ to admins and hidden from everyone else.
"""
import os

from fastapi import HTTPException

from . import config, db, sharing
from .store import marker, nodes, paths, roots

MODES = ("none", "ro", "rw")
MAX_LABEL = 80


def _shared_rows(conn, exclude_id: str | None = None):
    return [r for r in conn.execute("SELECT * FROM roots WHERE kind = 'shared'").fetchall() if r["id"] != exclude_id]


def check(conn, path, exclude_id: str | None = None) -> dict:
    """Can this folder be an admin shared folder? {ok, verdict, message, path ("/share/…"), rel}. Changes
    nothing."""
    out = {"ok": False, "verdict": "refused", "message": "", "path": path if isinstance(path, str) else "", "rel": None}
    try:
        display = marker.clean_location(path)
    except paths.PathError as e:
        msg = str(e).replace("the documents folder", "a shared folder").replace("The documents folder", "A shared folder")
        out["message"] = msg
        return out
    share_real = config.share_root()
    real = os.path.realpath(config.share_abs(display))
    out["path"] = display
    if not paths.inside(share_real, real) or real == share_real:
        out["message"] = "That folder isn't inside /share (a link pointing elsewhere?)."
        return out
    rel_parts = os.path.relpath(real, share_real).split(os.sep)
    if any(p.startswith(".") for p in rel_parts):
        out["message"] = "A hidden folder (starting with a dot) can't be shared."
        return out
    if not os.path.isdir(real):
        out.update(verdict="missing", message="That folder doesn't exist in /share.")
        return out
    docs = roots.docs_configured_real()
    if paths.inside(docs, real) or paths.inside(real, docs):
        out.update(verdict="overlap_docs", message="That folder is the documents folder, inside it or contains it — "
                                                   "sharing it would show everyone's documents.")
        return out
    if marker.in_marked(real, share_real, marker.MARKER) or marker.contains_marked(real, marker.MARKER) \
            or os.path.isfile(os.path.join(real, marker.MARKER)):
        out.update(verdict="overlap_docs", message="That folder is (or holds) a Household Docs documents folder.")
        return out
    if marker._in_chat(real, share_real) or marker._contains_chat(real):
        out.update(verdict="in_chat", message="That's Household Chat's files folder (or it's inside it, or holds it). "
                                              "Share a folder of its own.")
        return out
    for r in _shared_rows(conn, exclude_id):
        other = os.path.normpath(os.path.join(share_real, r["path"]))
        if paths.inside(other, real) or paths.inside(real, other):
            out.update(verdict="overlap_shared", message=f"That folder overlaps the shared folder “{r['label']}” "
                                                         "(one is inside the other).")
            return out
    if not os.access(real, os.R_OK | os.X_OK):
        out.update(verdict="unreadable", message="The app can't read that folder.")
        return out
    out.update(ok=True, verdict="ok", rel=os.path.relpath(real, share_real).replace(os.sep, "/"),
               writable=os.access(real, os.W_OK),
               message="This folder can be shared." + ("" if os.access(real, os.W_OK)
                                                      else " (It's read-only for the app: Read and write won't work.)"))
    return out


def clean_label(label) -> str:
    if not isinstance(label, str) or not label.strip():
        raise HTTPException(422, "Give the shared folder a name.")
    label = " ".join(label.split())
    if len(label) > MAX_LABEL:
        raise HTTPException(422, f"A name can be at most {MAX_LABEL} characters.")
    return label


def _label_free(conn, label: str, exclude_id: str | None = None) -> None:
    r = conn.execute("SELECT id FROM roots WHERE kind = 'shared' AND label = ? COLLATE NOCASE", (label,)).fetchone()
    if r is not None and r["id"] != exclude_id:
        raise HTTPException(409, f"There's already a shared folder called “{label}”.")


def clean_access(conn, access) -> dict:
    """{userId | "*": "none" | "ro" | "rw"} → the rows to keep (no "none")."""
    if access is None:
        return {}
    if not isinstance(access, dict) or len(access) > 500:
        raise HTTPException(422, "Send access as {person id: none, ro or rw}.")
    out = {}
    for uid, mode in access.items():
        if mode not in MODES:
            raise HTTPException(422, "Access is none, ro (read only) or rw (read and write).")
        if uid != sharing.EVERYONE and conn.execute("SELECT 1 FROM users WHERE id = ?", (uid,)).fetchone() is None:
            raise HTTPException(404, "That person isn't known to Household Docs.")
        if mode != "none":
            out[uid] = mode
    return out


def _set_access(conn, root_id: str, access: dict) -> None:
    conn.execute("DELETE FROM root_access WHERE root_id = ?", (root_id,))
    for uid, mode in access.items():
        conn.execute("INSERT INTO root_access (root_id, user_id, mode) VALUES (?, ?, ?)", (root_id, uid, mode))


def create(conn, admin: dict, path, label, access, kids: bool = False) -> str:
    info = check(conn, path)
    if not info["ok"]:
        raise HTTPException(409 if info["verdict"] != "refused" else 422, info["message"])
    label = clean_label(label)
    _label_free(conn, label)
    acc = clean_access(conn, access)
    if conn.execute("SELECT 1 FROM roots WHERE path = ?", (info["rel"],)).fetchone():
        raise HTTPException(409, "That folder is shared already.")
    rid = db.new_id()
    conn.execute("INSERT INTO roots (id, kind, user_id, path, label, created_by, created_at, kids) "
                 "VALUES (?, 'shared', NULL, ?, ?, ?, ?, ?)", (rid, info["rel"], label, admin["id"], config.now_iso(),
                                                               1 if kids else 0))
    _set_access(conn, rid, acc)
    db.audit(conn, "shared_folder_added", admin["id"], None, rid)
    return rid


def row(conn, root_id: str):
    r = conn.execute("SELECT * FROM roots WHERE id = ? AND kind = 'shared'", (root_id,)).fetchone()
    if r is None:
        raise HTTPException(404, "That shared folder isn't there.")
    return r


def update(conn, admin: dict, root_id: str, label=None, access=None, kids=None) -> None:
    r = row(conn, root_id)
    if kids is not None:
        conn.execute("UPDATE roots SET kids = ? WHERE id = ?", (1 if kids else 0, root_id))
    if label is not None:
        label = clean_label(label)
        _label_free(conn, label, root_id)
        conn.execute("UPDATE roots SET label = ? WHERE id = ?", (label, root_id))
    if access is not None:
        _set_access(conn, root_id, clean_access(conn, access))
    db.audit(conn, "shared_folder_changed", admin["id"], None, r["id"])


def remove(conn, admin: dict, root_id: str) -> None:
    """Stop sharing: the app forgets the folder; nothing on disk changes."""
    r = row(conn, root_id)
    ids = [x["id"] for x in conn.execute("SELECT id FROM nodes WHERE root_id = ?", (root_id,))]
    nodes.delete_rows(conn, ids)
    conn.execute("DELETE FROM trash WHERE root_id = ?", (root_id,))
    conn.execute("DELETE FROM root_access WHERE root_id = ?", (root_id,))
    conn.execute("DELETE FROM roots WHERE id = ?", (root_id,))
    db.audit(conn, "shared_folder_removed", admin["id"], None, r["id"])


def problem(r) -> str | None:
    """Why a shared folder can't be used right now, or what is wrong with it (shown to admins), or None."""
    return _problems(r)[0]


def _problems(r) -> tuple[str | None, bool]:
    """(problem, usable): usable is False when the folder itself can't be used."""
    msg, real = _root_problem(r)
    if msg is not None:
        return msg, False
    if marker.contains_marked(real, marker.CHAT_MARKER):
        return ("Household Chat's files folder is inside this folder. The app leaves it out (nobody sees it here), "
                "but it's better to share a folder that doesn't hold it."), True
    if marker.contains_marked(real, marker.MARKER):
        return ("A Household Docs documents folder is inside this folder. The app leaves it out (nobody sees it "
                "here), but it's better to share a folder that doesn't hold it."), True
    return None, True


def _root_problem(r) -> tuple[str | None, str | None]:
    try:
        real = roots.root_real(r)
    except roots.FolderMissing:
        return "Not found — is the storage connected?", None
    except paths.PathError as e:
        return str(e), None
    except HTTPException as e:
        return str(e.detail), None
    return None, real


def admin_json(conn, r) -> dict:
    acc = {a["user_id"]: a["mode"] for a in conn.execute("SELECT * FROM root_access WHERE root_id = ?", (r["id"],))}
    c = conn.execute("SELECT COUNT(*), COALESCE(SUM(size), 0) FROM nodes WHERE root_id = ? AND gone_at IS NULL "
                     "AND trash_id IS NULL AND kind != 'folder'", (r["id"],)).fetchone()
    prob, usable = _problems(r)
    missing = 1 if prob is not None and prob.startswith("Not found") else 0
    if missing != r["missing"]:
        conn.execute("UPDATE roots SET missing = ? WHERE id = ?", (missing, r["id"]))
    return {"id": r["id"], "label": r["label"], "path": config.share_display(r["path"]), "exists": usable,
            "problem": prob, "access": {k: v for k, v in acc.items() if k != sharing.EVERYONE},
            "everyone": acc.get(sharing.EVERYONE, "none"), "files": c[0], "size": c[1], "kids": bool(r["kids"]),
            "lastScanAt": r["last_scan_at"], "lastScanMs": r["last_scan_ms"], "createdAt": r["created_at"]}


def admin_listing(conn) -> list[dict]:
    rows = conn.execute("SELECT * FROM roots WHERE kind = 'shared' ORDER BY label COLLATE NOCASE").fetchall()
    return [admin_json(conn, r) for r in rows]


def shared_reals(conn) -> dict:
    """realpath → label, for the folder browser."""
    share_real = config.share_root()
    return {os.path.normpath(os.path.join(share_real, r["path"])): r["label"] for r in _shared_rows(conn)}


def for_user(conn, user: dict, include_missing: bool = False) -> list[dict]:
    """The admin shared folders this person may use: {rootId, label, mode, exists}. Missing ones only for admins
    (with ⚠); everyone else doesn't see them."""
    out = []
    child = sharing.is_child_id(conn, user["id"])                 # children: Kids folders only (§17.20)
    for r in conn.execute("SELECT * FROM roots WHERE kind = 'shared' ORDER BY label COLLATE NOCASE").fetchall():
        if child and not r["kids"]:
            continue
        rank = conn.execute("SELECT MAX(CASE mode WHEN 'rw' THEN 2 ELSE 1 END) FROM root_access WHERE root_id = ? "
                            "AND user_id IN (?, '*')", (r["id"], user["id"])).fetchone()[0]
        if not rank:
            continue
        prob = _root_problem(r)[0]                 # (the walk for stores inside is for admins' page only)
        ok = prob is None
        missing = 1 if prob is not None and prob.startswith("Not found") else 0
        if missing != r["missing"]:
            conn.execute("UPDATE roots SET missing = ? WHERE id = ?", (missing, r["id"]))
        if not ok and not include_missing:
            continue
        out.append({"rootId": r["id"], "label": r["label"], "mode": "rw" if rank >= 2 else "ro", "exists": ok,
                    "kids": bool(r["kids"])})
    return out
