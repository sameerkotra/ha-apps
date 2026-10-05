"""Opening and saving documents (SPEC §7.1–7.3): notes (.txt, and Markdown notes as plain text until the
Markdown editor arrives), checklists (.md task lists).

- Opening always stats the file first (store/index.refresh_node), so a change made outside the app is what
  the editor shows.
- Notes save the whole text with the etag the editor started from; a stale etag is a 409 with the newer text
  (*Keep mine* saves again with that newer etag and `keepMine`, which keeps theirs as a version; *Use theirs*
  reloads). The same happens when the file was changed outside the app.
- Checklists save operations, applied to the current file under its lock; items are found by text +
  occurrence, so two people ticking different items never conflict. An item that's gone is a 409 for that
  item only (the others are applied).
- Every save: versions first (store/versions), then write-to-temp-and-rename (store/fileio), then the index row.
"""
import os

from fastapi import HTTPException

from . import config, docops, links, settings, sharing, tags
from .formats import checklist_md, text as text_fmt
from .store import fileio, kinds, moving, nodes, paths, versions

EDITING_SECONDS = 30
TEXT_KINDS = ("note", "markdown")


def _limit(conn) -> int:
    return int(settings.get("max_doc_mb", conn)) * 1024 * 1024


def _content_limit(conn) -> int:
    return int(settings.get("content_index_mb", conn)) * 1024 * 1024


def _name_of(conn, uid):
    if not uid:
        return None
    r = conn.execute("SELECT name FROM users WHERE id = ?", (uid,)).fetchone()
    return r["name"] if r else None


def etag_of_row(row) -> str:
    return fileio.make_etag(row["size"] or 0, row["mtime_ns"] or 0, row["sha256"])


def editing_by(conn, user: dict, row) -> str | None:
    """Someone else saved in the last 30 seconds: their name ("Alex is editing")."""
    if not row["updated_by"] or row["updated_by"] == user["id"] or not row["mtime"]:
        return None
    t = config.parse_iso(row["mtime"])
    if t is None or abs((config.utcnow() - t).total_seconds()) > EDITING_SECONDS:
        return None
    return _name_of(conn, row["updated_by"])


# More fields for an open document from later steps: fn(conn, user, node) -> {field: value}
META_EXTRAS: list = []


def meta_json(conn, user: dict, node, role: str) -> dict:
    owner = sharing.owner_id(conn, node)
    rinfo = nodes.roots_info(conn)
    paused = moving.is_read_only(conn)          # documents are being moved (§5.7): everything opens read only
    may_tick = not paused and (sharing.at_least(role, "editor") or (node["kind"] == "checklist" and sharing.viewers_may_tick(conn, user, node)))
    more: dict = {}
    for fn in META_EXTRAS:
        more.update(fn(conn, user, node) or {})
    return nodes.node_json(node, role=role, extra={**more,
        "etag": etag_of_row(node), "canEdit": sharing.at_least(role, "editor") and not paused, "canTick": may_tick,
        "readOnlyMode": paused, "tags": tags.of(conn, node["id"]),
        "canShare": sharing.at_least(role, "manager") and owner is not None and not user.get("is_child"),
        "ownerId": owner, "ownerName": _name_of(conn, owner),
        "updatedByName": _name_of(conn, node["updated_by"]), "editing": editing_by(conn, user, node),
        "path": sharing.visible_path(conn, user, node), "rootKind": rinfo.get(node["root_id"], ("person",))[0],
        "rootLabel": rinfo.get(node["root_id"], ("", ""))[1], "parentRef": nodes.parent_ref(node, rinfo)})


