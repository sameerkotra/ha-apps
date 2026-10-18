"""One person's data (PERSON_DATA_SPEC.md §7, SPEC §18): an admin exports, imports or deletes everything one person
has in Household Docs. The file format, its checks, the jobs and the uploads are the shared
app/common/person_data.py; this module says what is a person's here and how it goes in and out.

What is theirs: their own folder (the person root — every node in it, its versions, tags, ticks, links, OCR
corrections), their templates (`people/<folder>/.templates/`), their Trash, and their own rows elsewhere (prefs,
favourites and pins, saved searches, follows of their own items, rules on their folder, Keep import records). What
they made in other folders belongs to that folder's owner (the ownership rule) and stays; an export lists it in
`elsewhere.json` (path and folder, no content).

The file (`schema` SCHEMA, below — the data version of this export; raise it when a table's columns change, and
upgrade older files in `_upgrade`):

    data/prefs.json            [{prefs}]                 their own settings (the known keys only)
    data/nodes.json            the index rows of their folder (and of their Trash, with Include Trash):
                               id, rel, parent_id, kind, color, size, sha256, mtime, created_by, updated_by, trash_id
    data/node_tags.json, node_links.json, checklist_ticks.json, ocr_text.json, versions.json, user_state.json,
    shares.json (the shares they gave on their own items, with the person's name and login), trash.json,
    saved_searches.json, follows.json, filing_rules.json, cleanup_rules.json, keep_imports.json
    data/elsewhere.json        a record (never imported): what they made in other people's or shared folders
    files/<rel>                their folder as the index has it (files/.trash/<trash id>/… with Include Trash)
    files/.versions/<node id>/<n>.<ext>   with Include old versions (on)
    files/.templates/<name>    their templates

Not in it: access (root_access, Kids' space links), view links (their tokens are secrets), Activity, AI usage,
App settings and every key.

Rules: an import gives every row a new id (IdMap) and writes the files after the commit; a delete removes the rows in
one transaction and the files after it. One import or delete at a time (person_data.one_at_a_time).
"""
import json
import logging
import os
import re
import shutil
import threading

from fastapi import HTTPException

from . import config, db, docops, files, filing, settings, tags as tagmod, templates
from .common import person_data as pd
from .search import saved as saved_mod
from .store import fileio, index, kinds, nodes, paths, roots, trash, versions

logger = logging.getLogger("people_data")

APP = "household_docs"
APP_LABEL = "Household Docs"
SCHEMA = 1                          # the data version of an export (documented in spec/SPEC.md §18)
FORMER_ID = "former-member"         # stands in for a forgotten person in what stays for others
FORMER_NAME = versions.FORMER_NAME
SMALL_FILES = 200                   # up to this many files and SMALL_BYTES: the .zip is sent at once
SMALL_BYTES = 20 * 1024 * 1024
MAX_FILES = files.ZIP_MAX_FILES     # the whole-app backup's limits (§7.2)
MAX_BYTES = files.ZIP_MAX_BYTES
UPLOAD_MAX = 6 * 1024 ** 3          # an export of MAX_BYTES with its versions and Trash
LIMITS = pd.Limits(max_upload=UPLOAD_MAX, max_unpacked=UPLOAD_MAX, max_members=60_000)
TEMP_PREFIX = "person-data-docs-"
UPLOAD_PREFIX = TEMP_PREFIX + "up-"
EXPORT_PREFIX = TEMP_PREFIX + "ex-"
TABLES = ("prefs", "nodes", "node_tags", "node_links", "checklist_ticks", "user_state", "ocr_text", "versions",
          "trash", "shares", "saved_searches", "follows", "filing_rules", "cleanup_rules", "keep_imports")
TRASH_PREFIX = trash.TRASH_REL_PREFIX          # ".trash/": a trashed node's rel
ROLES = ("viewer", "editor", "manager")

JOBS = pd.Jobs()
_LOCK = threading.Lock()                       # one import or delete at a time
_uploads: dict = {}


def uploads() -> pd.Uploads:
    """The uploaded files, kept in the data folder (config.DATA_DIR is read when first needed: tests set it)."""
    u = _uploads.get(config.DATA_DIR)
    if u is None:
        u = _uploads[config.DATA_DIR] = pd.Uploads(config.DATA_DIR, prefix=UPLOAD_PREFIX)
    return u


def _ids_json(ids) -> str:
    return json.dumps(list(ids))


IN_IDS = "(SELECT value FROM json_each(?))"


def _person(conn, uid: str):
    r = conn.execute("SELECT * FROM users WHERE id = ?", (uid,)).fetchone()
    if r is None:
        raise HTTPException(404, "That person isn't known to Household Docs.")
    return r


def _root_ids(conn, root_id: str) -> list[str]:
    return [r["id"] for r in conn.execute("SELECT id FROM nodes WHERE root_id = ?", (root_id,))]


def _targets(root_id: str, ids) -> list[str]:
    """How rules, follows and sensors name a place in this folder: node ids and "root:<id>"."""
    return list(ids) + ["root:" + root_id]


# =====================================================================================================================
# the summary (the dialogs, and the delete's dry run)
# =====================================================================================================================
def _counts(conn, user, root) -> dict:
    uid = user["id"]
    c = {"files": 0, "bytes": 0, "folders": 0, "versions": 0, "versionBytes": 0, "trash": 0, "trashBytes": 0,
         "templates": 0, "sharesGiven": 0, "sharesTo": 0, "elsewhere": 0, "kids": 0, "rules": 0, "searches": 0,
         "follows": 0, "sensors": 0, "sharedRules": 0}
    if root is not None:
        rid = root["id"]
        r = conn.execute("SELECT COUNT(*), COALESCE(SUM(size), 0) FROM nodes WHERE root_id = ? AND gone_at IS NULL "
                         "AND trash_id IS NULL AND kind != 'folder'", (rid,)).fetchone()
        c["files"], c["bytes"] = r[0], r[1]
        c["folders"] = conn.execute("SELECT COUNT(*) FROM nodes WHERE root_id = ? AND gone_at IS NULL AND trash_id IS NULL "
                                    "AND kind = 'folder'", (rid,)).fetchone()[0]
        r = conn.execute("SELECT COUNT(*), COALESCE(SUM(v.size), 0) FROM versions v JOIN nodes n ON n.id = v.node_id "
                         "WHERE n.root_id = ?", (rid,)).fetchone()
        c["versions"], c["versionBytes"] = r[0], r[1]
        c["trash"] = conn.execute("SELECT COUNT(*) FROM trash WHERE owner_id = ?", (uid,)).fetchone()[0]
        c["trashBytes"] = conn.execute("SELECT COALESCE(SUM(size), 0) FROM nodes WHERE root_id = ? AND trash_id IS NOT NULL",
                                       (rid,)).fetchone()[0]
        c["sharesGiven"] = conn.execute("SELECT COUNT(*) FROM shares s JOIN nodes n ON n.id = s.node_id "
                                        "WHERE n.root_id = ?", (rid,)).fetchone()[0]
        ids = _root_ids(conn, rid)
        targets = _ids_json(_targets(rid, ids))
        c["rules"] = sum(conn.execute(f"SELECT COUNT(*) FROM {t} WHERE target IN {IN_IDS}", (targets,)).fetchone()[0]
                         for t in ("filing_rules", "cleanup_rules", "ai_folders"))
        c["sensors"] = conn.execute(f"SELECT COUNT(*) FROM ha_sensors WHERE node_id IN {IN_IDS}", (targets,)).fetchone()[0]
        c["templates"] = len(_template_files(root))
    c["sharesTo"] = conn.execute("SELECT COUNT(*) FROM shares WHERE user_id = ?", (uid,)).fetchone()[0]
    c["elsewhere"] = conn.execute("SELECT COUNT(*) FROM nodes n JOIN roots r ON r.id = n.root_id WHERE n.created_by = ? "
                                  "AND n.gone_at IS NULL AND n.trash_id IS NULL AND NOT (r.kind = 'person' AND "
                                  "r.user_id IS ?)", (uid, uid)).fetchone()[0]
    c["kids"] = conn.execute("SELECT COUNT(*) FROM kid_parents WHERE child_id = ? OR parent_id = ?", (uid, uid)).fetchone()[0]
    c["searches"] = conn.execute("SELECT COUNT(*) FROM saved_searches WHERE user_id = ?", (uid,)).fetchone()[0]
    c["follows"] = conn.execute("SELECT COUNT(*) FROM follows WHERE user_id = ?", (uid,)).fetchone()[0]
    mine = _ids_json(_targets(root["id"], _root_ids(conn, root["id"]))) if root is not None else "[]"
    c["sharedRules"] = sum(conn.execute(f"SELECT COUNT(*) FROM {t} WHERE created_by = ? AND {col} NOT IN {IN_IDS}",
                                        (uid, mine)).fetchone()[0]
                           for t, col in (("filing_rules", "target"), ("cleanup_rules", "target"), ("ha_sensors", "node_id")))
    return c


def _plural(n: int, one: str, many: str | None = None) -> str:
    return one if n == 1 else (many or one + "s")


