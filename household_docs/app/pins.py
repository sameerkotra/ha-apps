"""Pinned documents and Quick note (SPEC §17.13).

**Pins**: up to MAX_PINS documents or folders on a person's home page (`user_state.pinned_order`), in the order
they drag them into. Each card shows a preview: a checklist "3 of 12 done", a note's first lines, a folder's
item count, and a sheet's chosen cell (`user_state.pin_cell`). A pinned item the person can no longer open, or
that was deleted, is left off the page (and comes back if it does).

**Quick note**: ⚡ (and `N`, and the address `#quick-note`) makes `My docs/Inbox/Note YYYY-MM-DD HH-MM.txt` (Home
Assistant's time zone; " (2)" when that minute is taken) and opens it. When the person leaves it, `finish` names
it after its first line — only while it still has its quick name, so a rename by hand is kept. An empty quick
note goes to Trash when left.
"""
import json
import re

from fastapi import HTTPException

from . import config, db, docops, documents, settings, sharing
from .formats import text as text_fmt
from .store import fileio, moving, nodes, paths

MAX_PINS = 8
INBOX = "Inbox"
QUICK = re.compile(r"^Note \d{4}-\d{2}-\d{2} \d{2}-\d{2}( \(\d+\))?$")
NAME_MAX = 60


# ---------------------------------------------------------------- pins
def _pinned_rows(conn, user: dict):
    acc_sql, acc_params = sharing.access_cte(user["id"])
    return conn.execute(acc_sql + "SELECT n.*, us.pinned_order AS pin_order, us.pin_cell AS pin_cell, acc.rank AS acc_rank "
                        "FROM user_state us JOIN nodes n ON n.id = us.node_id JOIN acc ON acc.node_id = n.id "
                        "JOIN roots r ON r.id = n.root_id "
                        f"WHERE us.user_id = ? AND us.pinned_order IS NOT NULL AND {nodes.LIVE} "
                        "AND (r.kind = 'person' OR r.missing = 0) ORDER BY us.pinned_order, n.name_folded",
                        (*acc_params, user["id"])).fetchall()


def _count(conn, user: dict) -> int:
    return len(_pinned_rows(conn, user))


def clean_cell(cell) -> dict | None:
    if cell in (None, ""):
        return None
    if not isinstance(cell, dict) or not isinstance(cell.get("ref"), str) or not isinstance(cell.get("tab"), str):
        raise HTTPException(422, "A pinned cell is {tab, ref}.")
    ref = cell["ref"].strip().upper()
    if not re.fullmatch(r"[A-Z]{1,3}[1-9][0-9]{0,5}", ref) or len(cell["tab"]) > 31:
        raise HTTPException(422, "That isn't a cell reference (like B4).")
    return {"tab": cell["tab"], "ref": ref}


def set_pin(conn, user: dict, node_id: str, value: bool, cell=None) -> dict:
    node, _role = sharing.require(conn, user, node_id, "viewer")
    cur = conn.execute("SELECT pinned_order FROM user_state WHERE user_id = ? AND node_id = ?",
                       (user["id"], node_id)).fetchone()
    pinned = cur is not None and cur["pinned_order"] is not None
    if value:
        c = clean_cell(cell)
        if c and node["kind"] != "sheet":
            raise HTTPException(422, "Only a sheet's card shows a cell.")
        if not pinned:
            if _count(conn, user) >= MAX_PINS:
                raise HTTPException(409, f"You can pin up to {MAX_PINS} things — unpin one first.")
            r = conn.execute("SELECT COALESCE(MAX(pinned_order), -1) + 1 FROM user_state WHERE user_id = ?",
                             (user["id"],)).fetchone()[0]
            conn.execute("INSERT INTO user_state (user_id, node_id, pinned_order) VALUES (?, ?, ?) "
                         "ON CONFLICT(user_id, node_id) DO UPDATE SET pinned_order = excluded.pinned_order",
                         (user["id"], node_id, r))
        if cell is not None or not pinned:
            conn.execute("UPDATE user_state SET pin_cell = ? WHERE user_id = ? AND node_id = ?",
                         (json.dumps(c) if c else None, user["id"], node_id))
    elif pinned:
        conn.execute("UPDATE user_state SET pinned_order = NULL, pin_cell = NULL WHERE user_id = ? AND node_id = ?",
                     (user["id"], node_id))
    return {"pinned": bool(value), "count": _count(conn, user)}