def touch_opened(conn, user_id: str, node_id: str) -> None:
    """Opened now (Recent), and looked at as it is now (§17.21: what changed since you last looked)."""
    now = config.now_iso()
    conn.execute("INSERT INTO user_state (user_id, node_id, opened_at, seen_at, seen_sha) VALUES (?, ?, ?, ?, "
                 "(SELECT sha256 FROM nodes WHERE id = ?)) ON CONFLICT(user_id, node_id) DO UPDATE SET "
                 "opened_at = excluded.opened_at, seen_at = excluded.seen_at, seen_sha = excluded.seen_sha",
                 (user_id, node_id, now, now, node_id))


def _ticks(conn, node_id: str) -> dict:
    return {r["item_key"]: (r["done_by"], r["done_at"])
            for r in conn.execute("SELECT * FROM checklist_ticks WHERE node_id = ?", (node_id,))}


def items_json(conn, items) -> list[dict]:
    names = {}
    out = []
    for it in items:
        by, at = it.tick if (it.done and it.tick) else (None, None)
        if by and by not in names:
            names[by] = _name_of(conn, by)
        out.append({"key": it.key, "text": it.text, "done": it.done, "level": it.level,
                    "doneBy": by, "doneByName": names.get(by), "doneAt": at})
    return out


def _read(conn, node):
    root, real = nodes.real_path(conn, node)
    if (node["size"] or 0) > _limit(conn):
        raise HTTPException(413, f"This document is bigger than {settings.get('max_doc_mb', conn)} MB, so the editor "
                                 "doesn't open it. Download it instead.")
    try:
        data, etag, st, sha = fileio.read_with_etag(real)
    except FileNotFoundError:
        raise HTTPException(404, sharing.NOT_FOUND)
    if len(data) > _limit(conn):
        raise HTTPException(413, "This document is too big for the editor. Download it instead.")
    return root, real, data, etag, st, sha


def open_doc(conn, user: dict, node_id: str) -> dict:
    node, role = sharing.require(conn, user, node_id, "viewer")
    if not kinds.is_document(node["kind"]):
        raise HTTPException(415, "This file isn't a document the app edits. Download it instead.")
    from . import changes, kids, app_messages
    kids.refuse_sheet(user, node["kind"])
    if node["kind"] == "sheet":
        from . import sheets
        return sheets.open_sheet(conn, user, node_id)
    _root, _real, data, etag, _st, _sha = _read(conn, node)
    try:
        text, _meta = text_fmt.decode(data)
    except text_fmt.NotText as e:
        raise HTTPException(415, str(e))
    out = meta_json(conn, user, node, role)
    out["etag"] = etag
    if node["kind"] == "checklist":
        try:
            items = checklist_md.parse(text)
        except ValueError:
            items = None
        if items is not None:
            ticks = _ticks(conn, node["id"])
            for it in items:
                it.tick = ticks.get(it.key)
            out["items"] = items_json(conn, items)
            out["changedSince"] = changes.since_last_look(conn, user, node, items=items)
            out["todoSends"] = app_messages.todo_notes(conn, user, node["id"])        # §17.16
        else:                               # edited elsewhere into something else: open it as a note
            conn.execute("UPDATE nodes SET kind = 'markdown' WHERE id = ?", (node["id"],))
            out["kind"] = "markdown"
            out["text"] = text
    else:
        out["text"] = text
        out["changedSince"] = changes.since_last_look(conn, user, node, text=text)   # §17.21
        if sharing.at_least(role, "editor") and not out.get("readOnlyMode"):
            links.update(conn, user, node, text)            # the text may have been changed outside the app
    touch_opened(conn, user["id"], node["id"])
    return out


def _write(conn, user: dict, root, node, real: str, data: bytes, *, kind: str | None = None, body: str | None = None,
           text: str | None = None):
    """Write a document and update its index row (`text`: the whole text, for the kind's search columns)."""
    st = fileio.write_atomic(root, real, data)
    nodes.update_stat(conn, node["id"], st, fileio.sha256_bytes(data), updated_by=user["id"], kind=kind,
                      body=body if len(data) <= _content_limit(conn) else None, set_body=True,
                      stats=kinds.stats_for_text(kind or node["kind"], text if text is not None else body))
    fresh = nodes.get(conn, node["id"])
    from . import activity, changes
    activity.record(conn, "edited", user["id"], fresh)
    changes.saw(conn, user["id"], fresh["id"])              # your own save is never "new" to you (§17.21)
    return fresh


