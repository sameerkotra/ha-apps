"""The storage report (SPEC §17.8).

**For each folder** — a person's own folder, an admin shared folder — `root_report()` gives: files, folders and
bytes; the files by type (for the bar chart); the largest files; files not changed in N years; empty folders;
duplicates (the same SHA-256 — every copy the viewer can open, wherever it is; *Keep one* moves the others the
viewer may delete to Trash); the space History (`.versions`) and Trash take; growth over 12 months; and the
person's `quota_gb`.

**Who sees what.** Each person sees the full report for their own folder and for admin shared folders they can
read and write (`GET /api/reports/storage`). **Admin → Storage** shows every folder's totals — sizes, counts,
types, History, Trash, growth, quota, duplicate counts — and `/data`, plus *Empty all Trash now*; the lists of
file names stay with the people who can open those files (admins get no content access as admins, §3.2 — a
decision recorded in the spec).

**Growth** comes from `storage_daily`: one row per root per day (written by housekeeping). Months before the
first snapshot are estimated from the index (when each file still there was first seen) and marked as such.
"""
import os
from datetime import date

from fastapi import HTTPException

from . import config, db, docops, settings, sharing
from .search import filters as flt
from .store import nodes, roots, trash

LARGEST = 20
OLD_LIST = 50
EMPTY_LIST = 100
DUP_GROUPS = 50


def _months(today: date, n: int = 12) -> list[str]:
    out = []
    y, m = today.year, today.month
    for _ in range(n):
        out.append(f"{y:04d}-{m:02d}")
        m -= 1
        if m == 0:
            y, m = y - 1, 12
    return list(reversed(out))


def totals(conn, root_id: str) -> dict:
    r = conn.execute(f"SELECT COUNT(*) AS files, COALESCE(SUM(n.size), 0) AS bytes FROM nodes n WHERE n.root_id = ? "
                     f"AND n.kind != 'folder' AND {nodes.LIVE}", (root_id,)).fetchone()
    f = conn.execute(f"SELECT COUNT(*) FROM nodes n WHERE n.root_id = ? AND n.kind = 'folder' AND {nodes.LIVE}",
                     (root_id,)).fetchone()[0]
    return {"files": r["files"], "bytes": r["bytes"], "folders": f}


def by_type(conn, root_id: str) -> list[dict]:
    acc: dict = {}
    for r in conn.execute(f"SELECT n.kind, n.ext, COUNT(*) AS c, COALESCE(SUM(n.size), 0) AS b FROM nodes n "
                          f"WHERE n.root_id = ? AND n.kind != 'folder' AND {nodes.LIVE} GROUP BY n.kind, n.ext", (root_id,)):
        label = flt.type_label_of(r["kind"], r["ext"])
        a = acc.setdefault(label, {"type": label, "files": 0, "bytes": 0})
        a["files"] += r["c"]
        a["bytes"] += r["b"]
    return sorted(acc.values(), key=lambda x: -x["bytes"])


def hidden_use(conn, root_id: str) -> dict:
    """History (.versions) and Trash, from the index: what the app keeps there for this folder."""
    v = conn.execute("SELECT COUNT(*) AS c, COALESCE(SUM(v.size), 0) AS b FROM versions v JOIN nodes n ON n.id = v.node_id "
                     "WHERE n.root_id = ?", (root_id,)).fetchone()
    t = conn.execute("SELECT COUNT(*) AS c, COALESCE(SUM(n.size), 0) AS b FROM nodes n WHERE n.root_id = ? "
                     "AND n.trash_id IS NOT NULL AND n.kind != 'folder'", (root_id,)).fetchone()
    items = conn.execute("SELECT COUNT(*) FROM trash WHERE root_id = ?", (root_id,)).fetchone()[0]
    return {"versions": {"count": v["c"], "bytes": v["b"]}, "trash": {"items": items, "files": t["c"], "bytes": t["b"]}}