def reorder(conn, user: dict, ids: list) -> None:
    if not isinstance(ids, list) or len(ids) > 50 or not all(isinstance(i, str) for i in ids):
        raise HTTPException(422, "Send the pinned items' ids in their new order.")
    current = [r["id"] for r in _pinned_rows(conn, user)]
    if set(ids) != set(current) or len(ids) != len(current):
        raise HTTPException(409, "Your pins changed meanwhile — reload the page.")
    for n, nid in enumerate(ids):
        conn.execute("UPDATE user_state SET pinned_order = ? WHERE user_id = ? AND node_id = ?", (n, user["id"], nid))


def _note_preview(conn, row) -> list[str]:
    try:
        _root, real = nodes.real_path(conn, row)
        with fileio.open_read(real) as f:
            head = f.read(4096)
        text, _m = text_fmt.decode(head)
    except Exception:
        return []
    out = []
    md = row["kind"] == "markdown"
    for line in text.splitlines():
        t = line.strip()
        if md:              # the words, not the marks
            t = re.sub(r"^(#{1,6}\s+|[-*+]\s+(\[[ xX]\]\s+)?|>\s*|\d+[.)]\s+)", "", t)
            t = re.sub(r"(\*\*|__|\*|`)", "", re.sub(r"\[\[([^\]]+)\]\]", r"\1", t)).strip()
            if re.fullmatch(r"[-*_|: ]*", t):
                continue
        if t:
            out.append(t[:120])
        if len(out) >= 3:
            break
    return out


def _cell_preview(conn, user: dict, row, cell: dict) -> dict:
    from . import sheets
    try:
        if (row["size"] or 0) > int(settings.get("max_doc_mb", conn)) * 1024 * 1024:
            return {"tab": cell["tab"], "ref": cell["ref"], "error": "too big"}
        _node, _role, _data, p = sheets.read_sheet(conn, user, row["id"])
    except Exception:
        return {"tab": cell["tab"], "ref": cell["ref"], "error": "unreadable"}
    tab = next((t for t in p["sheet"]["tabs"] if t.get("name") == cell["tab"] and t.get("kind") == "grid"), None)
    if tab is None:
        return {"tab": cell["tab"], "ref": cell["ref"], "error": "no such tab"}
    c = (tab.get("cells") or {}).get(cell["ref"]) or {}
    v = c.get("c", c.get("v")) if isinstance(c.get("v"), str) and c["v"].startswith("=") else c.get("v")
    return {"tab": cell["tab"], "ref": cell["ref"], "v": v, "f": c.get("f"), "d": c.get("d"), "red": c.get("red"),
            "label": _label_of(tab, cell["ref"])}


def _label_of(tab: dict, ref: str) -> str | None:
    """The text to the left of the cell (a row's label, like "Left this month"), if any."""
    m = re.fullmatch(r"([A-Z]+)(\d+)", ref)
    if not m or m.group(1) == "A":
        return None
    left = (tab.get("cells") or {}).get("A" + m.group(2)) or {}
    v = left.get("v")
    return v[:60] if isinstance(v, str) and not v.startswith("=") else None


