"""Sheets (SPEC §7.2, §8): open, save changed cells with per-cell conflicts, export, import, Convert to .xlsx and
Save a copy to edit.

- **Open**: the file is read (an .xlsx by the time-limited worker, formats/sheet_xlsx.py) into the sheet the
  editor shows; an .xlsx made elsewhere with things the app can't keep opens read-only, with the list.
- **Save** (`save`): the browser sends the cells it changed — each with what it was when the browser last saw
  it — plus any tab settings (widths, frozen panes, totals, colour rules, charts). The server re-reads the file
  (cached by content), applies a cell only if it's still what the browser saw (or the file hasn't changed at
  all), and writes. Only the same cell conflicts: those come back (409) with the newer content while every other
  change is saved. Changes to the sheet's shape (rows or columns inserted or deleted, a sort, tabs added,
  renamed, moved or deleted) send the whole sheet and need the file to be unchanged since it was opened.
- Versions, quota, the per-file lock and the index as for every document (documents.py).
"""
import copy
import json
import os

from fastapi import HTTPException

from . import config, docops, documents, settings, sharing
from .formats import sheet_csv, sheet_model as M, sheet_xlsx
from .store import fileio, kinds, nodes, paths, roots, versions

READ_ONLY_MESSAGE = ("This .xlsx was made in another app and has things Household Docs can't keep, so it opens "
                     "read only. Save a copy to edit it here.")


def _limit(conn) -> int:
    return int(settings.get("max_doc_mb", conn)) * 1024 * 1024


def parse(data: bytes, ext: str) -> dict:
    """{"sheet", "readOnly", "features", "format", "dialect"?} of a sheet file's bytes (HTTPException 422 when it
    can't be read)."""
    if ext == "csv":
        try:
            r = sheet_csv.read(data)
        except sheet_csv.CsvError as e:
            raise HTTPException(422, str(e))
        return {"sheet": r["sheet"], "readOnly": False, "features": [], "format": "csv", "dialect": r["dialect"]}
    try:
        r = sheet_xlsx.read(data)
    except sheet_xlsx.ReadError as e:
        raise HTTPException(422, str(e))
    return {"sheet": r["sheet"], "readOnly": r["readOnly"], "features": r["features"], "format": "xlsx"}


def encode(sheet: dict, fmt: str, dialect: dict | None = None) -> bytes:
    if fmt == "csv":
        return sheet_csv.write(sheet, dialect)
    return sheet_xlsx.write(sheet, config.currency())


def index_of(sheet: dict, size: int, conn) -> tuple[str | None, dict]:
    body, refs = M.index_text(sheet)
    limit = int(settings.get("content_index_mb", conn)) * 1024 * 1024
    stats = dict.fromkeys(kinds.STAT_COLUMNS)
    if size > limit:
        return None, stats
    stats["sheet_cells"] = json.dumps(refs, separators=(",", ":"))
    return body, stats


def _ext(node) -> str:
    return (node["ext"] or "xlsx").lower()


def open_sheet(conn, user: dict, node_id: str) -> dict:
    node, role = sharing.require(conn, user, node_id, "viewer")
    from . import changes, kids
    kids.refuse_sheet(user, "sheet")
    _root, _real, data, etag, _st, _sha = documents._read(conn, node)
    p = parse(data, _ext(node))
    out = documents.meta_json(conn, user, node, role)
    can_edit = sharing.at_least(role, "editor") and not out.get("readOnlyMode")    # moving (§5.7): read only
    out.update({"etag": etag, "sheet": p["sheet"], "format": p["format"], "readOnly": p["readOnly"],
                "features": p["features"], "canEdit": can_edit and not p["readOnly"], "mayEdit": can_edit,
                "currency": config.currency(), "limits": {"tabs": M.MAX_TABS, "rows": M.MAX_ROWS, "cols": M.MAX_COLS,
                                                          "cells": M.MAX_CELLS}})
    out["changedSince"] = changes.since_last_look(conn, user, node, sheet=p["sheet"], ext=_ext(node))   # §17.21
    documents.touch_opened(conn, user["id"], node["id"])
    return out


def _tab(sheet: dict, name) -> dict | None:
    if not isinstance(name, str):
        return None
    return next((t for t in sheet["tabs"] if t["name"].casefold() == name.casefold()), None)


