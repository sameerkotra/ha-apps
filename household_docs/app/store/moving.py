"""Moving the documents folder to a new location (SPEC §5.7): the admin copies the files, the app checks the copy
and switches. The app never copies, moves or deletes the documents folder itself.

- **Read-only mode** (`read_only` in app_settings, kept across restarts): every write through the app is refused
  (`guard`, one of `fileio.WRITE_GUARDS`, answers 423 with "Documents are being moved …"), and the background
  jobs that write files (Trash clean-up, version pruning, temp-file clean-up, people's first folders) pause.
  Every page shows a banner. Reading, searching and downloading keep working.
- **Check copy** (`start_check`, a background thread, one at a time): both folders are walked — everything,
  hidden folders included, except `.tmp` and the marker, which is looked at on its own — and compared by
  relative path: counts and sizes on both sides; *missing* in the new location; *different* (quick: size, or
  modified time more than 2 s apart — FAT and SMB keep times to 2 s; deep: size or SHA-256, so a copy that
  didn't keep times is fine when its content is the same); *extra* files only in the new location; whether
  `people/`, `.trash`, `.versions` and the marker are there; free space. Lists are capped at 200 lines in the
  answer; the full report is a CSV.
- **Switch** (`switch`): needs a finished check of that location with nothing missing or different — or
  `force` plus a typed confirmation of that number. It writes the marker in the new folder, puts `moved_to` and
  the date into the old folder's marker (the only change to the old folder), changes `docs_path` and every
  person root's path in one transaction, re-indexes (node ids are kept because every node is stored by its
  path relative to its root: `people/Priya/Trip/Budget.xlsx` is the same row in both places), turns read-only
  mode off and returns who to tell. **Switch back** is the same check and switch towards the previous location
  while it still exists.
"""
import hashlib
import logging
import os
import shutil
import threading
import time

from fastapi import HTTPException

from .. import config, db, settings
from . import marker, paths, roots

logger = logging.getLogger("moving")

STATE_KEY = "read_only"
PREVIOUS_KEY = "docs_previous"
LIST_CAP = 200
MAX_ENTRIES = 1_000_000
TIME_SLACK_NS = 2_000_000_000            # FAT / SMB keep modified times to 2 seconds
SKIP_TOP = {".tmp", marker.MARKER, marker.MARKER + ".tmp"}
WANTED = ("people", ".trash", ".versions")
USABLE_TARGET = ("ours", "has_files", "empty", "new")


# ---------------------------------------------------------------- read-only mode
class ReadOnly(HTTPException):
    def __init__(self, state: dict):
        who = state.get("byName") or "an admin"
        super().__init__(423, f"Documents are being moved — you can read but not change anything until {who} finishes.")


def state(conn=None) -> dict | None:
    """{"on": True, "by", "byName", "since", "target"} while read-only mode is on, else None."""
    def read(c):
        v = db.state_get(c, STATE_KEY)
        return v if isinstance(v, dict) and v.get("on") else None
    if conn is not None:
        return read(conn)
    try:
        with db.get_conn() as c:
            return read(c)
    except Exception:                      # no database yet (first start): nothing is being moved
        return None


def is_read_only(conn=None) -> bool:
    return state(conn) is not None


def guard() -> None:
    """A write guard (store/fileio.WRITE_GUARDS): refuse every write while read-only mode is on."""
    s = state()
    if s is not None:
        raise ReadOnly(s)


def set_read_only(conn, on: bool, admin: dict, target: str | None = None) -> dict | None:
    cur = state(conn)
    if on:
        value = {"on": True, "by": admin["id"], "byName": admin.get("name") or admin.get("username") or "an admin",
                 "since": cur["since"] if cur else config.now_iso(), "target": target or (cur or {}).get("target")}
        db.state_set(conn, STATE_KEY, value, admin.get("username") or admin["id"])
        if cur is None:
            db.audit(conn, "read_only_on", admin["id"])
        return value
    db.state_set(conn, STATE_KEY, {"on": False}, admin.get("username") or admin["id"])
    if cur is not None:
        db.audit(conn, "read_only_off", admin["id"])
    return None


def public_state(conn=None) -> dict | None:
    """What every page shows (no paths for non-admins)."""
    s = state(conn)
    return None if s is None else {"on": True, "byName": s.get("byName"), "since": s.get("since")}