def cards(conn, user: dict) -> list[dict]:
    from .routers.nodes import rows_json
    rows = _pinned_rows(conn, user)
    items = rows_json(conn, user, rows, role_for=lambda r: sharing.ROLE_OF_RANK.get(r["acc_rank"]))
    for it, r in zip(items, rows):
        prev: dict = {}
        if r["kind"] == "checklist":
            prev = {"open": r["check_open"] or 0, "done": r["check_done"] or 0}
        elif r["kind"] in ("note", "markdown"):
            prev = {"lines": _note_preview(conn, r)}
        elif r["kind"] == "folder":
            prev = {"items": conn.execute(f"SELECT COUNT(*) FROM nodes n WHERE n.parent_id = ? AND {nodes.LIVE}",
                                          (r["id"],)).fetchone()[0]}
        elif r["kind"] == "sheet" and r["pin_cell"]:
            try:
                cell = json.loads(r["pin_cell"])
            except ValueError:
                cell = None
            if cell:
                prev = {"cell": _cell_preview(conn, user, r, cell)}
        it["pinPreview"] = prev
    return items


# ---------------------------------------------------------------- Quick note
def _inbox(conn, user: dict, root) -> str:
    for r in nodes.children(conn, root["id"], None):
        if r["kind"] == "folder" and r["name"].casefold() == INBOX.casefold():
            return r["id"]
    return docops.create(conn, user, "folder", INBOX, None)


def quick_note(conn, user: dict) -> str:
    moving.guard()
    root = docops.my_root(conn, user)
    inbox = _inbox(conn, user, root)
    name = config.now().strftime("Note %Y-%m-%d %H-%M")
    return docops.create(conn, user, "note", name, inbox)


def name_from_text(text: str) -> str:
    """The first line with something in it, without Markdown marks, cut at a word to NAME_MAX characters."""
    for line in (text or "").splitlines():
        t = re.sub(r"^\s*(#{1,6}\s+|[-*+]\s+(\[[ xX]\]\s+)?|\d+[.)]\s+|>\s*)", "", line)
        t = re.sub(r"[*_`]+", "", t)
        t = re.sub(r"\[\[([^\]]+)\]\]", r"\1", t)
        t = " ".join(t.split())
        if t:
            if len(t) > NAME_MAX:
                cut = t[:NAME_MAX].rsplit(" ", 1)[0]
                t = cut if len(cut) >= 20 else t[:NAME_MAX]
            return paths.clean_name(t, "")
    return ""


def finish(conn, user: dict, node_id: str) -> dict:
    """Leaving a quick note: name it after its first line (or Trash it when empty)."""
    node, _role = sharing.require(conn, user, node_id, "editor")
    if node["kind"] not in documents.TEXT_KINDS:
        return {"id": node_id, "name": node["name"], "renamed": False}
    stem = paths.split_ext(node["name"])[0]
    if not QUICK.match(stem):
        return {"id": node_id, "name": node["name"], "renamed": False}
    _root, real, data, _etag, _st, _sha = documents._read(conn, node)
    try:
        text, _m = text_fmt.decode(data)
    except text_fmt.NotText:
        return {"id": node_id, "name": node["name"], "renamed": False}
    if not text.strip():
        docops.delete(conn, user, node_id)
        return {"id": node_id, "deleted": True}
    title = name_from_text(text)
    if not title or title.startswith("."):
        return {"id": node_id, "name": node["name"], "renamed": False}
    fileio.ensure_writable()
    import os
    folder_real = os.path.dirname(real)
    new_name = paths.unique_name(folder_real, f"{title}.{node['ext'] or 'txt'}", taken=())
    if new_name.casefold() == node["name"].casefold():
        return {"id": node_id, "name": node["name"], "renamed": False}
    try:
        docops.rename(conn, user, node_id, paths.split_ext(new_name)[0])
    except HTTPException:
        return {"id": node_id, "name": node["name"], "renamed": False}
    fresh = nodes.get(conn, node_id)
    db.audit(conn, "quick_note_named", user["id"], node_id, node["root_id"])
    return {"id": node_id, "name": fresh["name"], "renamed": True}