def apply_changes(base: dict, cells: list, tabs_meta: list | None, *, stale: bool) -> tuple[dict, list, list]:
    """The sheet with the browser's changed cells and tab settings applied → (sheet, conflicts, tabs whose
    settings weren't applied). A cell conflicts only when the file changed since the browser's copy *and* that
    cell isn't what it saw. Tab settings (widths, frozen panes, totals, colour rules, charts) replace the tab's
    whole settings, so they are applied only when the file hasn't changed since (security review: a stale save
    never overwrites someone else's settings); otherwise they come back to be done again."""
    new = copy.deepcopy(base)
    conflicts: list = []
    not_saved: list = []
    for ch in cells:
        if not isinstance(ch, dict):
            raise HTTPException(422, "Each change names a tab, a cell and its new content.")
        tab = _tab(new, ch.get("tab"))
        ref = ch.get("ref")
        if tab is None or tab["kind"] != "grid":
            conflicts.append({"tab": ch.get("tab"), "ref": ref, "gone": True, "theirs": None,
                              "message": f"The tab “{ch.get('tab')}” isn't there any more."})
            continue
        if not isinstance(ref, str) or M.parse_ref(ref) is None:
            raise HTTPException(422, f"Cells go up to {M.MAX_ROWS:,} rows and {M.MAX_COLS} columns.")
        try:
            was = M.clean_cell(ch.get("was"))
            now = M.clean_cell(ch.get("now"))
        except M.SheetError as e:
            raise HTTPException(422, str(e))
        cur = tab["cells"].get(ref)
        if stale and M.content(cur) != M.content(was):
            if M.content(cur) == M.content(now):
                if now is not None:
                    tab["cells"][ref] = now           # the same change made twice: no conflict
                continue
            conflicts.append({"tab": tab["name"], "ref": ref, "theirs": cur, "message":
                              f"{tab['name']} › {ref} was changed by someone else while you were editing it."})
            continue
        if now is None:
            tab["cells"].pop(ref, None)
        else:
            tab["cells"][ref] = now
    for tm in tabs_meta or []:
        if not isinstance(tm, dict):
            raise HTTPException(422, "Tab settings must be objects.")
        tab = _tab(new, tm.get("name"))
        if tab is None or tab["kind"] != "grid":
            continue
        try:
            M.clean_tab_meta(tm, dict(tab) if stale else tab)       # checked either way
        except M.SheetError as e:
            raise HTTPException(422, str(e))
        if stale:
            not_saved.append(tab["name"])
    return new, conflicts, not_saved


