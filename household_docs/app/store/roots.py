"""Roots (SPEC §5.3) and the documents folder's state (§5.6).

Two kinds of root, handled by the same code:
- person roots: each person's folder, `<docs folder>/people/<Folder>`; the owner is that person;
- shared roots: an admin shared folder anywhere else in /share (step 3); no owner, access per person.

Every path a request names is a root plus a relative path (store/paths.resolve), and a root's own location is
realpath-checked on every use: inside /share, and a person root inside the documents folder's people/.

The documents folder's state is checked at start-up, every 5 minutes (housekeeping) and when an admin
chooses a folder. While it isn't usable ("Documents folder not found — is the storage connected?") nothing
is written and the index isn't touched; reading the lists still works from the database.

Hidden folders per root (`hidden_dir`): person roots share the documents folder's `.trash`, `.versions` and
`.tmp`; an admin shared folder keeps its own inside itself (step 3).
"""
import logging
import os
import threading

from fastapi import HTTPException

from .. import config, db, settings
from . import marker, paths

logger = logging.getLogger("roots")

NOT_FOUND_MESSAGE = "Documents folder not found — is the storage connected?"
_lock = threading.RLock()
_state: dict = {"checked": False}
_listeners: list = []          # f(old_ok, new_ok) after a check changes the state (housekeeping rescans)


class DocsUnavailable(HTTPException):
    def __init__(self, message: str | None = None):
        super().__init__(503, message or _state.get("message") or NOT_FOUND_MESSAGE)


FOLDER_MISSING = "That shared folder isn't available right now — is its storage connected?"


class FolderMissing(HTTPException):
    """An admin shared folder whose folder isn't there (network storage not mounted)."""
    def __init__(self):
        super().__init__(404, FOLDER_MISSING)


# ---------- the install and the documents folder ----------
def install_id(conn) -> str:
    iid = db.state_get(conn, "install_id")
    if not iid:
        iid = db.new_id()
        db.state_set(conn, "install_id", iid)
    return iid


def configured(conn=None) -> str:
    return settings.get("docs_path", conn)


def is_confirmed(conn) -> bool:
    return bool(db.state_get(conn, "docs_confirmed", False))


def shared_reals(conn) -> list[str]:
    share_real = config.share_root()
    return [os.path.realpath(os.path.join(share_real, r["path"]))
            for r in conn.execute("SELECT path FROM roots WHERE kind = 'shared'")]


def check(allow_setup: bool = False) -> dict:
    """Check the documents folder now and remember the result. A usable new/empty location is set up while no
    admin has confirmed a location yet (the default is used straight away) or when `allow_setup`."""
    with _lock:
        with db.get_conn() as conn:
            path = configured(conn)
            iid = install_id(conn)
            confirmed = is_confirmed(conn)
            shared = shared_reals(conn)
        info = marker.check(path, iid, shared)
        verdict = info["verdict"]
        from . import moving
        quiet = moving.is_read_only()             # read-only mode while moving (§5.7): nothing is made
        try:
            if quiet:
                pass
            elif verdict in ("new", "empty") and (allow_setup or not confirmed):
                marker.setup(info["real"], iid)
                info = marker.check(path, iid, shared)
                verdict = info["verdict"]
            elif verdict == "ours":
                marker.ensure_layout(info["real"])
        except OSError as e:
            logger.warning("Setting up the documents folder %s failed: %s", path, e)
            info.update(verdict="read_only", ok=False, message=f"The app couldn't set up {path}: {e.strerror or e}.")
            verdict = "read_only"
        ok = verdict == "ours"
        if ok:
            message = ""
        elif confirmed and verdict in ("new", "parent_missing"):
            message = NOT_FOUND_MESSAGE
        else:
            message = f"The documents folder {path} can't be used: {info['message']}"
        old_ok = _state.get("ok")
        _state.clear()
        _state.update(checked=True, path=path, real=info.get("real") if ok else None, ok=ok, verdict=verdict,
                      message=message, confirmed=confirmed, detail=info["message"])
        if not ok:
            logger.warning("Documents folder: %s", message)
    if old_ok is not None and old_ok != ok:
        for fn in list(_listeners):
            try:
                fn(old_ok, ok)
            except Exception:
                logger.exception("A documents-folder listener failed")
    return status()