def summary(conn, uid: str) -> dict:
    user = _person(conn, uid)
    root = roots.person_root(conn, uid)
    c = _counts(conn, user, root)
    days = int(settings.get("trash_days", conn))
    counts = [
        {"key": "files", "label": _plural(c["files"], "file") + " in their folder", "count": c["files"],
         "detail": pd.bytes_text(c["bytes"])},
        {"key": "folders", "label": _plural(c["folders"], "folder"), "count": c["folders"]},
        {"key": "versions", "label": "old " + _plural(c["versions"], "version"), "count": c["versions"],
         "detail": pd.bytes_text(c["versionBytes"])},
        {"key": "trash", "label": _plural(c["trash"], "item") + " in their Trash", "count": c["trash"],
         "detail": pd.bytes_text(c["trashBytes"])},
        {"key": "templates", "label": _plural(c["templates"], "template"), "count": c["templates"]},
        {"key": "sharesGiven", "label": _plural(c["sharesGiven"], "share") + " they gave on their own items",
         "count": c["sharesGiven"]},
        {"key": "sharesTo", "label": _plural(c["sharesTo"], "share") + " to them", "count": c["sharesTo"]},
        {"key": "rules", "label": _plural(c["rules"], "rule") + " on their folder", "count": c["rules"]},
        {"key": "searches", "label": "saved " + _plural(c["searches"], "search", "searches"), "count": c["searches"]},
        {"key": "follows", "label": _plural(c["follows"], "follow"), "count": c["follows"]},
        {"key": "kids", "label": "Kids' space " + _plural(c["kids"], "link"), "count": c["kids"]},
        {"key": "elsewhere", "label": _plural(c["elsewhere"], "thing") + " they made in other folders (these stay)",
         "count": c["elsewhere"]},
    ]
    others = conn.execute("SELECT id, name FROM users WHERE id != ? AND disabled = 0 ORDER BY name COLLATE NOCASE",
                          (uid,)).fetchall()
    who = user["name"]
    give = [{"value": "give:" + o["id"], "label": f"Give to {o['name']}",
             "help": f"Everything in their folder moves into {o['name']}'s folder as “From {who}”, with its versions "
                     "and shares. Their Trash and templates are deleted."} for o in others]
    return {
        "person": {"id": user["id"], "name": who},
        "counts": counts,
        "exportOptions": [
            {"key": "versions", "label": "Include old versions", "default": True},
            {"key": "trash", "label": "Include Trash", "default": False,
             "help": "What's in their Trash comes back in their Trash."}],
        "exportNote": ("The file holds their documents and isn't encrypted — keep it somewhere safe. A big folder is "
                       "prepared in the background; the file is kept for an hour. More than "
                       f"{MAX_FILES:,} files or {MAX_BYTES // 1024 ** 3} GB: use the whole-app backup with files."),
        "keeps": [
            f"What they made in other people's folders and in shared folders ({c['elsewhere']}) — it belongs to that "
            f"folder's owner; if you forget them it shows as made by {FORMER_NAME}",
            "Versions they saved there, and the shares they gave on other people's items",
            f"Rules and Home Assistant sensors they set on shared folders ({c['sharedRules']}) — they stop working once "
            "they can't open the folder",
            "The audit log (it records this delete)"],
        "cannotReach": ["whole-app backups and Home Assistant backups made before",
                        "copies people downloaded, or opened over the network from /share"],
        "deleteChoices": [{
            "key": "folder", "label": "What happens to their folder", "default": "trash",
            "options": [{"value": "trash", "label": "Move to Trash",
                         "help": f"As before: everything in their folder moves to their Trash, where it can be "
                                 f"restored for {days} days. Nothing else is deleted, and they stay."}]
                       + give
                       + [{"value": "delete", "label": "Delete for good",
                           "help": "Their files, versions, templates and Trash go."}]}],
        "forgetNote": ("Only with Give to someone or Delete for good. Docs adds every Home Assistant person with a "
                       "login every 5 minutes, so someone still in Home Assistant comes back as a new, empty person — "
                       "remove them in Home Assistant first if they shouldn't."),
        "deleteNote": ("With Give to someone or Delete for good, their settings, favourites, searches, follows, extra "
                       "notify services, Kids' space links, the shares to them and their shared-folder access are "
                       "deleted too. It can't be undone."),
    }


# =====================================================================================================================
# export
# =====================================================================================================================
NODE_COLS = ("id", "rel", "parent_id", "kind", "color", "size", "sha256", "mtime", "created_by", "updated_by",
             "trash_id")


def _template_files(root) -> list[tuple[str, str]]:
    """(name, real path) of the person's templates: plain files in their folder's hidden .templates."""
    try:
        d = os.path.join(roots.root_real(root), templates.DIR)
    except (roots.DocsUnavailable, paths.PathError):
        return []
    if os.path.islink(d) or not os.path.isdir(d):
        return []
    out = []
    for e in sorted(os.scandir(d), key=lambda x: x.name):
        if e.name.startswith(".") or e.is_symlink() or not e.is_file(follow_symlinks=False):
            continue
        if paths.name_problem(e.name) is None:
            out.append((e.name, e.path))
    return out


def _regular(real: str, base: str) -> bool:
    """A plain file inside `base` (no link anywhere on the way: realpath is the path itself)."""
    return (os.path.isfile(real) and not os.path.islink(real) and paths.inside(base, os.path.realpath(real))
            and os.path.realpath(real) == os.path.normpath(real))


def _rows(conn, sql: str, params=()) -> list[dict]:
    return [dict(r) for r in conn.execute(sql, params)]


def collect(conn, uid: str, opts: dict) -> dict:
    """Everything of theirs for the file: {tables, records, files: [(arc, real)], bytes, person}. Raises 413 past
    the whole-app backup's limits."""
    user = _person(conn, uid)
    root = roots.person_root(conn, uid)
    tables = {t: [] for t in TABLES}
    prefs = {}
    try:
        prefs = json.loads(user["prefs"] or "{}")
    except ValueError:
        prefs = {}
    from .routers.me import PREF_DEFAULTS
    tables["prefs"] = [{"prefs": {k: v for k, v in (prefs if isinstance(prefs, dict) else {}).items()
                                  if k in PREF_DEFAULTS}}]
    out_files: list = []
    total = 0
    elsewhere = [{"folder": r["label"] if r["kind"] == "shared" else f"{r['label']} (someone's folder)",
                  "path": r["rel"]} for r in conn.execute(
        "SELECT n.rel, r.label, r.kind FROM nodes n JOIN roots r ON r.id = n.root_id WHERE n.created_by = ? "
        "AND n.gone_at IS NULL AND n.trash_id IS NULL AND NOT (r.kind = 'person' AND r.user_id IS ?) ORDER BY r.label, n.rel",
        (uid, uid))]
    if root is not None:
        rid = root["id"]
        base = roots.root_real(root)
        live = conn.execute(f"SELECT * FROM nodes n WHERE n.root_id = ? AND {nodes.LIVE} ORDER BY n.rel", (rid,)).fetchall()
        node_rows = []
        for n in live:
            if n["kind"] != "folder":
                try:
                    real = paths.resolve(base, n["rel"])
                except paths.PathError:
                    continue
                if not _regular(real, base):
                    continue                       # not on disk (yet): the scan before an export catches up
                out_files.append((n["rel"], real))
                total += os.path.getsize(real)
            node_rows.append(n)
        trash_rows = []
        if opts.get("trash"):
            for t in conn.execute("SELECT * FROM trash WHERE owner_id = ? AND root_id = ? ORDER BY deleted_at",
                                  (uid, rid)).fetchall():
                try:
                    tdir = trash._tdir(conn, root, t)
                except paths.PathError:
                    continue
                inner = conn.execute("SELECT * FROM nodes WHERE trash_id = ? ORDER BY rel", (t["id"],)).fetchall()
                prefix = f"{TRASH_PREFIX}{t['id']}/"
                kept = []
                for n in inner:
                    if not n["rel"].startswith(prefix):
                        continue
                    if n["kind"] != "folder":
                        real = os.path.join(tdir, *n["rel"][len(prefix):].split("/"))
                        if not _regular(real, tdir):
                            continue
                        out_files.append((n["rel"], real))
                        total += os.path.getsize(real)
                    kept.append(n)
                if kept:
                    node_rows += kept
                    trash_rows.append({k: t[k] for k in ("id", "node_id", "original_rel", "deleted_by", "deleted_at")})
        tables["nodes"] = [{k: n[k] for k in NODE_COLS} for n in node_rows]
        tables["trash"] = trash_rows
        ids = [n["id"] for n in node_rows]
        q = _ids_json(ids)
        tables["node_tags"] = _rows(conn, f"SELECT node_id, tag, added_by, added_at FROM node_tags WHERE node_id IN {IN_IDS}", (q,))
        tables["node_links"] = _rows(conn, f"SELECT from_id, text, to_id FROM node_links WHERE from_id IN {IN_IDS}", (q,))
        tables["checklist_ticks"] = _rows(conn, "SELECT node_id, item_key, done_by, done_at FROM checklist_ticks "
                                                f"WHERE node_id IN {IN_IDS}", (q,))
        tables["ocr_text"] = _rows(conn, "SELECT node_id, sha256, text, model, edited_by, updated_at FROM ocr_text "
                                         f"WHERE node_id IN {IN_IDS}", (q,))
        tables["user_state"] = _rows(conn, "SELECT node_id, favourite, pinned_order, opened_at, hidden, pin_cell, seen_at, "
                                           f"seen_sha FROM user_state WHERE user_id = ? AND node_id IN {IN_IDS}", (uid, q))
        tables["shares"] = _rows(conn, "SELECT s.node_id, s.user_id, s.role, s.viewers_tick, s.added_at, u.name, "
                                       "u.username FROM shares s LEFT JOIN users u ON u.id = s.user_id "
                                       f"WHERE s.node_id IN {IN_IDS} AND s.via_link = 0", (q,))
        if opts.get("versions", True):
            for v in conn.execute(f"SELECT * FROM versions WHERE node_id IN {IN_IDS} ORDER BY node_id, n", (q,)).fetchall():
                vdir = versions.version_dir(root, v["node_id"])
                real = os.path.join(vdir, os.path.basename(v["file"]))
                if not _regular(real, vdir):
                    continue
                out_files.append((f".versions/{v['node_id']}/{os.path.basename(v['file'])}", real))
                total += os.path.getsize(real)
                tables["versions"].append({k: v[k] for k in ("node_id", "n", "file", "size", "sha256", "made_by",
                                                             "saved_by", "created_at")})
        for name, real in _template_files(root):
            out_files.append((f"{templates.DIR}/{name}", real))
            total += os.path.getsize(real)
        targets = _ids_json(_targets(rid, ids))
        tables["follows"] = _rows(conn, f"SELECT target, created_at FROM follows WHERE user_id = ? AND target IN {IN_IDS}",
                                  (uid, targets))
        tables["filing_rules"] = _rows(conn, "SELECT target, position, pattern, type, min_kb, max_kb, rename, move_to, "
                                             f"created_at FROM filing_rules WHERE target IN {IN_IDS} ORDER BY target, "
                                             "position", (targets,))
        tables["cleanup_rules"] = _rows(conn, "SELECT target, action, days, created_at FROM cleanup_rules "
                                              f"WHERE target IN {IN_IDS}", (targets,))
        if len(out_files) > MAX_FILES or total > MAX_BYTES:
            raise HTTPException(413, f"That's more than {MAX_FILES:,} files or {MAX_BYTES // 1024 ** 3} GB — use the "
                                     "whole-app backup with files (Admin → Backup) instead.")
    tables["saved_searches"] = _rows(conn, "SELECT name, query, filters, pinned, created_at FROM saved_searches "
                                           "WHERE user_id = ? ORDER BY name", (uid,))
    tables["keep_imports"] = _rows(conn, "SELECT keep_id, node_id, imported_at FROM keep_imports WHERE user_id = ?", (uid,))
    return {"tables": tables, "records": {"elsewhere": elsewhere}, "files": out_files, "bytes": total,
            "person": {"id": user["id"], "username": user["username"], "name": user["name"]},
            "root": root["id"] if root is not None else None}