# ---------------------------------------------------------------- the new location
def target_info(path: str) -> dict:
    """Can `path` be the new location? {path, real, verdict, ok, message, markerOurs} (§5.6 rules, plus: not the
    current folder, not inside it or around it). It may be empty, new, or already hold the copy (with or
    without the marker)."""
    try:
        display = marker.clean_location(path)
    except paths.PathError as e:
        return {"path": path, "ok": False, "verdict": "refused", "message": str(e)}
    with db.get_conn() as conn:
        iid, shared = roots.install_id(conn), roots.shared_reals(conn)
        current = roots.configured(conn)
    cur_real = roots.docs_configured_real()
    info = marker.check(display, iid, shared, current_display=current)
    out = {"path": display, "verdict": info["verdict"], "ok": False, "message": info["message"],
           "exists": info.get("exists", False)}
    real = info.get("real")
    if real:
        if real == cur_real or display == current:
            out.update(verdict="current", message="That is the documents folder now.")
            return out
        if paths.inside(cur_real, real) or paths.inside(real, cur_real):
            out.update(verdict="nested", message="The new location can't be inside the documents folder, or around it.")
            return out
    if info["verdict"] in USABLE_TARGET:
        out["ok"] = True
        out["message"] = {"ours": "It holds a copy of this app's documents folder (with its marker).",
                          "has_files": "It already holds files — the check compares them with the documents folder.",
                          "empty": "An empty folder: copy the files into it first.",
                          "new": "This folder doesn't exist yet: copy the documents folder there first."}[info["verdict"]]
    out["real"] = real
    return out


def copy_commands(src_display: str, dst_display: str) -> dict:
    """The exact paths for the copy instructions (Samba, Terminal & SSH, a NAS tool)."""
    src_rel, dst_rel = config.share_rel(src_display), config.share_rel(dst_display)
    win = lambda rel: "\\\\homeassistant\\share\\" + rel.replace("/", "\\")        # noqa: E731
    return {"from": src_display, "to": dst_display,
            "rsync": f'rsync -a "{src_display}/" "{dst_display}/"',
            "cp": f'mkdir -p "{dst_display}" && cp -a "{src_display}/." "{dst_display}/"',
            "sambaFrom": win(src_rel), "sambaTo": win(dst_rel)}


# ---------------------------------------------------------------- the copy check (a background job)
_jobs: dict[str, dict] = {}
_jobs_lock = threading.Lock()


def _walk(base: str, job: dict, side: str) -> tuple[dict, bool]:
    """rel → (is_dir, size, mtime_ns) for everything under `base` (no links; not .tmp or the marker)."""
    out: dict = {}
    stack = [("", base)]
    capped = False
    while stack:
        rel, real = stack.pop()
        try:
            it = os.scandir(real)
        except OSError:
            continue
        with it:
            for e in it:
                if not rel and e.name in SKIP_TOP:
                    continue
                try:
                    if e.is_symlink():
                        continue
                    is_dir = e.is_dir(follow_symlinks=False)
                    if not is_dir and not e.is_file(follow_symlinks=False):
                        continue
                    st = e.stat(follow_symlinks=False)
                except OSError:
                    continue
                r = e.name if not rel else rel + "/" + e.name
                out[r] = (is_dir, 0 if is_dir else st.st_size, st.st_mtime_ns)
                if len(out) >= MAX_ENTRIES:
                    capped = True
                    return out, capped
                if is_dir:
                    stack.append((r, e.path))
        job["listed"][side] = len(out)
    return out, capped


def _sha(path: str, job: dict) -> str | None:
    h = hashlib.sha256()
    try:
        with open(path, "rb") as f:
            while True:
                chunk = f.read(1024 * 1024)
                if not chunk:
                    break
                h.update(chunk)
                job["bytesDone"] += len(chunk)
    except OSError:
        return None
    return h.hexdigest()


def _totals(entries: dict) -> dict:
    files = sum(1 for d, _s, _m in entries.values() if not d)
    return {"files": files, "folders": len(entries) - files, "bytes": sum(s for d, s, _m in entries.values() if not d)}


def _free(real: str) -> int | None:
    p = real
    while p and not os.path.isdir(p):
        p = os.path.dirname(p)
    return marker.free_space(p) if p else None