def status() -> dict:
    with _lock:
        if not _state.get("checked"):
            pass
        else:
            return {k: v for k, v in _state.items() if k != "real"}
    check()
    with _lock:
        return {k: v for k, v in _state.items() if k != "real"}


def reset() -> None:
    """Tests: forget the remembered state."""
    with _lock:
        _state.clear()
        _state["checked"] = False


def docs_real() -> str:
    """The documents folder's real path, or 503 when it isn't usable. Re-checked against /share every call."""
    with _lock:
        if not _state.get("checked"):
            pass
        elif _state.get("ok"):
            real = _state["real"]
            if os.path.isdir(real) and paths.inside(config.share_root(), os.path.realpath(real)):
                return real
    st = check()
    if not st["ok"]:
        raise DocsUnavailable(st["message"])
    return _state["real"]


def is_ok() -> bool:
    return bool(status().get("ok"))


def docs_display() -> str:
    return status()["path"]


# ---------- person roots ----------
def person_root(conn, user_id: str):
    return conn.execute("SELECT * FROM roots WHERE kind = 'person' AND user_id = ?", (user_id,)).fetchone()


def root_row(conn, root_id: str):
    return conn.execute("SELECT * FROM roots WHERE id = ?", (root_id,)).fetchone()


def _folder_name(conn, user) -> str:
    style = settings.get("folder_names", conn)
    base = user["username"] if style == "username" and user["username"] else user["name"]
    name = paths.clean_name(base or user["username"] or "Person", "Person")
    taken = {r["folder"].casefold() for r in conn.execute("SELECT folder FROM users WHERE folder IS NOT NULL AND id != ?",
                                                          (user["id"],))}
    if name.casefold() not in taken:
        return name
    if user["username"]:
        cand = paths.clean_name(f"{name} ({user['username']})")
        if cand.casefold() not in taken:
            return cand
    n = 2
    while f"{name} ({n})".casefold() in taken:
        n += 1
    return f"{name} ({n})"


def person_rel(folder: str) -> str:
    """The person root's path relative to /share for a folder name."""
    return paths.join_rel(config.share_rel(configured()), marker.PEOPLE, folder)


def ensure_person_root(conn, user_id: str):
    """The person's root, making their folder (on first visit) when the documents folder is usable. Returns the
    root row, or None while the documents folder isn't usable and they have none yet."""
    user = conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
    if user is None:
        return None
    root = person_root(conn, user_id)
    if root is not None and os.path.isdir(os.path.join(config.share_root(), root["path"])):
        return root
    try:
        base = docs_real()
    except DocsUnavailable:
        return root
    from . import moving
    if moving.is_read_only(conn):             # read-only mode while moving (§5.7): their folder is made later
        return root
    folder = user["folder"] or _folder_name(conn, user)
    people = os.path.join(base, marker.PEOPLE)
    os.makedirs(people, exist_ok=True)
    real = paths.resolve(people, folder)
    os.makedirs(real, exist_ok=True)
    if not user["folder"]:
        conn.execute("UPDATE users SET folder = ? WHERE id = ?", (folder, user_id))
    rel = person_rel(folder)
    if root is None:
        rid = db.new_id()
        conn.execute("INSERT INTO roots (id, kind, user_id, path, label, created_at) VALUES (?, 'person', ?, ?, ?, ?)",
                     (rid, user_id, rel, folder, config.now_iso()))
        db.audit(conn, "person_folder_created", user_id, None, rid)
        return root_row(conn, rid)
    if root["path"] != rel:
        conn.execute("UPDATE roots SET path = ?, label = ? WHERE id = ?", (rel, folder, root["id"]))
    return root_row(conn, root["id"])