def _write(dest, plan: dict, admin: dict, opts: dict, progress=None) -> dict:
    return pd.write_package(dest, app=APP, app_version=config.APP_VERSION, schema=SCHEMA, person=plan["person"],
                            exported_by=admin.get("username") or admin["id"], tables=plan["tables"],
                            records=plan["records"], files=plan["files"], options=opts, title=APP_LABEL,
                            what="their documents and files", progress=progress)


def clean_options(body, keys=("versions", "trash")) -> dict:
    raw = (body or {}).get("options") if isinstance(body, dict) else None
    raw = raw if isinstance(raw, dict) else {}
    out = {"versions": True, "trash": False, "share": False}
    for k in keys:
        if k in raw:
            if not isinstance(raw[k], bool):
                raise HTTPException(422, f"The option {k} is true or false.")
            out[k] = raw[k]
    return {k: out[k] for k in keys}


def _log(action: str, admin: dict, uid: str, counts) -> None:
    logger.info("Person data %s by %s for %s: %s", action, admin.get("username") or admin["id"], uid,
                ", ".join(f"{k} {v}" for k, v in counts.items()))


def export(admin: dict, uid: str, body) -> dict:
    """{"bytes": the .zip} for a small export, or {"job": id}; and the file name."""
    opts = clean_options(body)
    with db.get_conn() as conn:
        user = _person(conn, uid)
        root = roots.person_root(conn, uid)
    if root is not None:
        roots.docs_real()
        index.scan_root(root["id"])                  # the index as the disk is now (the file follows the index)
    with db.get_conn() as conn:
        plan = collect(conn, uid, opts)
        db.audit(conn, "person_data_exported", admin["id"], None, plan["root"])
    name = pd.file_name(APP_LABEL, user["name"], now=config.now())
    counts = {k: len(v) for k, v in plan["tables"].items() if v}
    counts["files"] = len(plan["files"])
    _log("export", admin, uid, counts)
    if len(plan["files"]) <= SMALL_FILES and plan["bytes"] <= SMALL_BYTES:
        import io
        buf = io.BytesIO()
        _write(buf, plan, admin, opts)
        return {"bytes": buf.getvalue(), "filename": name}
    from .common import backup_core
    backup_core.remove_stale(config.DATA_DIR, EXPORT_PREFIX, pd.KEEP_SECONDS, suffixes=(".zip", ".part"))

    def run(progress):
        path = pd.temp_path(config.DATA_DIR, prefix=EXPORT_PREFIX)
        try:
            _write(path, plan, admin, opts, progress)
        except OSError as e:                         # the job's error is shown: never a file's path or name
            os.remove(path)
            logger.warning("A person's data export failed: [Errno %s] %s", e.errno, e.strerror or type(e).__name__)
            raise RuntimeError("A file changed or went while the export was made — try again.") from None
        except BaseException:
            os.remove(path)
            logger.exception("A person's data export failed")
            raise
        return path

    return {"job": JOBS.start(run, by=admin["id"], filename=name), "filename": name}


# =====================================================================================================================
# import: the checks
# =====================================================================================================================
class RowError(HTTPException):
    def __init__(self, table: str, n: int, message: str):
        super().__init__(422, f"The file can't be imported: {table}, row {n}: {message}")


_HEX64 = re.compile(r"^[0-9a-f]{64}$")


def _text(v, n: int = 200, *, required: bool = False, what: str = "a value"):
    if v is None and not required:
        return None
    if not isinstance(v, str) or (required and not v) or len(v) > n:
        raise ValueError(f"{what} is text of at most {n} characters")
    return v


def _int(v, lo: int = 0, hi: int = 2 ** 53, *, required: bool = False, what: str = "a number"):
    if v is None and not required:
        return None
    if isinstance(v, bool) or not isinstance(v, int) or not lo <= v <= hi:
        raise ValueError(f"{what} is a whole number from {lo} to {hi}")
    return v


def _flag(v) -> int:
    if v in (0, 1, True, False, None):
        return 1 if v else 0
    raise ValueError("a yes / no value is 0 or 1")


def _when(v):
    return _text(v, 40, what="a time")


def _sha(v):
    if v is None:
        return None
    if not isinstance(v, str) or not _HEX64.match(v):
        raise ValueError("sha256 is 64 hex digits")
    return v


def _id(v, what="an id", required=True):
    return _text(v, 64, required=required, what=what)


def _rel(v, trashed: str | None) -> str:
    """A path inside their folder (or, trashed, `.trash/<id>/…`): every part a usable name."""
    _text(v, 4000, required=True, what="rel")
    parts = v.split("/")
    if trashed is not None:
        if len(parts) < 3 or parts[0] + "/" != TRASH_PREFIX or parts[1] != trashed:
            raise ValueError("a trashed item's rel is .trash/<trash id>/<name>")
        parts = parts[2:]
    if len(parts) > files.ZIP_DEPTH + 2:
        raise ValueError("the folders go too deep")
    for p in parts:
        problem = paths.name_problem(p)
        if problem:
            raise ValueError(f"the name “{p[:60]}”: {problem}")
    return v


def _checked(table: str, rows: list, check) -> list:
    out = []
    for i, r in enumerate(rows):
        try:
            out.append(check(r))
        except (ValueError, TypeError, KeyError) as e:
            raise RowError(table, i + 1, str(e) or "not a valid row") from None
        except HTTPException as e:
            raise RowError(table, i + 1, str(e.detail)) from None
    return out