def growth(conn, root_id: str, current_bytes: int, current_files: int) -> list[dict]:
    """12 months: the last snapshot of each month (this month: now); before the first snapshot, an estimate."""
    today = config.now().date()
    months = _months(today)
    snaps = {}
    for r in conn.execute("SELECT day, files, bytes FROM storage_daily WHERE root_id = ? AND day >= ? ORDER BY day",
                          (root_id, months[0] + "-01")):
        snaps[r["day"][:7]] = {"files": r["files"], "bytes": r["bytes"]}
    first = conn.execute("SELECT MIN(day) FROM storage_daily WHERE root_id = ?", (root_id,)).fetchone()[0]
    est: dict = {}
    if first is None or first[:7] > months[0]:
        run_b = run_f = 0
        per = {r["m"]: (r["c"], r["b"]) for r in conn.execute(
            f"SELECT substr(n.ctime, 1, 7) AS m, COUNT(*) AS c, COALESCE(SUM(n.size), 0) AS b FROM nodes n "
            f"WHERE n.root_id = ? AND n.kind != 'folder' AND {nodes.LIVE} GROUP BY m", (root_id,))}
        for m, (c, b) in sorted((k, v) for k, v in per.items() if k and k < months[0]):
            run_f += c
            run_b += b
        for m in months:
            c, b = per.get(m, (0, 0))
            run_f += c
            run_b += b
            est[m] = {"files": run_f, "bytes": run_b}
    out = []
    for m in months:
        if m == months[-1]:
            out.append({"month": m, "files": current_files, "bytes": current_bytes, "estimated": False})
        elif m in snaps:
            out.append({"month": m, **snaps[m], "estimated": False})
        elif first is None or m < first[:7]:
            out.append({"month": m, **est.get(m, {"files": 0, "bytes": 0}), "estimated": True})
        else:
            out.append({"month": m, "files": None, "bytes": None, "estimated": False})
    return out


def dup_summary(conn, root_id: str, user: dict | None = None) -> dict:
    """Duplicate groups touching this folder: how many, and the bytes the extra copies take (no names). Only copies
    the person can open count (`user`) — never copies elsewhere they can't, so the numbers say nothing about other
    people's files. Without a person (the admins' overview), only copies inside this same folder count."""
    if user is not None:
        acc_sql, acc_params = sharing.access_cte(user["id"])
        rows = conn.execute(
            acc_sql + f"SELECT n.sha256, n.size, COUNT(*) AS c FROM nodes n JOIN acc ON acc.node_id = n.id "
            f"JOIN roots r ON r.id = n.root_id WHERE n.sha256 IN ("
            f" SELECT m.sha256 FROM nodes m WHERE m.root_id = ? AND m.kind != 'folder' AND m.size > 0 "
            f" AND m.sha256 IS NOT NULL AND m.gone_at IS NULL AND m.trash_id IS NULL) AND n.kind != 'folder' "
            f"AND {nodes.LIVE} AND (r.kind = 'person' OR r.missing = 0) "
            "GROUP BY n.sha256, n.size HAVING COUNT(*) > 1", (*acc_params, root_id)).fetchall()
    else:
        rows = conn.execute(
            f"SELECT n.sha256, n.size, COUNT(*) AS c FROM nodes n WHERE n.root_id = ? AND n.kind != 'folder' "
            f"AND n.size > 0 AND n.sha256 IS NOT NULL AND {nodes.LIVE} "
            "GROUP BY n.sha256, n.size HAVING COUNT(*) > 1", (root_id,)).fetchall()
    return {"groups": len(rows), "extraBytes": sum((r["c"] - 1) * (r["size"] or 0) for r in rows)}


def quota(conn, root) -> dict | None:
    gb = int(settings.get("quota_gb", conn))
    if not gb or root["kind"] != "person":
        return None
    return {"gb": gb, "bytes": gb * 1024 ** 3}


def summary(conn, root, user: dict | None = None) -> dict:
    t = totals(conn, root["id"])
    out = {"rootId": root["id"], "kind": root["kind"], "label": root["label"], **t,
           "byType": by_type(conn, root["id"]), **hidden_use(conn, root["id"]),
           "growth": growth(conn, root["id"], t["bytes"], t["files"]), "duplicates": dup_summary(conn, root["id"], user),
           "quota": quota(conn, root), "missing": bool(root["missing"])}
    if root["kind"] == "person":
        u = conn.execute("SELECT name, folder FROM users WHERE id = ?", (root["user_id"],)).fetchone()
        out["personName"] = u["name"] if u else None
        out["folder"] = u["folder"] if u else None
    return out


