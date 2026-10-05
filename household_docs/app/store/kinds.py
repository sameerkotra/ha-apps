"""What each file is (SPEC §5.2): the node-kind registry.

Every node has a kind: folder, note (.txt), markdown (.md that isn't a pure task list), checklist (.md task
list), sheet (.xlsx/.csv, step 5) or file (anything else). A kind says which extensions it has, whether it is
a *document* (opened in the app's editor, gets versions, counts for `max_doc_mb`), what a new one starts as,
and how its text reaches the search index. Later steps register more (sheets) with `register(Kind(...))`;
nothing else needs to change for the index, versions, trash or search to handle them.
"""
import os
from dataclasses import dataclass, field
from typing import Callable, Optional

from ..formats import checklist_md, text as text_fmt

# Plain-text files whose words are searchable (SPEC §10.2), besides the documents themselves.
TEXT_EXTS = {"txt", "md", "csv", "json", "yaml", "yml", "log", "xml"}


@dataclass
class Kind:
    id: str
    label: str
    exts: tuple = ()                    # extensions (lower case, no dot); the first one is used for new files
    document: bool = False              # opened in the app's editor; versions kept (§7.3)
    creatable: bool = False             # offered under ➕ New
    new_content: bytes = b""
    icon: str = "📄"
    # (ext, read() -> bytes, previous kind) -> bool: does a file with one of `exts` belong to this kind?
    # None: the extension decides.
    detect: Optional[Callable] = None
    # (path, size, limit_bytes) -> text for the search index, or None
    index_text: Optional[Callable] = None
    # text -> {column: value} kept on the node for search filters (e.g. a checklist's open / ticked items)
    stats: Optional[Callable] = None
    # (path, size, limit_bytes) -> (text or None, {column: value}): for kinds whose files aren't text (sheets)
    # — one call gives both the searchable text and the search columns (cached per file version)
    info: Optional[Callable] = None
    # ext -> bytes: what a new file of this kind holds (else `new_content`)
    new_file: Optional[Callable] = None
    extra: dict = field(default_factory=dict)


_KINDS: dict[str, Kind] = {}
_ORDER: list[str] = []


def register(kind: Kind) -> Kind:
    if kind.id not in _KINDS:
        _ORDER.append(kind.id)
    _KINDS[kind.id] = kind
    return kind


def get(kind_id: str) -> Kind:
    return _KINDS[kind_id]


def all_kinds() -> list[Kind]:
    return [_KINDS[k] for k in _ORDER]


def is_document(kind_id: str) -> bool:
    k = _KINDS.get(kind_id)
    return bool(k and k.document)


def _read_limited(path: str, limit: int) -> bytes:
    from .fileio import open_read
    with open_read(path) as f:
        return f.read(limit)


def plain_index_text(path: str, size: int, limit: int) -> str | None:
    if limit <= 0 or size > limit:
        return None
    try:
        return text_fmt.index_text(_read_limited(path, limit))
    except OSError:
        return None


def checklist_index_text(path: str, size: int, limit: int) -> str | None:
    t = plain_index_text(path, size, limit)
    if t is None:
        return None
    try:
        return "\n".join(it.text for it in checklist_md.parse(t))
    except ValueError:
        return t


def _md_is(kind_id):
    def detect(ext, read, previous):
        try:
            data = read()
        except OSError:
            return previous == kind_id
        return checklist_md.md_kind(text_fmt.index_text(data), previous) == kind_id
    return detect


def checklist_stats(text: str) -> dict:
    try:
        items = checklist_md.parse(text)
    except ValueError:
        return {}
    done = sum(1 for it in items if it.done)
    return {"check_open": len(items) - done, "check_done": done}


register(Kind("folder", "Folder", icon="📁", creatable=True))
register(Kind("note", "Note", exts=("txt",), document=True, creatable=True, icon="📝",
              index_text=plain_index_text))
register(Kind("checklist", "Checklist", exts=("md",), document=True, creatable=True, icon="☑️",
              detect=_md_is("checklist"), index_text=checklist_index_text, stats=checklist_stats))
register(Kind("markdown", "Markdown note", exts=("md",), document=True, creatable=True, icon="📝",
              detect=_md_is("markdown"), index_text=plain_index_text))