def _check_node(r):
    kind = r.get("kind")
    if kind not in {k.id for k in kinds.all_kinds()}:
        raise ValueError("kind is one of " + ", ".join(k.id for k in kinds.all_kinds()))
    tid = _id(r.get("trash_id"), "trash_id", required=False)
    return {"id": _id(r.get("id")), "rel": _rel(r.get("rel"), tid), "parent_id": _id(r.get("parent_id"), required=False),
            "kind": kind, "color": tagmod.clean_colour(r.get("color")), "size": _int(r.get("size"), what="size"),
            "sha256": _sha(r.get("sha256")), "mtime": _when(r.get("mtime")),
            "created_by": _id(r.get("created_by"), required=False), "updated_by": _id(r.get("updated_by"), required=False),
            "trash_id": tid}


def _check_tag(r):
    return {"node_id": _id(r.get("node_id")), "tag": tagmod.clean_tag(r.get("tag")),
            "added_by": _id(r.get("added_by"), required=False), "added_at": _when(r.get("added_at"))}


def _check_link(r):
    return {"from_id": _id(r.get("from_id")), "text": _text(r.get("text"), 400, required=True, what="text"),
            "to_id": _id(r.get("to_id"), required=False)}


def _check_tick(r):
    return {"node_id": _id(r.get("node_id")), "item_key": _text(r.get("item_key"), 128, required=True, what="item_key"),
            "done_by": _id(r.get("done_by"), required=False), "done_at": _when(r.get("done_at"))}


def _check_ocr(r):
    return {"node_id": _id(r.get("node_id")), "sha256": _sha(r.get("sha256")),
            "text": _text(r.get("text"), 2_000_000, required=False, what="text") or "",
            "model": _text(r.get("model"), 100, what="model"), "edited_by": _id(r.get("edited_by"), required=False),
            "updated_at": _when(r.get("updated_at"))}


def _check_state(r):
    cell = r.get("pin_cell")
    if cell is not None:
        from . import pins
        cell = json.dumps(pins.clean_cell(json.loads(_text(cell, 200, what="pin_cell"))))
    return {"node_id": _id(r.get("node_id")), "favourite": _flag(r.get("favourite")),
            "pinned_order": _int(r.get("pinned_order"), 0, 10 ** 9, what="pinned_order"),
            "opened_at": _when(r.get("opened_at")), "hidden": _flag(r.get("hidden")), "pin_cell": cell,
            "seen_at": _when(r.get("seen_at")), "seen_sha": _sha(r.get("seen_sha"))}


def _check_version(r):
    n = _int(r.get("n"), 1, 10 ** 6, required=True, what="n")
    name = _text(r.get("file"), 60, required=True, what="file")
    if paths.name_problem(name) or "/" in name:
        raise ValueError("file is a version's file name")
    return {"node_id": _id(r.get("node_id")), "n": n, "file": name, "size": _int(r.get("size"), required=True, what="size"),
            "sha256": _sha(r.get("sha256")) or "", "made_by": _id(r.get("made_by"), required=False),
            "saved_by": _id(r.get("saved_by"), required=False), "created_at": _when(r.get("created_at")) or config.now_iso()}


def _check_trash(r):
    return {"id": _id(r.get("id")), "node_id": _id(r.get("node_id")),
            "original_rel": _text(r.get("original_rel"), 4000, required=True, what="original_rel"),
            "deleted_by": _id(r.get("deleted_by"), required=False), "deleted_at": _when(r.get("deleted_at")) or config.now_iso()}


def _check_share(r):
    role = r.get("role")
    if role not in ROLES:
        raise ValueError("role is viewer, editor or manager")
    return {"node_id": _id(r.get("node_id")), "user_id": _text(r.get("user_id"), 128, required=True, what="user_id"),
            "role": role, "viewers_tick": _flag(r.get("viewers_tick", 1)), "added_at": _when(r.get("added_at")),
            "name": _text(r.get("name"), 100, what="name"), "username": _text(r.get("username"), 100, what="username")}


def _check_search(r):
    f = r.get("filters") or "{}"
    filters = json.loads(_text(f, 4000, what="filters"))
    if not isinstance(filters, dict):
        raise ValueError("filters is an object")
    return {"name": saved_mod.clean_name(r.get("name")), "query": _text(r.get("query") or "", 400, what="query"),
            "filters": json.dumps(filters, sort_keys=True, separators=(",", ":")), "pinned": _flag(r.get("pinned")),
            "created_at": _when(r.get("created_at")) or config.now_iso()}


def _check_follow(r):
    return {"target": _text(r.get("target"), 70, required=True, what="target"), "created_at": _when(r.get("created_at"))}


def _check_filing(r):
    pat = r.get("pattern")
    if not isinstance(pat, str) or not 1 <= len(pat.strip()) <= 200 or "/" in pat or "\\" in pat:
        raise ValueError("the name pattern is 1–200 characters (no folders)")
    t = r.get("type")
    if t is not None and t not in filing.TYPES:
        raise ValueError("type is " + ", ".join(filing.TYPES))
    rn = _text(r.get("rename"), 200, what="rename")
    if rn:
        bad = [m for m in filing.PLACEHOLDER_RE.findall(rn) if m not in filing.PLACEHOLDERS]
        if bad:
            raise ValueError(f"unknown {{{bad[0]}}} in rename")
    return {"target": _text(r.get("target"), 70, required=True, what="target"),
            "position": _int(r.get("position"), 0, 10 ** 6, required=True, what="position"), "pattern": pat.strip(),
            "type": t, "min_kb": _int(r.get("min_kb"), 0, 100_000_000, what="min_kb"),
            "max_kb": _int(r.get("max_kb"), 0, 100_000_000, what="max_kb"), "rename": rn or None,
            "move_to": _text(r.get("move_to"), 70, what="move_to"), "created_at": _when(r.get("created_at"))}


def _check_cleanup(r):
    if r.get("action") not in ("trash", "archive"):
        raise ValueError("action is trash or archive")
    return {"target": _text(r.get("target"), 70, required=True, what="target"), "action": r["action"],
            "days": _int(r.get("days"), 1, 3650, required=True, what="days"), "created_at": _when(r.get("created_at"))}


def _check_keep(r):
    return {"keep_id": _text(r.get("keep_id"), 200, required=True, what="keep_id"),
            "node_id": _id(r.get("node_id"), required=False), "imported_at": _when(r.get("imported_at")) or config.now_iso()}


def _check_prefs(r):
    from .routers.me import PREF_DEFAULTS, PrefsIn
    p = r.get("prefs")
    if not isinstance(p, dict):
        raise ValueError("prefs is an object")
    known = {k: v for k, v in p.items() if k in PREF_DEFAULTS}
    try:
        return {"prefs": PrefsIn(**known).model_dump(exclude_none=True)}
    except Exception as e:                            # pydantic's ValidationError: the first problem, plainly
        errs = getattr(e, "errors", lambda: [])()
        raise ValueError(f"{errs[0]['loc'][0]}: {errs[0]['msg']}" if errs else "a setting isn't valid") from None


CHECKS = {"prefs": _check_prefs, "nodes": _check_node, "node_tags": _check_tag, "node_links": _check_link,
          "checklist_ticks": _check_tick, "user_state": _check_state, "ocr_text": _check_ocr,
          "versions": _check_version, "trash": _check_trash, "shares": _check_share, "saved_searches": _check_search,
          "follows": _check_follow, "filing_rules": _check_filing, "cleanup_rules": _check_cleanup,
          "keep_imports": _check_keep}


def _upgrade(tables: dict, schema: int) -> dict:
    """An older file brought up to SCHEMA (nothing yet: SCHEMA 1 is the first)."""
    return tables


def check_package(pkg: pd.Package) -> dict:
    """Every row checked with the app's own rules (unknown columns dropped); file members matched to their rows.
    Returns {table: [clean rows]}; raises RowError (422, with the table and row number)."""
    raw = _upgrade(dict(pkg.tables), pkg.schema)
    out = {t: _checked(t, raw.get(t) or [], CHECKS[t]) for t in TABLES}
    names = set(pkg.file_names)
    seen = set()
    trash_ids = {t["id"] for t in out["trash"]}
    for i, n in enumerate(out["nodes"]):
        if n["id"] in seen:
            raise RowError("nodes", i + 1, "the same id twice")
        seen.add(n["id"])
        if n["trash_id"] is not None and n["trash_id"] not in trash_ids:
            raise RowError("nodes", i + 1, "its Trash item isn't in the file")
        if n["kind"] != "folder" and "files/" + n["rel"] not in names:
            raise RowError("nodes", i + 1, "its file isn't in the export")
    for i, v in enumerate(out["versions"]):
        if f"files/.versions/{v['node_id']}/{v['file']}" not in names:
            raise RowError("versions", i + 1, "its file isn't in the export")
    if len(out["prefs"]) > 1:
        raise RowError("prefs", 2, "one row only")
    return out


def _template_members(pkg) -> list[str]:
    pre = f"files/{templates.DIR}/"
    return [n for n in pkg.file_names if n.startswith(pre) and "/" not in n[len(pre):]
            and paths.name_problem(n[len(pre):]) is None]


def _open(path: str) -> pd.Package:
    try:
        return pd.read_package(path, app=APP, schema=SCHEMA, limits=LIMITS, tables=TABLES)
    except pd.PackageError as e:
        raise HTTPException(422, str(e))