# ---------------------------------------------------------------- the detailed lists (for people who can open the files)
def _items(conn, user: dict, rows) -> list[dict]:
    from .routers.nodes import rows_json
    from .search.engine import location
    out = rows_json(conn, user, rows, role_for=lambda r: sharing.role_of(conn, user, r))
    for it, r in zip(out, rows):
        rr = dict(r)
        root = roots.root_row(conn, r["root_id"])
        rr.update(root_kind=root["kind"], root_label=root["label"], root_owner=root["user_id"])
        it["location"] = " › ".join(location(conn, _Row(rr), user))
    return out


class _Row(dict):
    def __getitem__(self, k):
        return dict.get(self, k)


def details(conn, user: dict, root, years: int = 2) -> dict:
    rid = root["id"]
    largest = conn.execute(f"SELECT n.* FROM nodes n WHERE n.root_id = ? AND n.kind != 'folder' AND {nodes.LIVE} "
                           "ORDER BY n.size DESC, n.name_folded LIMIT ?", (rid, LARGEST)).fetchall()
    cutoff = config.ago_iso(days=365 * years)
    old_count = conn.execute(f"SELECT COUNT(*) AS c, COALESCE(SUM(n.size), 0) AS b FROM nodes n WHERE n.root_id = ? "
                             f"AND n.kind != 'folder' AND n.mtime < ? AND {nodes.LIVE}", (rid, cutoff)).fetchone()
    old = conn.execute(f"SELECT n.* FROM nodes n WHERE n.root_id = ? AND n.kind != 'folder' AND n.mtime < ? AND {nodes.LIVE} "
                       "ORDER BY n.mtime LIMIT ?", (rid, cutoff, OLD_LIST)).fetchall()
    empty = conn.execute(f"SELECT n.* FROM nodes n WHERE n.root_id = ? AND n.kind = 'folder' AND {nodes.LIVE} AND NOT EXISTS "
                         f"(SELECT 1 FROM nodes c WHERE c.parent_id = n.id AND c.gone_at IS NULL AND c.trash_id IS NULL) "
                         "ORDER BY n.rel LIMIT ?", (rid, EMPTY_LIST)).fetchall()
    return {"largest": _items(conn, user, largest), "old": {"years": years, "files": old_count["c"],
                                                            "bytes": old_count["b"], "items": _items(conn, user, old)},
            "emptyFolders": _items(conn, user, empty), "duplicateGroups": duplicates(conn, user, rid)}


def duplicates(conn, user: dict, root_id: str) -> list[dict]:
    """Groups of files with the same content, at least one copy in this folder; every copy the person can open."""
    acc_sql, acc_params = sharing.access_cte(user["id"])
    shas = conn.execute(
        f"SELECT n.sha256, n.size FROM nodes n WHERE n.root_id = ? AND n.kind != 'folder' AND n.size > 0 "
        f"AND n.sha256 IS NOT NULL AND {nodes.LIVE} GROUP BY n.sha256, n.size ORDER BY n.size DESC", (root_id,)).fetchall()
    groups = []
    for s in shas:
        rows = conn.execute(acc_sql + f"SELECT n.*, acc.rank AS acc_rank FROM nodes n JOIN acc ON acc.node_id = n.id "
                            f"JOIN roots r ON r.id = n.root_id WHERE n.sha256 = ? AND n.size = ? AND n.kind != 'folder' "
                            f"AND {nodes.LIVE} AND (r.kind = 'person' OR r.missing = 0) ORDER BY n.mtime",
                            (*acc_params, s["sha256"], s["size"])).fetchall()
        if len(rows) < 2:
            continue
        groups.append({"sha256": s["sha256"], "size": s["size"], "copies": _items(conn, user, rows),
                       "extraBytes": (len(rows) - 1) * s["size"]})
        if len(groups) >= DUP_GROUPS:
            break
    return groups