def save(conn, user: dict, node_id: str, body: dict) -> tuple[dict, list]:
    """Save changed cells / tab settings, or (`sheet`) the whole sheet. → (answer, conflicts). An answer with
    `tabsNotSaved` (a stale save's tab settings, not applied) is a 409 too."""
    node, role = sharing.require(conn, user, node_id, "editor")
    if node["kind"] != "sheet":
        raise HTTPException(422, "This document isn't a sheet.")
    from . import kids
    kids.refuse_sheet(user, "sheet")
    etag = body.get("etag")
    if not isinstance(etag, str) or not etag:
        raise HTTPException(422, "Send the etag the sheet was opened with.")
    fileio.ensure_writable()
    with fileio.file_lock(node["id"]):
        node = nodes.get(conn, node_id)
        root, real, data, cur_etag, st, sha = documents._read(conn, node)
        ext = _ext(node)
        p = parse(data, ext)
        if p["readOnly"]:
            raise HTTPException(409, {"message": READ_ONLY_MESSAGE, "readOnly": True, "features": p["features"]})
        base = p["sheet"]
        stale = cur_etag != etag
        outside = sha != node["sha256"] or st.st_mtime_ns != node["mtime_ns"]
        if body.get("sheet") is not None:
            if stale:
                raise HTTPException(409, {"message": "This sheet was changed since you opened it, so that change "
                                                     "(rows, columns, sorting or tabs) wasn't made. It has been "
                                                     "reloaded — please do it again.",
                                          "stale": True, "sheet": base, "etag": cur_etag, "outside": outside,
                                          "by": None if outside else documents._name_of(conn, node["updated_by"])})
            try:
                new = M.clean_sheet(body["sheet"])
            except M.SheetError as e:
                raise HTTPException(422, str(e))
            conflicts, tabs_not_saved = [], []
        else:
            new, conflicts, tabs_not_saved = apply_changes(base, body.get("cells") or [], body.get("tabs"), stale=stale)
            try:
                new = M.clean_sheet(new)
            except M.SheetError as e:
                raise HTTPException(422, str(e))
        if ext == "csv":
            grids = [t for t in new["tabs"] if t["kind"] == "grid"]
            if len(new["tabs"]) > 1 or len(grids) != 1:
                raise HTTPException(422, "A .csv sheet has one tab — Convert to .xlsx first to add tabs or charts.")
        answer = {}
        if new == base:
            fresh = node
            answer.update(etag=cur_etag, modified=node["mtime"], size=node["size"])
        else:
            out = encode(new, ext, p.get("dialect"))
            if len(out) > _limit(conn):
                raise HTTPException(413, f"A sheet can be at most {settings.get('max_doc_mb', conn)} MB.")
            docops.check_quota(conn, root, len(out) - st.st_size)
            if outside:
                nodes.update_stat(conn, node["id"], st, sha, updated_by=None)
                node = nodes.get(conn, node_id)
            versions.snapshot(conn, root, node, real, user["id"], force=bool(conflicts))
            nst = fileio.write_atomic(root, real, out)
            nsha = fileio.sha256_bytes(out)
            if ext == "xlsx":
                sheet_xlsx.remember(nsha, {"sheet": new, "readOnly": False, "features": [], "owned": True})
            body_text, stats = index_of(new, len(out), conn)
            nodes.update_stat(conn, node["id"], nst, nsha, updated_by=user["id"], body=body_text, set_body=True,
                              stats=stats)
            fresh = nodes.get(conn, node_id)
            from . import activity, changes
            activity.record(conn, "edited", user["id"], fresh)
            changes.saw(conn, user["id"], fresh["id"])          # your own save is never "new" to you (§17.21)
            answer.update(etag=documents.etag_of_row(fresh), modified=fresh["mtime"], size=fresh["size"])
        if tabs_not_saved:
            answer["tabsNotSaved"] = tabs_not_saved
        if stale or conflicts:
            answer["sheet"] = new                  # others' changes, for the browser to show
            answer["by"] = None if outside else documents._name_of(conn, node["updated_by"])
            answer["outside"] = outside
    return answer, conflicts


# ---------------------------------------------------------------- export / import / convert / copy
def read_sheet(conn, user: dict, node_id: str) -> tuple:
    node, role = sharing.require(conn, user, node_id, "viewer")
    if node["kind"] != "sheet":
        raise HTTPException(422, "This document isn't a sheet.")
    _root, _real, data, _etag, _st, _sha = documents._read(conn, node)
    return node, role, data, parse(data, _ext(node))


def export_csv(conn, user: dict, node_id: str, tab_name: str | None, mode: str) -> tuple[str, bytes]:
    from .common import csv_export
    node, _role, _data, p = read_sheet(conn, user, node_id)
    grids = [t for t in p["sheet"]["tabs"] if t["kind"] == "grid"]
    tab = _tab(p["sheet"], tab_name) if tab_name else grids[0]
    if tab is None or tab["kind"] != "grid":
        raise HTTPException(404, "That tab isn't in this sheet.")
    rows = []
    for row in sheet_csv.export_tab(tab, mode):
        rows.append([v if k in ("num", "formula") else csv_export.guard(v) for k, v in row])
    data = csv_export.to_text(None, rows, bom=True).encode("utf-8")
    stem = paths.split_ext(node["name"])[0]
    name = f"{stem} - {tab['name']}.csv" if len(grids) > 1 else f"{stem}.csv"
    return name, data


def export_xlsx(conn, user: dict, node_id: str) -> tuple[str, bytes]:
    node, _role, data, p = read_sheet(conn, user, node_id)
    stem = paths.split_ext(node["name"])[0]
    if p["format"] == "xlsx":
        return f"{stem}.xlsx", data                # the file itself
    return f"{stem}.xlsx", encode(p["sheet"], "xlsx")