COUNT_LABELS = (("nodes:files", "files"), ("nodes:folders", "folders"), ("versions", "old versions"),
                ("trash", "items in Trash"), ("templates", "templates"), ("node_tags", "tags"),
                ("shares", "shares they gave"), ("saved_searches", "saved searches"), ("follows", "follows"),
                ("filing_rules", "filing rules"), ("cleanup_rules", "clean-up rules"), ("user_state", "favourites and pins"))


def _file_counts(rows: dict, pkg) -> dict:
    live = [n for n in rows["nodes"] if n["trash_id"] is None]
    return {"nodes:files": sum(1 for n in live if n["kind"] != "folder"),
            "nodes:folders": sum(1 for n in live if n["kind"] == "folder"),
            "versions": len(rows["versions"]), "trash": len(rows["trash"]), "templates": len(_template_members(pkg)),
            "node_tags": len(rows["node_tags"]), "shares": len(rows["shares"]),
            "saved_searches": len(rows["saved_searches"]), "follows": len(rows["follows"]),
            "filing_rules": len(rows["filing_rules"]), "cleanup_rules": len(rows["cleanup_rules"]),
            "user_state": len(rows["user_state"])}


def preview(admin: dict, tmp: str) -> dict:
    """Check an uploaded file; keep it (an hour) under a token; what the dialog shows."""
    try:
        pkg = _open(tmp)
        with pkg:
            rows = check_package(pkg)
            counts = _file_counts(rows, pkg)
            m = pkg.manifest
            person = pkg.person
    except BaseException:
        if os.path.exists(tmp):
            os.remove(tmp)
        raise
    warnings = []
    if pkg.schema < SCHEMA:
        warnings.append("Made by an older version of Household Docs — it's brought up to date as it's read.")
    opts = m.get("options") if isinstance(m.get("options"), dict) else {}
    if opts.get("versions") is False:
        warnings.append("Old versions were left out of this file.")
    if rows["shares"]:
        warnings.append("Shares come back only if you tick “Also share with the same people again”, and only with "
                        "people who are here.")
    target = None
    with db.get_conn() as conn:
        pid, pun = str(person.get("id") or "")[:128], person.get("username")
        r = conn.execute("SELECT id FROM users WHERE id = ?", (pid,)).fetchone() if pid else None
        if r is None and isinstance(pun, str) and pun:
            r = conn.execute("SELECT id FROM users WHERE username = ?", (pun[:100],)).fetchone()
        target = r["id"] if r else None
    token = uploads().add(tmp, admin["id"], {"name": str(person.get("name") or "")[:100]})
    return {"token": token, "preview": {
        "app": APP_LABEL, "appVersion": str(m.get("appVersion") or "")[:20], "exportedAt": m.get("exportedAt"),
        "exportedBy": str(m.get("exportedBy") or "")[:100],
        "person": {"id": str(person.get("id") or "")[:128], "name": str(person.get("name") or "")[:100],
                   "username": str(person.get("username") or "")[:100]},
        "counts": pd.count_list((k, label, counts[k]) for k, label in COUNT_LABELS),
        "warnings": warnings,
        "options": [{"key": "share", "label": "Also share with the same people again", "default": False,
                     "help": "Only with people who are here; Everyone only when App settings allow it."}],
        "target": target}}


# =====================================================================================================================
# import: doing it
# =====================================================================================================================
def _replace_clear(conn, admin: dict, user, root) -> int:
    """Replace, step 1 (its own transaction): their folder's contents to their Trash, and their own rows that the
    file brings again (searches, follows, rules on their folder, Keep records)."""
    top = nodes.children(conn, root["id"], None)
    for n in top:
        trash.trash_node(conn, admin["id"], n)
    uid = user["id"]
    for t in ("saved_searches", "follows", "keep_imports"):
        conn.execute(f"DELETE FROM {t} WHERE user_id = ?", (uid,))
    targets = _ids_json(_targets(root["id"], [r["id"] for r in conn.execute(
        f"SELECT n.id FROM nodes n WHERE n.root_id = ? AND {nodes.LIVE}", (root["id"],))]))
    for t in ("filing_rules", "cleanup_rules"):
        conn.execute(f"DELETE FROM {t} WHERE target IN {IN_IDS}", (targets,))
    return len(top)


def _iso_ts(v) -> float | None:
    t = config.parse_iso(v) if v else None
    return t.timestamp() if t else None


def run_import(admin: dict, uid: str, token: str, body) -> dict:
    body = body if isinstance(body, dict) else {}
    mode = body.get("mode", "add")
    if mode not in ("add", "replace"):
        raise HTTPException(422, "The mode is add or replace.")
    opts = clean_options(body, ("share",))
    item = uploads().get(token, admin["id"])
    if item is None:
        raise HTTPException(404, "That file isn't here any more (it's kept for an hour) — choose it again.")
    try:
        with pd.one_at_a_time(_LOCK):
            out = _import(admin, uid, item["path"], mode, body.get("confirm"), opts)
    except pd.Busy as e:
        raise HTTPException(423, str(e))
    uploads().drop(token)
    return out


def _import(admin: dict, uid: str, path: str, mode: str, confirm, opts: dict) -> dict:
    with db.get_conn() as conn:
        user = _person(conn, uid)
        if mode == "replace" and not pd.names_match(confirm, user["name"]):
            raise HTTPException(409, f"Type {user['name']} to replace what they have.")
    roots.docs_real()
    fileio.ensure_writable()
    pkg = _open(path)
    with pkg:
        rows = check_package(pkg)
        return _load(admin, uid, pkg, rows, mode, opts)


def _load(admin: dict, uid: str, pkg: pd.Package, rows: dict, mode: str, opts: dict) -> dict:
    old_pid = str(pkg.person.get("id") or "")
    with db.get_conn() as conn:
        user = _person(conn, uid)
        root = roots.ensure_person_root(conn, uid)
        if root is None:
            raise roots.DocsUnavailable()
    if mode == "replace":
        with db.get_conn() as conn:
            _replace_clear(conn, admin, user, root)
    index.scan_root(root["id"])                       # their folder as it is now: duplicates are judged against it
    base = roots.root_real(root)
    with db.get_conn() as conn:
        live = {r["rel"]: r for r in conn.execute(f"SELECT * FROM nodes n WHERE n.root_id = ? AND {nodes.LIVE}",
                                                  (root["id"],))}
        known_users = {r["id"] for r in conn.execute("SELECT id FROM users")}
        limit = files.upload_limit(conn)
    shas = pkg.manifest.get("sha256") or {}
    prefix = ""
    if mode == "add" and live:                        # Add next to what they have: in a folder of its own (§7.3)
        prefix = paths.unique_name(base, paths.clean_name(f"Imported {config.now().strftime('%Y-%m-%d')}"))
    # what goes in: duplicates (same path, same contents) and files over the upload limit are left out
    skipped, too_big = set(), []
    for n in rows["nodes"]:
        if n["trash_id"] is not None or n["kind"] == "folder":
            continue
        cur = live.get(n["rel"])
        if mode == "add" and cur is not None and cur["kind"] != "folder" and cur["sha256"] \
                and cur["sha256"] == shas.get("files/" + n["rel"]):
            skipped.add(n["id"])
        elif pkg._zip.getinfo("files/" + n["rel"]).file_size > limit:
            too_big.append(n["rel"])
            skipped.add(n["id"])
    if mode == "add":
        # a folder that is there already and would bring nothing new (only duplicates) is a duplicate too
        def above(rel):
            while "/" in rel:
                rel = rel.rsplit("/", 1)[0]
                yield rel

        live_rows = [n for n in rows["nodes"] if n["trash_id"] is None]
        needed = {a for n in live_rows if n["kind"] != "folder" and n["id"] not in skipped for a in above(n["rel"])}
        for n in sorted((x for x in live_rows if x["kind"] == "folder"), key=lambda x: -x["rel"].count("/")):
            cur = live.get(n["rel"])
            if cur is not None and cur["kind"] == "folder" and n["rel"] not in needed:
                skipped.add(n["id"])                                   # deepest first: its insides are decided
            else:
                needed.update(above(n["rel"]))
    node_rows = [n for n in rows["nodes"] if n["id"] not in skipped]
    keep_ids = {n["id"] for n in node_rows}
    if prefix and not any(n["trash_id"] is None for n in node_rows):
        prefix = ""                                   # nothing new for their folder: no empty Imported folder
    version_rows = [v for v in rows["versions"] if v["node_id"] in keep_ids]
    tdir = os.path.join(base, templates.DIR)
    tmpl = []
    for m in _template_members(pkg):                  # the same template there already: a duplicate
        cur = os.path.join(tdir, m.rsplit("/", 1)[1])
        if os.path.isfile(cur) and not os.path.islink(cur) and fileio.sha256_file(cur) == shas.get(m):
            skipped.add(m)
        else:
            tmpl.append(m)
    need = sum(pkg._zip.getinfo("files/" + n["rel"]).file_size for n in node_rows if n["kind"] != "folder")
    need += sum(pkg._zip.getinfo(f"files/.versions/{v['node_id']}/{v['file']}").file_size for v in version_rows)
    need += sum(pkg._zip.getinfo(n).file_size for n in tmpl)
    with db.get_conn() as conn:
        docops.check_quota(conn, root, need)
    fileio.ensure_room(need)
    # the files, unpacked into the root's .tmp first (nothing in their folder yet)
    stage = os.path.join(roots.hidden_dir(root, ".tmp"), "person-import-" + pd.new_token()[:12])
    os.makedirs(stage)
    try:
        staged: dict = {}
        k = 0
        for n in node_rows:
            if n["kind"] != "folder":
                k += 1
                staged["files/" + n["rel"]] = os.path.join(stage, str(k))
        for v in version_rows:
            k += 1
            staged[f"files/.versions/{v['node_id']}/{v['file']}"] = os.path.join(stage, str(k))
        for name in tmpl:
            k += 1
            staged[name] = os.path.join(stage, str(k))
        for member, dest in staged.items():
            pkg.extract(member, dest)
        result = _commit(admin, uid, user, root, rows, node_rows, version_rows, prefix, old_pid, known_users, opts,
                         mode)
        _place(root, base, result, staged, tmpl)
    finally:
        shutil.rmtree(stage, ignore_errors=True)
    index.scan_root(root["id"])                       # read and index what came in, as a scan does
    files_n = sum(1 for n in node_rows if n["kind"] != "folder" and n["trash_id"] is None)
    counts = {"files": files_n, "folders": sum(1 for n in node_rows if n["kind"] == "folder" and n["trash_id"] is None),
              "versions": len(version_rows), "trash": result["trash_items"], "templates": len(tmpl),
              "skipped": len(skipped) - len(too_big) + result["skipped_rows"], "tooBig": len(too_big)}
    _log("import", admin, uid, counts)
    parts = [f"{files_n} {_plural(files_n, 'file')}"]
    for key, one in (("folders", "folder"), ("versions", "old version"), ("trash", "item in Trash"),
                     ("templates", "template")):
        if counts[key]:
            parts.append(f"{counts[key]} {_plural(counts[key], one, 'items in Trash' if key == 'trash' else None)}")
    msg = "Imported " + ", ".join(parts)
    if prefix:
        msg += f" into “{prefix}”"
    if counts["skipped"]:
        msg += f"; {counts['skipped']} already there {'was' if counts['skipped'] == 1 else 'were'} skipped"
    if too_big:
        msg += f"; {len(too_big)} {_plural(len(too_big), 'file')} over the upload limit left out"
    return {"message": msg + ".", "counts": counts}