def save_note(conn, user: dict, node_id: str, etag: str, text: str, keep_mine: bool = False,
              link_hints: dict | None = None) -> dict:
    node, role = sharing.require(conn, user, node_id, "editor")
    if node["kind"] not in TEXT_KINDS:
        raise HTTPException(422, "This document isn't a note.")
    if not isinstance(text, str):
        raise HTTPException(422, "Send the text.")
    fileio.ensure_writable()
    with fileio.file_lock(node["id"]):
        node = nodes.get(conn, node_id)
        root, real = nodes.real_path(conn, node)
        try:
            cur, st, sha = fileio.current_etag(real)
        except FileNotFoundError:
            raise HTTPException(404, sharing.NOT_FOUND)
        if cur != etag:
            with fileio.open_read(real) as f:
                theirs_raw = f.read()
            try:
                theirs, _m = text_fmt.decode(theirs_raw)
            except text_fmt.NotText:
                theirs = None
            outside = not (node["sha256"] == sha and node["mtime_ns"] == st.st_mtime_ns and node["updated_by"])
            mine = not outside and node["updated_by"] == user["id"]
            raise HTTPException(409, {"message": "This note was changed outside the app since you opened it." if outside
                                      else ("You changed this note in another window since you opened it here." if mine
                                            else "Someone else changed this note since you opened it."),
                                      "text": theirs, "etag": cur, "outside": outside,
                                      "by": None if outside else _name_of(conn, node["updated_by"])})
        with fileio.open_read(real) as f:
            _old, meta = text_fmt.decode(f.read())
        data = text_fmt.encode(text, meta)
        if len(data) > _limit(conn):
            raise HTTPException(413, f"A note can be at most {settings.get('max_doc_mb', conn)} MB.")
        docops.check_quota(conn, root, len(data) - st.st_size)
        if sha != node["sha256"] or st.st_mtime_ns != node["mtime_ns"]:
            # changed outside since the index last saw it: record that before the version check
            nodes.update_stat(conn, node["id"], st, sha, updated_by=None)
            node = nodes.get(conn, node_id)
        versions.snapshot(conn, root, node, real, user["id"], force=keep_mine)
        # a Markdown note saved in the app stays one, even when it is only tick boxes for now (§17.2); a
        # change made outside the app is classified again by the index (§5.2)
        fresh = _write(conn, user, root, node, real, data, body=text)
        links.update(conn, user, fresh, text, link_hints)
    return {"etag": etag_of_row(fresh), "modified": fresh["mtime"], "size": fresh["size"], "kind": fresh["kind"]}