def _run(job: dict, src: str, dst: str) -> None:
    try:
        job["phase"] = "listing"
        a, cap_a = _walk(src, job, "from")
        b, cap_b = _walk(dst, job, "to") if os.path.isdir(dst) else ({}, False)
        job["capped"] = cap_a or cap_b
        job["phase"] = "comparing"
        missing, different, extra = [], [], []
        to_hash = []
        for rel, (is_dir, size, mt) in a.items():
            other = b.get(rel)
            if other is None:
                missing.append({"path": rel, "folder": is_dir, "size": size})
                continue
            if is_dir != other[0]:
                different.append({"path": rel, "why": "a folder on one side, a file on the other", "size": size})
                continue
            if is_dir:
                continue
            if size != other[1]:
                different.append({"path": rel, "why": "size", "size": size, "newSize": other[1]})
            elif job["deep"]:
                to_hash.append((rel, size))
            elif abs(mt - other[2]) > TIME_SLACK_NS:
                different.append({"path": rel, "why": "modified time", "size": size})
        for rel, (is_dir, size, _mt) in b.items():
            if rel not in a:
                extra.append({"path": rel, "folder": is_dir, "size": size})
        if to_hash:
            job["phase"] = "hashing"
            job["bytesTotal"] = sum(s for _r, s in to_hash) * 2
            for rel, size in to_hash:
                if job.get("cancel"):
                    raise RuntimeError("cancelled")
                if _sha(os.path.join(src, rel), job) != _sha(os.path.join(dst, rel), job):
                    different.append({"path": rel, "why": "content (SHA-256)", "size": size})
                job["hashed"] += 1
        key = lambda x: x["path"]                                     # noqa: E731
        for lst in (missing, different, extra):
            lst.sort(key=key)
        mk = marker.read_marker(dst) if os.path.isdir(dst) else None
        job["result"] = {
            "from": _totals(a), "to": _totals(b),
            "missing": missing, "different": different, "extra": extra,
            "missingFiles": sum(1 for x in missing if not x["folder"]),
            "hidden": {name: os.path.isdir(os.path.join(dst, name)) for name in WANTED},
            "hiddenFrom": {name: os.path.isdir(os.path.join(src, name)) for name in WANTED},
            "marker": {"present": mk is not None, "ours": bool(mk) and mk.get("install") == job["install"]},
            "free": _free(dst), "needed": max(0, sum(x["size"] for x in missing if not x["folder"])
                                               + sum(max(0, x["size"] - x.get("newSize", 0)) for x in different)),
        }
        job["phase"] = "done"
    except Exception as e:                  # noqa: BLE001 — the page shows what went wrong
        logger.exception("The copy check failed")
        job["phase"] = "failed"
        job["error"] = "The check stopped: " + (str(e) or e.__class__.__name__)
    finally:
        job["finishedAt"] = config.now_iso()
        job["ms"] = int((time.monotonic() - job["_t0"]) * 1000)


def start_check(path: str, deep: bool, admin: dict, *, wait: bool = False) -> dict:
    info = target_info(path)
    if not info["ok"]:
        raise HTTPException(409, info["message"])
    src = roots.docs_real()
    with _jobs_lock:
        if any(j["phase"] in ("listing", "comparing", "hashing") for j in _jobs.values()):
            raise HTTPException(409, "A check is already running — wait for it to finish.")
        jid = db.new_id()
        with db.get_conn() as conn:
            iid = roots.install_id(conn)
            current = roots.configured(conn)
            db.audit(conn, "docs_location_checked", admin["id"])
        job = {"id": jid, "path": info["path"], "real": info["real"], "fromPath": current, "fromReal": src,
               "deep": bool(deep), "phase": "queued", "startedAt": config.now_iso(), "finishedAt": None,
               "listed": {"from": 0, "to": 0}, "hashed": 0, "bytesDone": 0, "bytesTotal": 0, "install": iid,
               "readOnlyAtStart": is_read_only(), "_t0": time.monotonic(), "result": None, "error": None}
        for old in sorted(_jobs.values(), key=lambda j: j["startedAt"])[:-4]:
            _jobs.pop(old["id"], None)
        _jobs[jid] = job
    t = threading.Thread(target=_run, args=(job, src, info["real"]), name="docs-copy-check", daemon=True)
    t.start()
    if wait:
        t.join()
    return job_json(jid)


def get_job(job_id: str) -> dict:
    job = _jobs.get(job_id) if isinstance(job_id, str) else None
    if job is None:
        raise HTTPException(404, "That check isn't known (the app may have restarted) — run it again.")
    return job


def job_json(job_id: str, cap: int = LIST_CAP) -> dict:
    job = get_job(job_id)
    out = {k: v for k, v in job.items() if not k.startswith("_") and k not in ("real", "fromReal", "result", "install")}
    r = job["result"]
    if r is not None:
        res = dict(r)
        for k in ("missing", "different", "extra"):
            res[k + "Count"] = len(r[k])
            res[k] = r[k][:cap]
        res["problems"] = len(r["missing"]) + len(r["different"])
        out["result"] = res
    if job["bytesTotal"]:
        out["progress"] = min(1.0, job["bytesDone"] / job["bytesTotal"])
    return out