def _commit(admin, uid, user, root, rows, node_rows, version_rows, prefix, old_pid, known_users, opts, mode) -> dict:
    """The rows, in one transaction (new ids for everything). Returns what _place needs."""
    ids = pd.IdMap()
    rid = root["id"]
    now = config.now_iso()

    def who(old):
        """A person named in a row: them (the file's person) → the one it goes to; someone here → as is; anyone
        else → Former member."""
        if old is None:
            return None
        if old == old_pid:
            return uid
        return old if old in known_users else FORMER_ID

    with db.get_conn() as conn:
        folder_name = trash._owner_folder(conn, root)
        top_id = None
        if prefix:
            top_id = nodes.insert(conn, root_id=rid, rel=prefix, parent_id=None, kind="folder", created_by=uid,
                                  updated_by=uid)
        new_trash = {}
        for t in rows["trash"]:
            new_trash[t["id"]] = db.new_id()
        placed = []                                       # (member, rel or trash place, mtime) for _place
        folders = [prefix] if prefix else []
        by_rel = {}                                       # new rel → new id: a node's folder is the one its path says
        # live nodes first (a trashed item's folder may be one of them), each pass parents first
        order = sorted(node_rows, key=lambda x: (x["trash_id"] is not None, x["rel"].count("/")))
        for n in order:
            nid = db.new_id()
            ids.put("nodes", n["id"], nid)
            if n["trash_id"] is None:
                rel = paths.join_rel(prefix, n["rel"]) if prefix else n["rel"]
                prel = paths.parent_rel(rel)
                parent = by_rel.get(prel) if prel != prefix else top_id
                if prel and parent is None:
                    raise RowError("nodes", rows["nodes"].index(n) + 1, "its folder isn't in the file")
            else:
                tid = new_trash[n["trash_id"]]
                rest = n["rel"].split("/", 2)[2]
                rel = f"{TRASH_PREFIX}{tid}/{rest}"
                parent = by_rel.get(paths.parent_rel(rel)) if "/" in rest else ids.get("nodes", n["parent_id"])
            by_rel[rel] = nid
            nodes.free_rel(conn, rid, rel)
            nodes.insert(conn, root_id=rid, rel=rel, parent_id=parent, kind=n["kind"], sha=n["sha256"],
                         created_by=who(n["created_by"]), updated_by=who(n["updated_by"]), node_id=nid)
            conn.execute("UPDATE nodes SET size = ?, mtime = ?, color = ?, trash_id = ? WHERE id = ?",
                         (n["size"] if n["kind"] != "folder" else None, n["mtime"], n["color"],
                          new_trash.get(n["trash_id"]) if n["trash_id"] else None, nid))
            if n["kind"] == "folder":
                if n["trash_id"] is None:
                    folders.append(rel)
                else:
                    placed.append((None, rel, None))
            else:
                placed.append(("files/" + n["rel"], rel, n["mtime"]))
        trash_items = 0
        for t in rows["trash"]:
            nid = ids.get("nodes", t["node_id"])
            if nid is None:
                continue
            tid = new_trash[t["id"]]
            orig = paths.join_rel(prefix, t["original_rel"]) if prefix else t["original_rel"]
            conn.execute("INSERT INTO trash (id, node_id, root_id, original_rel, trash_rel, owner_id, deleted_by, "
                         "deleted_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                         (tid, nid, rid, orig[:4000], f"{folder_name}/{tid}", uid, who(t["deleted_by"]), t["deleted_at"]))
            trash_items += 1
        skipped_rows = 0
        for r in rows["node_tags"]:
            nid = ids.get("nodes", r["node_id"])
            if nid:
                conn.execute("INSERT OR IGNORE INTO node_tags (node_id, tag, tag_folded, added_by, added_at) "
                             "VALUES (?, ?, ?, ?, ?)", (nid, r["tag"], tagmod.fold(r["tag"]), who(r["added_by"]),
                                                        r["added_at"] or now))
        for r in rows["node_links"]:
            nid = ids.get("nodes", r["from_id"])
            if nid:
                conn.execute("INSERT OR IGNORE INTO node_links (from_id, text, to_id) VALUES (?, ?, ?)",
                             (nid, r["text"], ids.get("nodes", r["to_id"])))
        for r in rows["checklist_ticks"]:
            nid = ids.get("nodes", r["node_id"])
            if nid:
                conn.execute("INSERT OR IGNORE INTO checklist_ticks (node_id, item_key, done_by, done_at) "
                             "VALUES (?, ?, ?, ?)", (nid, r["item_key"], who(r["done_by"]), r["done_at"]))
        for r in rows["ocr_text"]:
            nid = ids.get("nodes", r["node_id"])
            if nid:
                conn.execute("INSERT OR REPLACE INTO ocr_text (node_id, sha256, text, model, edited_by, updated_at) "
                             "VALUES (?, ?, ?, ?, ?, ?)", (nid, r["sha256"], r["text"], r["model"], who(r["edited_by"]),
                                                           r["updated_at"] or now))
        for r in rows["user_state"]:
            nid = ids.get("nodes", r["node_id"])
            if nid:
                conn.execute("INSERT OR IGNORE INTO user_state (user_id, node_id, favourite, pinned_order, opened_at, "
                             "hidden, pin_cell, seen_at, seen_sha) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                             (uid, nid, r["favourite"], r["pinned_order"], r["opened_at"], r["hidden"], r["pin_cell"],
                              r["seen_at"], r["seen_sha"]))
        vplaced = []
        for v in version_rows:
            nid = ids.get("nodes", v["node_id"])
            if nid is None:
                continue
            conn.execute("INSERT OR IGNORE INTO versions (node_id, n, file, size, sha256, made_by, saved_by, created_at) "
                         "VALUES (?, ?, ?, ?, ?, ?, ?, ?)", (nid, v["n"], v["file"], v["size"], v["sha256"],
                                                             who(v["made_by"]), who(v["saved_by"]), v["created_at"]))
            vplaced.append((f"files/.versions/{v['node_id']}/{v['file']}", nid, v["file"]))
        if opts.get("share"):
            everyone = bool(settings.get("everyone_shares", conn))
            for s in rows["shares"]:
                nid = ids.get("nodes", s["node_id"])
                if nid is None:
                    continue
                target = None
                if s["user_id"] == "*":
                    target = "*" if everyone else None
                elif s["user_id"] in known_users and s["user_id"] != old_pid:
                    target = s["user_id"]
                elif s["username"]:
                    r = conn.execute("SELECT id FROM users WHERE username = ?", (s["username"],)).fetchone()
                    target = r["id"] if r else None
                if target is None or target == uid:
                    continue
                conn.execute("INSERT OR IGNORE INTO shares (node_id, user_id, role, viewers_tick, added_by, added_at) "
                             "VALUES (?, ?, ?, ?, ?, ?)", (nid, target, s["role"], s["viewers_tick"], uid, now))

        def target_of(t):
            """A rule's or follow's place: an imported folder's new id; the top of their old folder → the top of
            what came in (the Imported folder, or their folder's top)."""
            if t.startswith("root:"):
                return top_id if prefix else "root:" + rid
            return ids.get("nodes", t)

        have = {(r["name"].casefold(), r["query"], r["filters"]) for r in conn.execute(
            "SELECT name, query, filters FROM saved_searches WHERE user_id = ?", (uid,))}
        count = len(have)
        for r in rows["saved_searches"]:
            key = (r["name"].casefold(), r["query"], r["filters"])
            if key in have or count >= saved_mod.MAX_SAVED:
                skipped_rows += 1
                continue
            have.add(key)
            count += 1
            conn.execute("INSERT INTO saved_searches (id, user_id, name, query, filters, pinned, created_at) "
                         "VALUES (?, ?, ?, ?, ?, ?, ?)", (db.new_id(), uid, r["name"], r["query"], r["filters"],
                                                          r["pinned"], r["created_at"]))
        for r in rows["follows"]:
            t = target_of(r["target"])
            if t:
                cur = conn.execute("INSERT OR IGNORE INTO follows (user_id, target, created_at, cursor) VALUES (?, ?, ?, ?)",
                                   (uid, t, r["created_at"] or now, now))
                skipped_rows += cur.rowcount == 0
        for r in rows["filing_rules"]:
            t = target_of(r["target"])
            if not t:
                continue
            mv = target_of(r["move_to"]) if r["move_to"] else None
            if r["move_to"] and not mv:
                continue                                   # its folder didn't come: the rule would do nothing right
            same = conn.execute("SELECT 1 FROM filing_rules WHERE target = ? AND pattern = ? AND type IS ? "
                                "AND rename IS ? AND move_to IS ?", (t, r["pattern"], r["type"], r["rename"], mv)).fetchone()
            if same:
                skipped_rows += 1
                continue
            conn.execute("INSERT INTO filing_rules (id, target, position, pattern, type, min_kb, max_kb, rename, move_to, "
                         "created_by, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                         (db.new_id(), t, r["position"], r["pattern"], r["type"], r["min_kb"], r["max_kb"], r["rename"],
                          mv, uid, r["created_at"] or now))
        for r in rows["cleanup_rules"]:
            t = target_of(r["target"])
            if t:
                cur = conn.execute("INSERT OR IGNORE INTO cleanup_rules (target, action, days, created_by, created_at) "
                                   "VALUES (?, ?, ?, ?, ?)", (t, r["action"], r["days"], uid, r["created_at"] or now))
                skipped_rows += cur.rowcount == 0
        for r in rows["keep_imports"]:
            cur = conn.execute("INSERT OR IGNORE INTO keep_imports (user_id, keep_id, node_id, imported_at) "
                               "VALUES (?, ?, ?, ?)", (uid, r["keep_id"], ids.get("nodes", r["node_id"]), r["imported_at"]))
            skipped_rows += cur.rowcount == 0
        if rows["prefs"]:
            from .routers.me import PREF_DEFAULTS
            try:
                cur = json.loads(user["prefs"] or "{}")
            except ValueError:
                cur = {}
            cur = cur if isinstance(cur, dict) else {}
            new = rows["prefs"][0]["prefs"]
            if mode == "add":                              # what they set here wins
                merged = dict(new, **{k: v for k, v in cur.items() if k in PREF_DEFAULTS})
            else:
                merged = dict(cur, **new)
            conn.execute("UPDATE users SET prefs = ? WHERE id = ?", (json.dumps(merged), uid))
        db.audit(conn, "person_data_imported", admin["id"], None, rid)
    trash_places = {new_trash[t["id"]]: dict(t, new_node=ids.get("nodes", t["node_id"])) for t in rows["trash"]}
    return {"placed": placed, "folders": folders, "versions": vplaced, "trash_items": trash_items,
            "skipped_rows": skipped_rows, "trash_folder": folder_name, "trash_ids": trash_places, "ids": ids,
            "admin": admin["id"]}