def rename_person_folder(conn, user_id: str, new_name: str) -> str:
    """Admin → People: rename a person's folder on disk and in the index (node ids stay)."""
    new_name = paths.check_name(new_name)
    user = conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
    root = person_root(conn, user_id)
    if user is None or root is None or not user["folder"]:
        raise paths.PathError("That person has no folder yet (it's made when they first open the app).")
    if new_name == user["folder"]:
        return new_name
    from . import fileio
    fileio.ensure_writable()
    clash = conn.execute("SELECT 1 FROM users WHERE folder = ? COLLATE NOCASE AND id != ?", (new_name, user_id)).fetchone()
    people = os.path.join(docs_real(), marker.PEOPLE)
    dest = paths.resolve(people, new_name)
    if clash or (os.path.exists(dest) and os.path.realpath(dest) != os.path.realpath(paths.resolve(people, user["folder"]))):
        raise paths.PathError(f"There is already a folder called “{new_name}”.")
    src = paths.resolve(people, user["folder"])
    if os.path.isdir(src):
        os.rename(src, dest)
    else:
        os.makedirs(dest, exist_ok=True)
    conn.execute("UPDATE users SET folder = ? WHERE id = ?", (new_name, user_id))
    conn.execute("UPDATE roots SET path = ?, label = ? WHERE id = ?", (person_rel(new_name), new_name, root["id"]))
    return new_name


def docs_configured_real() -> str:
    """The configured documents folder's realpath (whether or not it is usable right now)."""
    return os.path.realpath(config.share_abs(configured()))


def root_real(root) -> str:
    """A root's real path, checked: inside /share; a person root inside the documents folder's people/.
    An admin shared folder (stored as its real path relative to /share) must still be exactly that: no link on
    the way (a folder swapped for a symlink is refused), not overlapping the documents folder, and there
    (FolderMissing when its storage is away). Raises DocsUnavailable (503) when the documents folder isn't
    usable (person roots), PathError when the root has been swapped for something pointing elsewhere."""
    share_real = config.share_root()
    joined = os.path.normpath(os.path.join(share_real, root["path"]))
    real = os.path.realpath(joined)
    if root["kind"] == "person":
        people = os.path.realpath(os.path.join(docs_real(), marker.PEOPLE))
        if not paths.inside(people, real) or real == people:
            raise paths.PathError("That folder isn't where it should be.")
        if marker.in_marked(real, share_real, marker.CHAT_MARKER) or marker.foreign_store(real):
            raise paths.PathError(IN_CHAT)
        return real
    if not paths.inside(share_real, real) or real == share_real:
        raise paths.PathError("That folder isn't inside /share.")
    if real != joined:
        raise paths.PathError("That shared folder has been replaced by a link — an admin needs to look at it.")
    docs = docs_configured_real()
    if paths.inside(docs, real) or paths.inside(real, docs):
        raise paths.PathError("That shared folder overlaps the documents folder — an admin needs to look at it.")
    if not os.path.isdir(real):
        raise FolderMissing()
    if marker.in_marked(real, share_real, marker.CHAT_MARKER):        # re-checked on every use (§9.3)
        raise paths.PathError(IN_CHAT)
    if marker.in_marked(real, share_real, marker.MARKER):
        raise paths.PathError(IN_DOCS)
    return real


IN_CHAT = ("That folder is Household Chat's files folder or inside it — it isn't shown here. "
           "An admin needs to look at it.")
IN_DOCS = ("That folder is a Household Docs documents folder or inside one — it isn't shown here. "
           "An admin needs to look at it.")


def check_unmarked(base_real: str, real: str) -> None:
    """No folder between a root (`base_real`, itself checked by root_real) and `real` is another app's store
    (Household Chat's files folder, another Household Docs documents folder): PathError when one is."""
    cur = real if os.path.isdir(real) and not os.path.islink(real) else os.path.dirname(real)
    while cur != base_real and paths.inside(base_real, cur):
        if marker.foreign_store(cur):
            raise paths.PathError("That item is inside another app's folder, so it isn't shown here.")
        cur = os.path.dirname(cur)


def hidden_dir(root, name: str) -> str:
    """The root's .trash / .versions / .tmp folder (made when missing)."""
    if root["kind"] == "person":
        base = docs_real()
    else:
        base = root_real(root)
    d = os.path.join(base, name)
    if os.path.islink(d) or not paths.inside(base, os.path.realpath(d)):
        raise paths.PathError(f"The {name} folder has been replaced by a link — an admin needs to look at it.")
    if not os.path.isdir(d):
        from . import moving
        if not moving.is_read_only():         # read-only mode while moving (§5.7): nothing is made
            os.makedirs(d, exist_ok=True)
    return d


def owner_of(root) -> str | None:
    return root["user_id"] if root["kind"] == "person" else None