def checklist_ops(conn, user: dict, node_id: str, ops: list) -> tuple[dict, list]:
    """Apply operations; returns (the checklist now, conflicts)."""
    node, role = sharing.require(conn, user, node_id, "viewer")
    if node["kind"] != "checklist":
        raise HTTPException(422, "This document isn't a checklist.")
    if not isinstance(ops, list) or not ops or len(ops) > 200:
        raise HTTPException(422, "Send 1 to 200 operations.")
    for op in ops:
        if not isinstance(op, dict) or op.get("op") not in checklist_md.OPS:
            raise HTTPException(422, "Unknown operation.")
    if not sharing.at_least(role, "editor"):
        if not all(op["op"] in checklist_md.TICK_OPS for op in ops):
            raise HTTPException(403, "You can only view this checklist.")
        if not sharing.viewers_may_tick(conn, user, node):
            raise HTTPException(403, "You can only view this checklist (ticking is off for viewers).")
    fileio.ensure_writable()
    conflicts = []
    with fileio.file_lock(node["id"]):
        node = nodes.get(conn, node_id)
        root, real, data, _etag, st, sha = _read(conn, node)
        try:
            text, meta = text_fmt.decode(data)
            items = checklist_md.parse(text)
        except (text_fmt.NotText, ValueError):
            conn.execute("UPDATE nodes SET kind = 'markdown' WHERE id = ?", (node["id"],))
            raise HTTPException(409, "This file isn't a checklist any more (it was changed outside the app). "
                                     "Reopen it to see it as a note.")
        ticks = _ticks(conn, node["id"])
        for it in items:
            it.tick = ticks.get(it.key) if it.done else None
        now = config.now_iso()
        changed = False
        for i, op in enumerate(ops):
            try:
                res = checklist_md.apply(items, op)
            except checklist_md.OpError as e:
                conflicts.append({"index": i, "op": op.get("op"), "key": op.get("key"), "message": str(e), "gone": e.gone})
                checklist_md.assign_keys(items)
                continue
            for it in res["ticked"]:
                it.tick = (user["id"], now)
            for it in res["unticked"]:
                it.tick = None
            checklist_md.assign_keys(items)
            changed = True
        if changed:
            new_text = checklist_md.serialise(items)
            new_data = text_fmt.encode(new_text, meta)
            if new_data != data:
                if len(new_data) > _limit(conn):
                    raise HTTPException(413, "This checklist is too big.")
                docops.check_quota(conn, root, len(new_data) - len(data))
                if sha != node["sha256"] or st.st_mtime_ns != node["mtime_ns"]:
                    nodes.update_stat(conn, node["id"], st, sha, updated_by=None)
                    node = nodes.get(conn, node_id)
                versions.snapshot(conn, root, node, real, user["id"])
                node = _write(conn, user, root, node, real, new_data, kind="checklist",
                              body="\n".join(it.text for it in items), text=new_text)
            conn.execute("DELETE FROM checklist_ticks WHERE node_id = ?", (node["id"],))
            for it in items:
                if it.done and it.tick:
                    conn.execute("INSERT OR REPLACE INTO checklist_ticks (node_id, item_key, done_by, done_at) VALUES (?, ?, ?, ?)",
                                 (node["id"], it.key, it.tick[0], it.tick[1]))
        out = meta_json(conn, user, nodes.get(conn, node_id), role)
        out["items"] = items_json(conn, items)
    return out, conflicts


def versions_ext(conn, node_id: str, n: int) -> str:
    r = conn.execute("SELECT file FROM versions WHERE node_id = ? AND n = ?", (node_id, n)).fetchone()
    return paths.split_ext(r["file"])[1] if r else ""