def _place(root, base: str, result: dict, staged: dict, tmpl: list) -> None:
    """After the commit: the folders made, the files moved in (never over a name taken meanwhile), versions,
    templates and Trash items put where the app keeps them. A file that can't be placed is logged; the next scan
    marks its row gone."""
    for rel in sorted(result["folders"], key=lambda r: r.count("/")):
        os.makedirs(paths.resolve(base, rel), exist_ok=True)
    trash_base = roots.hidden_dir(root, ".trash")
    made_items = set()
    for member, rel, mtime in result["placed"]:
        try:
            if rel.startswith(TRASH_PREFIX):
                tid, rest = rel[len(TRASH_PREFIX):].split("/", 1)
                tdir = os.path.join(trash_base, result["trash_folder"], tid)
                dest = os.path.join(tdir, *rest.split("/"))
                if tid not in made_items:
                    os.makedirs(tdir, exist_ok=True)
                    t = result["trash_ids"][tid]
                    item = {"name": rest.split("/")[0], "originalPath": t["original_rel"], "nodeId": t["new_node"],
                            "rootId": root["id"], "deletedBy": t["deleted_by"], "deletedAt": t["deleted_at"]}
                    with open(os.path.join(tdir, ".item.json"), "w", encoding="utf-8") as f:
                        json.dump(item, f, indent=1)
                    made_items.add(tid)
                if member is None:
                    os.makedirs(dest, exist_ok=True)
                    continue
                os.makedirs(os.path.dirname(dest), exist_ok=True)
            else:
                dest = paths.resolve(base, rel)
                os.makedirs(os.path.dirname(dest), exist_ok=True)
            os.chmod(staged[member], fileio.FILE_MODE)
            fileio.place(staged[member], dest, exclusive=True)
            ts = _iso_ts(mtime)
            if ts:
                os.utime(dest, (ts, ts), follow_symlinks=False)
        except (OSError, paths.PathError) as e:
            logger.warning("An imported file couldn't be put in place: %s", getattr(e, "strerror", None) or e)
    for member, nid, name in result["versions"]:
        try:
            d = versions.version_dir(root, nid)
            os.makedirs(d, exist_ok=True)
            fileio.place(staged[member], os.path.join(d, name), exclusive=True)
        except (OSError, paths.PathError) as e:
            logger.warning("An imported version couldn't be put in place: %s", getattr(e, "strerror", None) or e)
    if tmpl:
        d = os.path.join(base, templates.DIR)
        if not os.path.islink(d):
            os.makedirs(d, exist_ok=True)
            for member in tmpl:
                name = member.rsplit("/", 1)[1]
                try:
                    fileio.place(staged[member], os.path.join(d, paths.unique_name(d, name)), exclusive=True)
                except OSError as e:
                    logger.warning("An imported template couldn't be put in place: %s", e.strerror or e)


# =====================================================================================================================
# delete
# =====================================================================================================================
FORMER_COLUMNS = (("nodes", "created_by"), ("nodes", "updated_by"), ("versions", "made_by"), ("versions", "saved_by"),
                  ("activity", "actor_id"), ("activity", "target_user"), ("checklist_ticks", "done_by"),
                  ("node_tags", "added_by"), ("shares", "added_by"), ("trash", "deleted_by"), ("ocr_text", "edited_by"),
                  ("filing_log", "actor_id"), ("filing_log", "undone_by"), ("view_links", "created_by"),
                  ("filing_rules", "created_by"), ("cleanup_rules", "created_by"), ("ha_sensors", "created_by"),
                  ("ai_folders", "added_by"), ("kid_parents", "added_by"), ("roots", "created_by"))


def _tidy_node_refs(conn, ids: list, targets: list) -> list[str]:
    """Rows elsewhere that name nodes going for good (no foreign key does it). Returns the sensors to remove."""
    q, tq = _ids_json(ids), _ids_json(targets)
    gone = [r["entity_id"] for r in conn.execute(f"SELECT entity_id FROM ha_sensors WHERE node_id IN {IN_IDS}", (tq,))]
    conn.execute(f"DELETE FROM ha_sensors WHERE node_id IN {IN_IDS}", (tq,))
    conn.execute(f"UPDATE node_links SET to_id = NULL WHERE to_id IN {IN_IDS}", (q,))
    for t in ("follows", "filing_rules", "cleanup_rules", "ai_folders"):
        conn.execute(f"DELETE FROM {t} WHERE target IN {IN_IDS}", (tq,))
    conn.execute(f"UPDATE filing_rules SET move_to = NULL WHERE move_to IN {IN_IDS}", (tq,))
    for t in ("filing_queue", "filing_log", "activity", "bus_requests", "todo_sends"):
        conn.execute(f"DELETE FROM {t} WHERE node_id IN {IN_IDS}", (q,))
    conn.execute(f"UPDATE keep_imports SET node_id = NULL WHERE node_id IN {IN_IDS}", (q,))
    return gone