def person_report(conn, user: dict, ref: str | None, years: int = 2) -> dict:
    """The report for the person's own folder (ref None / "mine") or an admin shared folder they can write."""
    if not ref or ref == "mine":
        root = docops.my_root(conn, user)
    elif ref.startswith("root:"):
        root = roots.root_row(conn, ref[5:])
        role = sharing.root_role(conn, user, root) if root is not None else None
        if root is None or role is None:
            raise HTTPException(404, sharing.NOT_FOUND)
        if root["kind"] != "shared" or not sharing.at_least(role, "editor"):
            raise HTTPException(403, "The storage report is for your own folder and shared folders you can change.")
    else:
        raise HTTPException(422, "Choose My docs or one of your shared folders.")
    out = summary(conn, root, user)
    out.update(details(conn, user, root, years))
    from . import share_folders
    out["places"] = [{"ref": "mine", "label": "My docs"}] + [
        {"ref": "root:" + f["rootId"], "label": f["label"]} for f in share_folders.for_user(conn, user) if f["mode"] == "rw"]
    return out


def keep_one(conn, user: dict, keep_id: str) -> dict:
    """Move every other copy of this file (same SHA-256) that the person may delete to Trash."""
    keep, _role = sharing.require(conn, user, keep_id, "viewer")
    if keep["kind"] == "folder" or not keep["sha256"]:
        raise HTTPException(422, "Pick a file to keep.")
    acc_sql, acc_params = sharing.access_cte(user["id"])
    others = conn.execute(acc_sql + f"SELECT n.id FROM nodes n JOIN acc ON acc.node_id = n.id WHERE n.sha256 = ? "
                          f"AND n.size = ? AND n.id != ? AND n.kind != 'folder' AND {nodes.LIVE}",
                          (*acc_params, keep["sha256"], keep["size"], keep["id"])).fetchall()
    removed, skipped = 0, 0
    for r in others:
        try:
            docops.delete(conn, user, r["id"])
            removed += 1
        except HTTPException:
            skipped += 1
    return {"removed": removed, "skipped": skipped}


# ---------------------------------------------------------------- admin
def data_use() -> dict:
    """/data: the database (with its WAL) and everything else there."""
    dbb = 0
    for suffix in ("", "-wal", "-shm"):
        try:
            dbb += os.path.getsize(config.DB_PATH + suffix)
        except OSError:
            pass
    total = 0
    for dirpath, _dirs, files_ in os.walk(config.DATA_DIR):
        for f in files_:
            try:
                total += os.lstat(os.path.join(dirpath, f)).st_size
            except OSError:
                pass
    try:
        st = os.statvfs(config.DATA_DIR)
        free = st.f_bavail * st.f_frsize
    except OSError:
        free = None
    return {"database": dbb, "total": total, "free": free}


def admin_report(conn) -> dict:
    rows = conn.execute("SELECT * FROM roots ORDER BY kind, label COLLATE NOCASE").fetchall()
    folders_ = [summary(conn, r) for r in rows]
    trash_items = conn.execute("SELECT COUNT(*) FROM trash").fetchone()[0]
    return {"folders": folders_, "data": data_use(), "trashItems": trash_items,
            "totals": {"files": sum(f["files"] for f in folders_), "bytes": sum(f["bytes"] for f in folders_),
                       "versions": sum(f["versions"]["bytes"] for f in folders_),
                       "trash": sum(f["trash"]["bytes"] for f in folders_)}}


def empty_all_trash(conn) -> int:
    from .store import fileio
    fileio.ensure_writable()
    rows = conn.execute("SELECT * FROM trash").fetchall()
    for t in rows:
        trash._purge_item(conn, t)
    return len(rows)


def snapshot(force: bool = False) -> int:
    """Today's row per root (housekeeping, hourly; once a day)."""
    day = config.now().date().isoformat()
    n = 0
    with db.get_conn() as conn:
        for r in conn.execute("SELECT id FROM roots").fetchall():
            if not force and conn.execute("SELECT 1 FROM storage_daily WHERE day = ? AND root_id = ?", (day, r["id"])).fetchone():
                continue
            t = totals(conn, r["id"])
            conn.execute("INSERT INTO storage_daily (day, root_id, files, bytes) VALUES (?, ?, ?, ?) "
                         "ON CONFLICT(day, root_id) DO UPDATE SET files = excluded.files, bytes = excluded.bytes",
                         (day, r["id"], t["files"], t["bytes"]))
            n += 1
        conn.execute("DELETE FROM storage_daily WHERE day < ?", (f"{config.now().year - 2:04d}-01-01",))
        conn.execute("DELETE FROM storage_daily WHERE root_id NOT IN (SELECT id FROM roots)")
    return n