def report_rows(job_id: str):
    job = get_job(job_id)
    r = job["result"]
    if r is None:
        raise HTTPException(409, "The check hasn't finished yet.")
    from ..common import csv_export
    rows = []
    for what, key in (("Missing in the new location", "missing"), ("Different", "different"),
                      ("Only in the new location", "extra")):
        for x in r[key]:
            rows.append([what, csv_export.guard(x["path"]), "folder" if x.get("folder") else "file",
                         x.get("size", ""), csv_export.guard(x.get("why", ""))])
    return ["What", "Path", "Type", "Size (bytes)", "Why"], rows


# ---------------------------------------------------------------- the switch
def previous(conn) -> dict | None:
    p = db.state_get(conn, PREVIOUS_KEY)
    if not isinstance(p, dict) or not p.get("path"):
        return None
    real = os.path.realpath(config.share_abs(p["path"]))
    out = dict(p)
    out["exists"] = paths.inside(config.share_root(), real) and os.path.isdir(real)
    return out


def switch(path: str, job_id: str, force: bool, confirm, admin: dict) -> dict:
    job = get_job(job_id)
    try:
        display = marker.clean_location(path)
    except paths.PathError as e:
        raise HTTPException(422, str(e))
    if job["path"] != display:
        raise HTTPException(409, "That check was for another folder — check this one first.")
    if job["phase"] != "done":
        raise HTTPException(409, "The check hasn't finished." if job["phase"] != "failed" else job["error"])
    info = target_info(display)
    if not info["ok"] or info.get("real") != job["real"]:
        raise HTTPException(409, info["message"] if not info["ok"] else "That folder changed — check it again.")
    with db.get_conn() as conn:
        current = roots.configured(conn)
    if job["fromPath"] != current:
        raise HTTPException(409, "The documents folder changed since that check — check again.")
    r = job["result"]
    problems = len(r["missing"]) + len(r["different"])
    if problems:
        if not force:
            raise HTTPException(409, f"{problems} file{'s are' if problems != 1 else ' is'} missing or different in the "
                                     "new location. Copy again and check, or switch anyway.")
        if str(confirm or "").strip() != str(problems):
            raise HTTPException(422, f"Type {problems} to confirm switching with {problems} missing or different.")
    new_real = job["real"]
    old_real = roots.docs_configured_real()
    with db.get_conn() as conn:
        iid = roots.install_id(conn)
    try:
        if not os.path.isdir(new_real):
            os.makedirs(new_real)
        marker.write_marker(new_real, iid)
        marker.ensure_layout(new_real)
    except OSError as e:
        raise HTTPException(409, f"The app couldn't write to {display}: {e.strerror or e}.")
    old_marker_note = None
    try:
        if marker.read_marker(old_real) is not None:
            marker.write_marker(old_real, iid, moved_to=display, moved_at=config.now_iso())
    except OSError as e:
        old_marker_note = f"The old folder's marker couldn't be updated ({e.strerror or e})."
    new_rel = config.share_rel(display)
    with db.get_conn() as conn:                    # one transaction: the setting, every person root, the history
        db.state_set(conn, "docs_path", display, admin.get("username") or admin["id"])
        for row in conn.execute("SELECT r.id, u.folder FROM roots r JOIN users u ON u.id = r.user_id "
                                "WHERE r.kind = 'person'").fetchall():
            if row["folder"]:
                conn.execute("UPDATE roots SET path = ?, missing = 0 WHERE id = ?",
                             (paths.join_rel(new_rel, marker.PEOPLE, row["folder"]), row["id"]))
        db.state_set(conn, PREVIOUS_KEY, {"path": current, "movedAt": config.now_iso(), "movedTo": display,
                                          "by": admin["id"]})
        db.state_set(conn, "docs_confirmed", True)
        was_on = is_read_only(conn)
        db.state_set(conn, STATE_KEY, {"on": False})
        db.audit(conn, "docs_location_switched", admin["id"])
        if was_on:
            db.audit(conn, "read_only_off", admin["id"])
    settings.invalidate()
    roots.reset()
    st = roots.check()
    from . import index
    scan = index.scan_all()
    totals = {"new": 0, "changed": 0, "moved": 0, "gone": 0, "back": 0}
    for v in scan.values():
        for k in totals:
            totals[k] += (v or {}).get(k, 0)
    with db.get_conn() as conn:
        for row in conn.execute("SELECT user_id FROM roots WHERE kind = 'person'").fetchall():
            roots.ensure_person_root(conn, row["user_id"])
        tell = [row["id"] for row in conn.execute("SELECT id FROM users WHERE disabled = 0")]
    return {"ok": st["ok"], "path": display, "previous": current, "scan": totals, "note": old_marker_note,
            "tell": tell}


def reset() -> None:
    """Tests: forget the jobs."""
    with _jobs_lock:
        _jobs.clear()