def _clear_person(conn, uid: str) -> None:
    """Their own rows outside their folder (§7.4 "Then")."""
    conn.execute("UPDATE users SET prefs = NULL, seen_from = ? WHERE id = ?", (config.now_iso(), uid))
    for t in ("user_state", "saved_searches", "recent_searches", "follows", "user_notify", "keep_imports",
              "ai_person_month", "bus_requests", "todo_sends", "shares", "root_access"):
        conn.execute(f"DELETE FROM {t} WHERE user_id = ?", (uid,))
    conn.execute("DELETE FROM kid_parents WHERE child_id = ? OR parent_id = ?", (uid, uid))


def _give(conn, admin, user, root, target_id: str, renames: list) -> dict:
    """Their folder's live contents into the target's folder as "From <name>": the same nodes (ids, versions, tags,
    shares follow), one rename per top item. `renames` collects (from, to) for undoing a failed transaction."""
    target = _person(conn, target_id)
    if target["disabled"]:
        raise HTTPException(409, f"{target['name']} can't use Household Docs (an admin has turned it off for them).")
    new_root = roots.ensure_person_root(conn, target_id)
    if new_root is None:
        raise roots.DocsUnavailable()
    top = nodes.children(conn, root["id"], None)
    ids = [r["id"] for r in conn.execute(f"SELECT n.id FROM nodes n WHERE n.root_id = ? AND {nodes.LIVE}", (root["id"],))]
    size = conn.execute(f"SELECT COALESCE(SUM(size), 0) FROM nodes WHERE id IN {IN_IDS}", (_ids_json(ids),)).fetchone()[0]
    docops.check_quota(conn, new_root, size)
    if not top:
        return {"moved": 0, "ids": [], "to": target["name"], "folder": None}
    base = roots.root_real(new_root)
    src_base = roots.root_real(root)
    folder = paths.unique_name(base, paths.clean_name(f"From {user['name']}"))
    dest_dir = paths.resolve(base, folder)
    st = fileio.make_dir(dest_dir)
    renames.append((None, dest_dir))
    fid = nodes.insert(conn, root_id=new_root["id"], rel=folder, parent_id=None, kind="folder", st=st,
                       created_by=target_id, updated_by=target_id)
    for n in top:
        src = paths.resolve(src_base, n["rel"])
        dest = paths.resolve(base, paths.join_rel(folder, n["name"]))
        nodes.relocate(conn, n, root_id=new_root["id"], rel=paths.join_rel(folder, n["name"]), parent_id=fid)
        os.rename(src, dest)
        renames.append((src, dest))
    q = _ids_json(ids)
    conn.execute(f"DELETE FROM shares WHERE user_id = ? AND node_id IN {IN_IDS}", (target_id, q))   # theirs now
    db.audit(conn, "person_docs_given", admin["id"], fid, new_root["id"])
    return {"moved": len(top), "ids": ids, "to": target["name"], "folder": folder}


def delete(admin: dict, uid: str, body) -> dict:
    body = body if isinstance(body, dict) else {}
    forget = body.get("forget", False)
    choices = body.get("choices") if isinstance(body.get("choices"), dict) else {}
    choice = choices.get("folder", "trash")
    if not isinstance(forget, bool):
        raise HTTPException(422, "forget is true or false.")
    if not isinstance(choice, str) or not (choice in ("trash", "delete") or choice.startswith("give:")):
        raise HTTPException(422, "Choose Move to Trash, Give to someone or Delete for good.")
    with db.get_conn() as conn:
        user = _person(conn, uid)
    if not pd.names_match(body.get("confirm"), user["name"]):
        raise HTTPException(409, f"Type {user['name']} to confirm.")
    if forget and choice == "trash":
        raise HTTPException(422, "To forget them, choose Give to someone or Delete for good.")
    if forget and uid == admin["id"]:
        raise HTTPException(409, "You can't forget yourself while you're signed in — another admin can.")
    if choice.startswith("give:") and choice[5:] == uid:
        raise HTTPException(422, "Choose someone else to give their folder to.")
    try:
        with pd.one_at_a_time(_LOCK):
            return _delete(admin, user, choice, forget)
    except pd.Busy as e:
        raise HTTPException(423, str(e))


def _delete(admin: dict, user, choice: str, forget: bool) -> dict:
    uid = user["id"]
    roots.docs_real()
    fileio.ensure_writable()
    with db.get_conn() as conn:
        root = roots.person_root(conn, uid)
        before = _counts(conn, user, root)
    if choice == "trash":
        with db.get_conn() as conn:
            moved = 0
            if root is not None:
                for n in nodes.children(conn, root["id"], None):
                    trash.trash_node(conn, admin["id"], n)
                    moved += 1
            db.audit(conn, "person_docs_deleted", admin["id"], None, root["id"] if root else None)
        _log("delete (to Trash)", admin, uid, {"moved": moved})
        return {"message": f"Moved {moved} {_plural(moved, 'item')} to {user['name']}'s Trash.", "moved": moved}
    if root is not None:
        index.scan_root(root["id"])
    renames: list = []
    after: dict = {"rm": [], "versions": [], "sensors": []}
    given = None
    try:
        with db.get_conn() as conn:
            if root is not None:
                rid = root["id"]
                base = roots.root_real(root)
                folder = trash._owner_folder(conn, root)
                all_ids = _root_ids(conn, rid)
                if choice.startswith("give:"):
                    given = _give(conn, admin, user, root, choice[5:], renames)
                    gone_ids = [i for i in all_ids if i not in set(given["ids"])]
                    # rules on what they gave go (§7.4), its sensors stay with the items
                    gq = _ids_json(given["ids"])
                    for t in ("filing_rules", "cleanup_rules", "ai_folders"):
                        conn.execute(f"DELETE FROM {t} WHERE target IN {IN_IDS}", (gq,))
                else:
                    gone_ids = all_ids
                after["versions"] = list(gone_ids)
                after["sensors"] = _tidy_node_refs(conn, gone_ids, _targets(rid, gone_ids))
                nodes.delete_rows(conn, gone_ids)
                conn.execute("DELETE FROM trash WHERE owner_id = ? OR root_id = ?", (uid, rid))
                after["rm"].append(os.path.join(roots.hidden_dir(root, ".trash"), folder))
                after["base"] = base
                if forget:
                    conn.execute("DELETE FROM storage_daily WHERE root_id = ?", (rid,))
                    conn.execute("DELETE FROM roots WHERE id = ?", (rid,))
            _clear_person(conn, uid)
            if forget:
                for table, col in FORMER_COLUMNS:
                    conn.execute(f"UPDATE {table} SET {col} = ? WHERE {col} = ?", (FORMER_ID, uid))
                conn.execute("DELETE FROM users WHERE id = ?", (uid,))
            db.audit(conn, "person_data_forgotten" if forget else "person_data_deleted", admin["id"], None,
                     root["id"] if root is not None else None)
    except BaseException:
        for src, dest in reversed(renames):              # the database stayed as it was: so do the files
            try:
                if src is None:
                    os.rmdir(dest)
                else:
                    os.rename(dest, src)
            except OSError:
                logger.warning("Putting a file back after a failed delete didn't work")
        raise
    # files after the commit: a failure here leaves only a few files to tidy
    if root is not None:
        for nid in after["versions"]:
            versions.remove_all(root, nid)
        for d in after["rm"]:
            shutil.rmtree(d, ignore_errors=True)
        base = after.get("base")
        if base:
            if forget:
                shutil.rmtree(base, ignore_errors=True)
            else:
                for e in os.scandir(base):
                    if e.is_dir(follow_symlinks=False):
                        shutil.rmtree(e.path, ignore_errors=True)
                    else:
                        try:
                            os.remove(e.path)
                        except OSError:
                            pass
    if after["sensors"]:
        from . import ha_sensors
        ha_sensors.forget(after["sensors"])
    counts = {"files": before["files"], "folders": before["folders"], "versions": before["versions"],
              "trash": before["trash"], "sharesTo": before["sharesTo"], "kids": before["kids"], "rules": before["rules"]}
    _log("forget" if forget else "delete", admin, uid, dict(counts, given=bool(given)))
    if given:
        msg = (f"Gave {given['moved']} {_plural(given['moved'], 'item')} to {given['to']}"
               + (f" (in “{given['folder']}”)" if given["folder"] else "") + f"; deleted {user['name']}'s Trash")
    else:
        msg = (f"Deleted {before['files']} {_plural(before['files'], 'file')} ({pd.bytes_text(before['bytes'])}), "
               f"{before['versions']} old {_plural(before['versions'], 'version')} and {before['trash']} "
               f"{_plural(before['trash'], 'item')} in Trash")
    msg += f", and their own settings, searches and follows. {user['name']} " + (
        "is forgotten." if forget else "stays, empty.")
    return {"message": msg, "counts": counts, "forgotten": forget}