def sheet_info(path: str, size: int, limit: int):
    """A sheet's searchable text (what every filled cell shows, all tabs) and, per line, the cell it came from
    (`sheet_cells`, so a search result opens the sheet at that cell). .xlsx files are read by the worker."""
    from ..formats import sheet_csv, sheet_model, sheet_xlsx
    if limit <= 0 or size > limit:
        return None, {}
    data = _read_limited(path, limit + 1)
    if len(data) > limit:
        return None, {}
    try:
        if path.lower().endswith(".csv"):
            sheet = sheet_csv.read(data)["sheet"]
        else:
            sheet = sheet_xlsx.read(data)["sheet"]
    except Exception:
        return None, {}
    body, refs = sheet_model.index_text(sheet)
    import json
    return body, {"sheet_cells": json.dumps(refs, separators=(",", ":"))}


def new_sheet(ext: str) -> bytes:
    from ..formats import sheet_model, sheet_xlsx
    if ext == "csv":
        return b""
    return sheet_xlsx.write(sheet_model.empty_sheet())


register(Kind("sheet", "Sheet", exts=("xlsx", "csv"), document=True, creatable=True, icon="📊",
              info=sheet_info, new_file=new_sheet))
register(Kind("file", "File", icon="📎"))

DETECT_READ_LIMIT = 2 * 1024 * 1024      # a .md bigger than this is a Markdown note (no task-list check)


def kind_for_file(path: str, name: str, size: int | None, previous: str | None = None) -> str:
    """The kind of the file at `path` called `name` (the registry's first match, else 'file')."""
    ext = os.path.splitext(name)[1].lower().lstrip(".")
    for k in all_kinds():
        if k.id == "folder" or ext not in k.exts:
            continue
        if k.detect is None:
            return k.id
        if size is not None and size > DETECT_READ_LIMIT:
            if k.id == "markdown":
                return k.id
            continue
        if k.detect(ext, lambda: _read_limited(path, DETECT_READ_LIMIT), previous):
            return k.id
    return "file"


_info_cache: dict = {}


def _info(k: Kind, path: str, size: int, limit: int):
    """k.info(path, size, limit), once per file version (the index asks for the text and the columns)."""
    try:
        st = os.stat(path)
        key = (path, st.st_size, st.st_mtime_ns, limit)
    except OSError:
        return None, {}
    hit = _info_cache.get(key)
    if hit is None:
        try:
            hit = k.info(path, st.st_size, limit)
        except Exception:
            hit = (None, {})
        if len(_info_cache) > 64:
            _info_cache.clear()
        _info_cache[key] = hit
    return hit


def index_text_for(kind_id: str, path: str, name: str, size: int, limit: int) -> str | None:
    """The searchable text of a file (None: names only)."""
    k = _KINDS.get(kind_id)
    if k and k.info:
        return _info(k, path, size, limit)[0]
    if k and k.index_text:
        return k.index_text(path, size, limit)
    ext = os.path.splitext(name)[1].lower().lstrip(".")
    if ext in TEXT_EXTS:
        return plain_index_text(path, size, limit)
    return None


STAT_COLUMNS = ("check_open", "check_done", "preview", "sheet_cells")
RASTER_EXTS = ("png", "jpg", "jpeg", "gif", "webp")


def raster_type(head: bytes) -> str | None:
    """The image type from a file's first bytes: PNG, JPEG, GIF or WebP only (SVG, HTML … never)."""
    if head.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if head.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if head.startswith((b"GIF87a", b"GIF89a")):
        return "image/gif"
    if len(head) >= 12 and head[:4] == b"RIFF" and head[8:12] == b"WEBP":
        return "image/webp"
    return None


def stats_for_text(kind_id: str, text: str | None) -> dict:
    """The node columns a kind keeps for search (every STAT_COLUMNS key, None when it doesn't apply)."""
    out = dict.fromkeys(STAT_COLUMNS)
    k = _KINDS.get(kind_id)
    if k and k.stats and text is not None:
        out.update(k.stats(text))
    return out


def stats_for(kind_id: str, path: str, size: int, limit: int) -> dict:
    if kind_id == "file":                  # previewable? (only real raster images, whatever the name says)
        out = dict.fromkeys(STAT_COLUMNS)
        try:
            out["preview"] = raster_type(_read_limited(path, 16))
        except OSError:
            pass
        return out
    k = _KINDS.get(kind_id)
    if k and k.info:
        out = dict.fromkeys(STAT_COLUMNS)
        out.update(_info(k, path, size, limit)[1])
        return out
    if not (k and k.stats) or size > max(limit, DETECT_READ_LIMIT):
        return dict.fromkeys(STAT_COLUMNS)
    try:
        return stats_for_text(kind_id, text_fmt.index_text(_read_limited(path, max(limit, DETECT_READ_LIMIT))))
    except OSError:
        return dict.fromkeys(STAT_COLUMNS)
