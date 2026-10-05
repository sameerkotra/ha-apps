"""Checking a documents-folder location (SPEC §5.6) and the folder browser over /share.

A documents folder is ours when it holds the marker `.household_docs` with this install's id. Rules for any
location, on choosing it, at start-up and every 5 minutes:

- new (doesn't exist, its parent does) or empty → usable: the marker, people/ and the hidden folders are made;
- ours → usable;
- refused, with the reason: /share itself, outside /share, a hidden folder, another install's marker, inside
  or containing an admin shared folder, inside Household Chat's files folder (its `.household_chat_store`
  marker), read-only, parent missing (storage not mounted), not a folder, or a folder that already has
  files but no marker (only the move of §5.7 may take such a folder, so files are never mixed by accident).

The app never copies, moves or deletes a documents folder as a whole; it writes the marker, people/ and its
own hidden folders only.
"""
import json
import os
import shutil

from .. import config
from . import paths

MARKER = ".household_docs"
CHAT_MARKER = ".household_chat_store"
FORMAT = 1
HIDDEN_DIRS = (".trash", ".versions", ".tmp")
PEOPLE = "people"

USABLE = ("new", "empty", "ours")


def read_marker(folder_real: str) -> dict | None:
    try:
        with open(os.path.join(folder_real, MARKER), encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else {"install": None}


def write_marker(folder_real: str, install_id: str, **extra) -> None:
    data = {"install": install_id, "format": FORMAT, "app": "Household Docs"}
    old = read_marker(folder_real) or {}
    if old.get("install") == install_id and old.get("created"):
        data["created"] = old["created"]
    else:
        data["created"] = config.now_iso()
    data.update(extra)
    tmp = os.path.join(folder_real, MARKER + ".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=1)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, os.path.join(folder_real, MARKER))


def clean_location(value) -> str:
    """A typed location → "/share/…" (normalised), or PathError with the reason."""
    if not isinstance(value, str) or not value.strip():
        raise paths.PathError("Choose a folder inside /share.")
    v = value.strip().replace("\\", "/")
    if not v.startswith("/"):
        v = "/share/" + v
    v = "/" + "/".join(p for p in v.split("/") if p)
    if v == config.SHARE_PREFIX:
        raise paths.PathError("/share itself can't be the documents folder — choose or type a folder inside it.")
    if not v.startswith(config.SHARE_PREFIX + "/"):
        raise paths.PathError("The documents folder must be inside /share.")
    rel = config.share_rel(v)
    for p in rel.split("/"):
        if p in (".", ".."):
            raise paths.PathError("That path isn't allowed.")
        if p.startswith("."):
            raise paths.PathError("A hidden folder (starting with a dot) can't be the documents folder.")
    if len(v) > 400:
        raise paths.PathError("That path is too long.")
    return v


def _visible_entries(folder_real: str) -> list[str]:
    try:
        return [n for n in os.listdir(folder_real) if n not in (MARKER, MARKER + ".tmp")]
    except OSError:
        return []


def in_marked(real: str, share_real: str, name: str) -> bool:
    """Is `real` a folder holding the marker file `name`, or inside one (looking up to /share)?"""
    cur = real
    while paths.inside(share_real, cur) and cur != share_real:
        if os.path.isfile(os.path.join(cur, name)):
            return True
        cur = os.path.dirname(cur)
    return False


def contains_marked(real: str, name: str, limit: int = 2000) -> bool:
    """Does a folder holding the marker file `name` sit somewhere inside `real`? (a bounded walk)"""
    n = 0
    for dirpath, dirnames, filenames in os.walk(real):
        if name in filenames and dirpath != real:
            return True
        dirnames[:] = [d for d in dirnames if not d.startswith(".")][:200]
        n += 1
        if n > limit:
            break
    return False


def foreign_store(folder_real: str) -> bool:
    """Is this folder another app's store — Household Chat's files folder (`.household_chat_store`) or a Household
    Docs documents folder (`.household_docs`)? The scanner, folder downloads and every file access skip such a
    folder (and everything inside it) when it turns up inside a root (SPEC §9.3)."""
    try:
        return (os.path.isfile(os.path.join(folder_real, CHAT_MARKER))
                or os.path.isfile(os.path.join(folder_real, MARKER)))
    except (OSError, ValueError):
        return True


def _in_chat(real: str, share_real: str) -> bool:
    """Is `real` Household Chat's files folder or inside it (a .household_chat_store marker on the way up)?"""
    return in_marked(real, share_real, CHAT_MARKER)


def _contains_chat(real: str, limit: int = 2000) -> bool:
    """Does a Household Chat files folder sit somewhere inside `real`? (a bounded walk)"""
    return contains_marked(real, CHAT_MARKER, limit) or os.path.isfile(os.path.join(real, CHAT_MARKER))


def check(display: str, install_id: str, shared_reals=(), *, current_display: str | None = None) -> dict:
    """What a location is, and whether it can be the documents folder. Never changes anything.
    shared_reals: the realpaths of the admin shared folders (overlap is refused)."""
    out = {"path": display, "verdict": None, "ok": False, "message": "", "exists": False, "files": 0,
           "current": bool(current_display) and display == current_display}
    try:
        display = clean_location(display)
    except paths.PathError as e:
        out.update(verdict="refused", message=str(e))
        return out
    out["path"] = display
    share_real = config.share_root()
    if not os.path.isdir(share_real):
        out.update(verdict="parent_missing", message="/share isn't available.")
        return out
    real = os.path.realpath(config.share_abs(display))
    if not paths.inside(share_real, real) or real == share_real:
        out.update(verdict="refused", message="That folder isn't inside /share (a link pointing elsewhere?).")
        return out
    out["real"] = real
    if _in_chat(real, share_real):
        out.update(verdict="in_chat", message="That folder is inside Household Chat's files folder. Choose a "
                                              "folder of its own.")
        return out
    for s in shared_reals:
        if paths.inside(s, real) or paths.inside(real, s):
            out.update(verdict="overlap", message="That folder overlaps an admin shared folder (one is inside the "
                                                  "other), which would show everyone's documents there.")
            return out
    parent = os.path.dirname(real)
    if not os.path.exists(real):
        if not os.path.isdir(parent):
            out.update(verdict="parent_missing", message=f"{config.share_display(os.path.relpath(parent, share_real))} "
                                                         "doesn't exist — is the storage connected?")
            return out
        if not os.access(parent, os.W_OK | os.X_OK):
            out.update(verdict="read_only", message="The app can't create a folder there (read-only).")
            return out
        out.update(verdict="new", ok=True, message="A new folder: the app creates it.")
        return out
    out["exists"] = True
    if not os.path.isdir(real):
        out.update(verdict="refused", message="That's a file, not a folder.")
        return out
    if not os.access(real, os.W_OK | os.X_OK):
        out.update(verdict="read_only", message="That folder is read-only for the app.")
        return out
    marker = read_marker(real)
    entries = _visible_entries(real)
    out["files"] = len(entries)
    if marker is not None:
        if marker.get("install") == install_id:
            out.update(verdict="ours", ok=True, message="This app's documents folder.")
        else:
            out.update(verdict="other_install", message="That folder belongs to another Household Docs (its "
                                                        f"{MARKER} file is from a different install).")
        return out
    if _contains_chat(real):
        out.update(verdict="in_chat", message="Household Chat's files folder is inside that folder.")
        return out
    if entries:
        out.update(verdict="has_files", message=f"That folder already has {len(entries)} item"
                   f"{'s' if len(entries) != 1 else ''} but no {MARKER} file. Choose an empty or new folder, so "
                   "documents are never mixed up with other files.")
        return out
    out.update(verdict="empty", ok=True, message="An empty folder.")
    return out


def setup(real: str, install_id: str) -> None:
    """Make a usable location ours: the folder (its parent must exist), the marker, people/ and the hidden
    folders. Safe to run on a folder that is already ours."""
    if not os.path.isdir(real):
        os.mkdir(real)
    if read_marker(real) is None:
        write_marker(real, install_id)
    ensure_layout(real)


def ensure_layout(real: str) -> None:
    for d in (PEOPLE,) + HIDDEN_DIRS:
        os.makedirs(os.path.join(real, d), exist_ok=True)


def free_space(real: str) -> int | None:
    try:
        return shutil.disk_usage(real).free
    except OSError:
        return None


def browse(rel: str = "", shared: dict | None = None) -> dict:
    """The folder browser over /share: the folders (only) inside /share/<rel>, hidden ones and links pointing
    out of /share left out. Each folder says whether it is a documents folder or Household Chat's, and (from
    `shared`: realpath → label) which admin shared folder it already is. Inside a documents folder or Chat's
    files folder nothing is listed (`sealed`): admins get no view of people's folder names (§3.2)."""
    share_real = config.share_root()
    try:
        real = paths.resolve(share_real, rel or "")
    except paths.PathError:
        raise paths.PathError("No such folder.")
    if not os.path.isdir(real):
        raise paths.PathError("No such folder.")
    folders = []
    here = os.path.relpath(real, share_real).replace(os.sep, "/") if real != share_real else ""
    sealed = ("docs" if in_marked(real, share_real, MARKER) else
              "chat" if in_marked(real, share_real, CHAT_MARKER) else None)
    if sealed is not None:        # what's inside another app's store (people's folder names …) isn't listed
        return {"path": config.share_display(here), "rel": here, "folders": [], "sealed": sealed,
                "note": ("This is a Household Docs documents folder — what's inside it isn't listed."
                         if sealed == "docs" else "This is Household Chat's files folder — what's inside it isn't listed.")}
    try:
        names = sorted(os.listdir(real), key=str.casefold)
    except OSError:
        names = []
    for name in names:
        if name.startswith("."):
            continue
        p = os.path.join(real, name)
        rp = os.path.realpath(p)
        if not os.path.isdir(rp) or not paths.inside(share_real, rp):
            continue
        folders.append({"name": name, "docs": os.path.isfile(os.path.join(rp, MARKER)),
                        "chat": os.path.isfile(os.path.join(rp, CHAT_MARKER)),
                        "shared": (shared or {}).get(rp)})
        if len(folders) >= 500:
            break
    return {"path": config.share_display(here), "rel": here, "folders": folders}