def restore_version(conn, user: dict, node_id: str, n: int) -> dict:
    node, _role = sharing.require(conn, user, node_id, "editor")
    from . import kids
    kids.refuse_sheet(user, node["kind"])
    fileio.ensure_writable()
    with fileio.file_lock(node["id"]):
        node = nodes.get(conn, node_id)
        root, real = nodes.real_path(conn, node)
        try:
            data = versions.read(conn, root, node["id"], n)
        except FileNotFoundError:
            raise HTTPException(404, "That version isn't there any more.")
        try:
            st = fileio.current_etag(real)[1:]
            if st[1] != node["sha256"] or st[0].st_mtime_ns != node["mtime_ns"]:
                nodes.update_stat(conn, node["id"], st[0], st[1], updated_by=None)
                node = nodes.get(conn, node_id)
        except FileNotFoundError:
            raise HTTPException(404, sharing.NOT_FOUND)
        if not kinds.is_document(node["kind"]):          # a replaced upload's previous copy (§9.2)
            versions.keep_replaced(conn, root, node, real, user["id"])
            st = fileio.write_atomic(root, real, data)
            from .store import index
            kind, sha, body, cols = index.read_file_info(real, node["name"], st, node["kind"], _content_limit(conn))
            nodes.update_stat(conn, node["id"], st, sha, updated_by=user["id"], kind=kind, body=body, set_body=True,
                              stats=cols)
            fresh = nodes.get(conn, node_id)
            from . import activity
            activity.record(conn, "edited", user["id"], fresh)
            return {"etag": etag_of_row(fresh), "kind": fresh["kind"]}
        versions.snapshot(conn, root, node, real, user["id"], force=True)
        if node["kind"] == "sheet":
            if paths.split_ext(f"x.{versions_ext(conn, node['id'], n)}")[1] != (node["ext"] or ""):
                raise HTTPException(409, "This version is a .csv from before the sheet was converted — download "
                                         "it from History instead.")
            from . import sheets
            st = fileio.write_atomic(root, real, data)
            body, stats = sheets.restore_index(conn, node, real)
            nodes.update_stat(conn, node["id"], st, fileio.sha256_bytes(data), updated_by=user["id"], body=body,
                              set_body=True, stats=stats)
            fresh = nodes.get(conn, node_id)
            from . import activity
            activity.record(conn, "edited", user["id"], fresh)
            return {"etag": etag_of_row(fresh), "kind": fresh["kind"]}
        try:
            text, _m = text_fmt.decode(data)
        except text_fmt.NotText:
            text = ""
        kind = None
        if node["kind"] in ("checklist", "markdown"):
            kind = checklist_md.md_kind(text, node["kind"])
        fresh = _write(conn, user, root, node, real, data, kind=kind, body=text)
        links.update(conn, user, fresh, text)
    return {"etag": etag_of_row(fresh), "kind": fresh["kind"]}


def convert_note(conn, user: dict, node_id: str) -> str:
    """Plain ↔ Markdown (§17.2): the file is renamed .txt ↔ .md (same item: shares, tags, links, history stay).
    A .md that is only tick boxes would open as a checklist, so such a note is refused."""
    node, _role = sharing.require(conn, user, node_id, "editor")
    if node["kind"] not in TEXT_KINDS:
        raise HTTPException(422, "Only notes switch between plain text and Markdown.")
    fileio.ensure_writable()
    with fileio.file_lock(node["id"]):
        node = nodes.get(conn, node_id)
        root, real = nodes.real_path(conn, node)
        to_md = node["kind"] == "note"
        if to_md:
            _r, _p, data, _e, _st, _sha = _read(conn, node)
            try:
                text, _m = text_fmt.decode(data)
            except text_fmt.NotText as e:
                raise HTTPException(415, str(e))
            if checklist_md.is_checklist(text):
                raise HTTPException(409, "This note is only tick boxes — as a .md file it would be a checklist. "
                                         "Add a line of text first, or make a checklist instead.")
        from .store import roots
        base = roots.root_real(root)
        prel = paths.parent_rel(node["rel"])
        stem = paths.split_ext(node["name"])[0] if node["ext"] else node["name"]
        new_name = paths.unique_name(paths.resolve(base, prel), stem + (".md" if to_md else ".txt"))
        rel = paths.join_rel(prel, new_name)
        dest = paths.resolve(base, rel)
        if os.path.lexists(dest):
            raise HTTPException(409, f"There is already something called “{new_name}” there.")
        nodes.free_rel(conn, root["id"], rel)
        os.rename(real, dest)
        nodes.relocate(conn, node, root_id=root["id"], rel=rel, parent_id=node["parent_id"])
        conn.execute("UPDATE nodes SET kind = ? WHERE id = ?", ("markdown" if to_md else "note", node["id"]))
    from . import db
    db.audit(conn, "converted", user["id"], node["id"], node["root_id"])
    from . import activity
    activity.record_id(conn, "renamed", user["id"], node["id"], detail={"from": node["name"], "to": new_name})
    return node["id"]