def import_file(conn, user: dict, name: str, data: bytes, parent_id: str | None, fmt: str) -> tuple[str, list]:
    """A .csv or .xlsx into a new sheet (an .xlsx keeps what the app can keep). → (node id, what was left out)."""
    stem, ext = paths.split_ext((name or "").strip())
    ext = (ext or "").lower()
    if ext not in ("csv", "xlsx", "tsv", "txt"):
        raise HTTPException(422, "Import a .csv or .xlsx file.")
    if len(data) > _limit(conn):
        raise HTTPException(413, f"A sheet can be at most {settings.get('max_doc_mb', conn)} MB.")
    p = parse(data, "xlsx" if ext == "xlsx" else "csv")
    sheet = p["sheet"]
    for t in sheet["tabs"]:
        for cell in (t.get("cells") or {}).values() if t["kind"] == "grid" else []:
            cell.pop("x", None)
    if fmt not in ("xlsx", "csv"):
        fmt = "xlsx"
    if fmt == "csv" and len(sheet["tabs"]) > 1:
        fmt = "xlsx"
    out = encode(sheet, fmt, p.get("dialect") if fmt == "csv" else None)
    nid = docops.create(conn, user, "sheet", stem or "Imported", parent_id, out, ext=fmt)
    return nid, p["features"]


def convert_to_xlsx(conn, user: dict, node_id: str) -> str:
    """A .csv sheet becomes an .xlsx (same item: shares, favourites and history stay; the .csv is kept as a
    version)."""
    node, _role = sharing.require(conn, user, node_id, "editor")
    if node["kind"] != "sheet" or _ext(node) != "csv":
        raise HTTPException(422, "Only a .csv sheet can be converted.")
    fileio.ensure_writable()
    with fileio.file_lock(node["id"]):
        node = nodes.get(conn, node_id)
        root, real, data, _etag, st, sha = documents._read(conn, node)
        p = parse(data, "csv")
        out = encode(p["sheet"], "xlsx")
        docops.check_quota(conn, root, len(out))
        if sha != node["sha256"] or st.st_mtime_ns != node["mtime_ns"]:
            nodes.update_stat(conn, node["id"], st, sha, updated_by=None)
            node = nodes.get(conn, node_id)
        versions.snapshot(conn, root, node, real, user["id"], force=True)
        base = roots.root_real(root)
        prel = paths.parent_rel(node["rel"])
        folder_real = paths.resolve(base, prel)
        new_name = paths.unique_name(folder_real, paths.split_ext(node["name"])[0] + ".xlsx")
        rel = paths.join_rel(prel, new_name)
        dest = paths.resolve(base, rel)
        nodes.free_rel(conn, root["id"], rel)
        try:
            nst = fileio.write_atomic(root, dest, out, exclusive=True)
        except FileExistsError:
            raise HTTPException(409, "Something with that name appeared just now — try again.")
        os.remove(real)
        nodes.relocate(conn, node, root_id=root["id"], rel=rel, parent_id=node["parent_id"])
        nsha = fileio.sha256_bytes(out)
        sheet_xlsx.remember(nsha, {"sheet": p["sheet"], "readOnly": False, "features": [], "owned": True})
        body_text, stats = index_of(p["sheet"], len(out), conn)
        nodes.update_stat(conn, node["id"], nst, nsha, updated_by=user["id"], body=body_text, set_body=True,
                          stats=stats)
    from . import db
    db.audit(conn, "converted", user["id"], node["id"], node["root_id"])
    from . import activity
    activity.record_id(conn, "renamed", user["id"], node["id"], detail={"from": node["name"], "to": paths.base_name(rel)})
    return node["id"]


def save_copy(conn, user: dict, node_id: str) -> str:
    """Save a copy to edit: a new .xlsx the app owns, with what the app can keep (beside the original when you
    can add things there, else in My docs)."""
    node, role, _data, p = read_sheet(conn, user, node_id)
    sheet = p["sheet"]
    for t in sheet["tabs"]:
        if t["kind"] == "grid":
            for cell in t["cells"].values():
                cell.pop("x", None)
    out = encode(sheet, "xlsx")
    stem = paths.split_ext(node["name"])[0]
    parent = nodes.parent_ref(node, nodes.roots_info(conn))
    try:
        docops.target_folder(conn, user, parent)
    except HTTPException:
        parent = None
    return docops.create(conn, user, "sheet", f"{stem} (editable)", parent, out, ext="xlsx")


def restore_index(conn, node, real: str) -> tuple[str | None, dict]:
    """The searchable text and columns of a sheet file on disk (after a restore)."""
    limit = int(settings.get("content_index_mb", conn)) * 1024 * 1024
    st = os.stat(real)
    return (kinds.index_text_for("sheet", real, node["name"], st.st_size, limit),
            kinds.stats_for("sheet", real, st.st_size, limit))
