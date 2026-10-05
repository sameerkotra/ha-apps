"""Path rules (SPEC §9.3): every path a request names is a root plus a relative path, joined, normalised,
`realpath`-resolved and checked to be inside the root's own realpath — on every request, so a folder later
swapped for a symlink is refused. Hidden entries (a leading ".") are never named or listed; file names
must be ones Windows can hold too (people open these files over Samba).

Standard library only; nothing here touches the database. (Household Chat checks its shared folders the
same way; this module is the candidate for a shared `common/python/share_paths.py` once both use it.)
"""
import os
import re
import unicodedata

MAX_NAME = 200                   # characters; well under the 255 bytes most file systems allow
_BAD_CHARS = set('/\\:*?"<>|')
_RESERVED = re.compile(r"^(con|prn|aux|nul|com[0-9]|lpt[0-9])(\..*)?$", re.I)


class PathError(ValueError):
    """A name or path that can't be used; the message says why (shown to the person)."""


def name_problem(name) -> str | None:
    """Why `name` can't be a file or folder name, or None when it can."""
    if not isinstance(name, str) or not name.strip():
        return "A name can't be empty."
    if len(name) > MAX_NAME:
        return f"A name can be at most {MAX_NAME} characters."
    if name in (".", "..") or name.startswith("."):
        return "A name can't start with a dot (those files are hidden)."
    bad = sorted({c for c in name if c in _BAD_CHARS})
    if bad:
        return "A name can't contain " + " ".join(bad) + " (Windows can't hold those)."
    if any(ord(c) < 32 or ord(c) == 127 for c in name):
        return "A name can't contain control characters."
    if name != name.rstrip(" ."):
        return "A name can't end with a space or a dot."
    if name != name.lstrip(" "):
        return "A name can't start with a space."
    if _RESERVED.match(name):
        return f"“{name}” is a name Windows reserves for devices."
    return None


def check_name(name) -> str:
    """`name` (NFC-normalised) or PathError."""
    if isinstance(name, str):
        name = unicodedata.normalize("NFC", name)
    problem = name_problem(name)
    if problem:
        raise PathError(problem)
    return name


def clean_name(text: str, fallback: str = "Untitled") -> str:
    """Any text → a usable name: unusable characters become "_", no leading dots, no trailing dots or spaces,
    reserved names get "_" added. Used for people's folder names."""
    t = unicodedata.normalize("NFC", str(text or ""))
    t = "".join("_" if (c in _BAD_CHARS or ord(c) < 32 or ord(c) == 127) else c for c in t)
    t = t.strip().lstrip(".").rstrip(" .").strip()[:MAX_NAME].rstrip(" .")
    if not t:
        t = fallback
    if _RESERVED.match(t):
        t = t + "_"
    return t


def split_rel(rel: str) -> list[str]:
    """"a/b/c" → ["a", "b", "c"]; refuses empty parts, ".", ".." and hidden parts."""
    rel = (rel or "").replace("\\", "/").strip("/")
    if not rel:
        return []
    parts = rel.split("/")
    for p in parts:
        if p in ("", ".", "..") or p.startswith("."):
            raise PathError("That path isn't allowed.")
    return parts


def join_rel(*parts: str) -> str:
    return "/".join(p.strip("/") for p in parts if p and p.strip("/"))


def parent_rel(rel: str) -> str:
    return rel.rsplit("/", 1)[0] if "/" in rel else ""


def base_name(rel: str) -> str:
    return rel.rsplit("/", 1)[-1]


def inside(base_real: str, path_real: str) -> bool:
    """Is `path_real` the folder `base_real` or inside it (both already realpath'd)?"""
    return path_real == base_real or path_real.startswith(base_real.rstrip(os.sep) + os.sep)


def resolve(root_real: str, rel: str, *, must_exist: bool = False) -> str:
    """The real path of `rel` inside the root whose realpath is `root_real`. Raises PathError when it isn't
    inside (`..`, an absolute path, a symlink pointing out, a hidden part)."""
    parts = split_rel(rel)
    joined = os.path.join(root_real, *parts) if parts else root_real
    real = os.path.realpath(joined)
    if not inside(root_real, real):
        raise PathError("That path isn't inside its folder.")
    if must_exist and not os.path.lexists(real):
        raise PathError("Not found.")
    return real


def is_hidden(name: str) -> bool:
    return name.startswith(".")


def split_ext(name: str) -> tuple[str, str]:
    """"Budget.xlsx" → ("Budget", "xlsx"); a folder-like name without a dot → (name, "")."""
    stem, dot, ext = name.rpartition(".")
    if not dot or not stem:
        return name, ""
    return stem, ext.lower()


def unique_name(folder_real: str, name: str, taken=(), suffix_word: str = "") -> str:
    """`name`, or "name (2).ext", "name (3).ext" … (or "name (restored).ext", "name (restored 2).ext" with
    suffix_word="restored") — the first one that doesn't exist in the folder (case-insensitively, as on
    Windows shares) and isn't in `taken`."""
    try:
        existing = {n.casefold() for n in os.listdir(folder_real)}
    except OSError:
        existing = set()
    existing |= {t.casefold() for t in taken}
    if name.casefold() not in existing:
        return name
    stem, ext = split_ext(name)
    dot = "." + ext if ext else ""
    n = 1 if suffix_word else 2
    while True:
        label = (suffix_word + (f" {n}" if n > 1 else "")) if suffix_word else str(n)
        cand = f"{stem} ({label}){dot}"
        if cand.casefold() not in existing:
            return cand
        n += 1
