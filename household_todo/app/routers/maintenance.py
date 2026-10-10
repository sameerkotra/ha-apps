"""Maintenance (SPEC §14): the tab's data, recurring items, Mark done / Undo / Snooze, history,
suggestions, files, and Admin → Maintenance.

Everyone who uses the app can see the tab, add and edit items and jobs, mark done, snooze and attach files.
Turning the feature on or off, the household recipients, the home profile, the overdue sensor, custom
suggestions and the files folder are admin-only. While the feature is off, everything but the admin routes
answers 409."""
import json
import os
from datetime import date, timedelta

from fastapi import APIRouter, BackgroundTasks, Body, Depends, File, HTTPException, Query, Response, UploadFile
from fastapi.responses import FileResponse

from .. import (config, db, ha_sensors, maint_catalog as cat, maint_files, maintenance as mt, recurrence, settings,
               task_files, taskview)
from ..auth import get_acting_user, require_admin
from ..common import csv_export

router = APIRouter(prefix="/api", tags=["maintenance"])


def _push(background: BackgroundTasks) -> None:
    background.add_task(ha_sensors.push_maintenance_blocking)


# ---------------------------------------------------------------------------
# the tab
# ---------------------------------------------------------------------------
def _jobs(conn, lid: str | None, acting_id: str, today: date) -> list[dict]:
    if not lid:
        return []
    rows = conn.execute(taskview.TASK_SELECT + " WHERE t.list_id = ? AND t.completed = 0", (lid,)).fetchall()
    tasks = taskview.serialize_tasks(conn, rows, today)          # with fileCount
    tasks.sort(key=lambda t: (t["dueDate"] is None, t["dueDate"] or "", t["position"]))
    return tasks


@router.get("/maintenance")
def overview(acting: dict = Depends(get_acting_user)):
    """Everything the Maintenance tab shows except suggestions and history (their own routes)."""
    mt.require_enabled()
    today = config.today()
    with db.get_conn() as conn:
        lk = mt.lookups(conn)
        items = [mt.serialize(it, today, lk) for it in mt.all_items(conn)]
        lid = mt.list_id(conn)
        jobs = _jobs(conn, lid, acting["id"], today)
    order = {"overdue": 0, "due": 1, "upcoming": 2, "paused": 3}
    items.sort(key=lambda x: (order[x["status"]], x["dueDate"] or "9999", x["name"].lower()))
    return {
        "today": today.isoformat(), "currency": config.CURRENCY, "listId": lid,
        "items": items, "jobs": jobs,
        "overdueCount": sum(1 for x in items if x["status"] == "overdue"),
        "categories": [{"key": k, "label": v} for k, v in cat.CATEGORIES.items()],
        "files": maint_files.public_status(),
        "upcomingDays": mt.UPCOMING_DAYS,
    }


# ---------------------------------------------------------------------------
# items
# ---------------------------------------------------------------------------
def _write_recipients(conn, item_id: str, ids: list[str] | None) -> None:
    if ids is None:
        return
    conn.execute("DELETE FROM maint_recipients WHERE item_id = ?", (item_id,))
    for uid in ids:
        conn.execute("INSERT INTO maint_recipients (item_id, user_id) VALUES (?, ?)", (item_id, uid))


def _item_out(conn, item_id: str) -> dict:
    return mt.serialize(mt.load_item(conn, item_id), config.today(), mt.lookups(conn))


@router.post("/maintenance/items", status_code=201)
def create_item(background: BackgroundTasks, body: dict = Body(...), acting: dict = Depends(get_acting_user)):
    mt.require_enabled()
    today = config.today()
    with db.get_conn() as conn:
        if conn.execute("SELECT COUNT(*) FROM maint_items").fetchone()[0] >= mt.MAX_ITEMS:
            raise HTTPException(422, f"There can be at most {mt.MAX_ITEMS} maintenance items.")
        vals, recips = mt.clean_item(conn, body, None, acting, today)
        iid = db.new_id()
        now = config.now_iso()
        vals.update(id=iid, entity_slug=mt.make_slug(conn, vals["name"]), created_by=acting["id"], created_at=now,
                    updated_at=now)
        cols = ", ".join(vals)
        conn.execute(f"INSERT INTO maint_items ({cols}) VALUES ({', '.join(':' + k for k in vals)})", vals)
        _write_recipients(conn, iid, recips)
        out = _item_out(conn, iid)
    _push(background)
    return out


@router.patch("/maintenance/items/{item_id}")
def update_item(item_id: str, background: BackgroundTasks, body: dict = Body(...), acting: dict = Depends(get_acting_user)):
    mt.require_enabled()
    today = config.today()
    renamed = False
    with db.get_conn() as conn:
        cur = mt.load_item(conn, item_id)
        vals, recips = mt.clean_item(conn, body, cur, acting, today)
        vals.pop("suggestion_key", None)
        renamed = "name" in vals and vals["name"] != cur["name"]
        if vals:
            vals["updated_at"] = config.now_iso()
            conn.execute(f"UPDATE maint_items SET {', '.join(k + ' = :' + k for k in vals)} WHERE id = :id",
                         dict(vals, id=item_id))
        _write_recipients(conn, item_id, recips)
        out = _item_out(conn, item_id)
    if renamed and cur["folder"] and maint_files.is_online():
        background.add_task(maint_files.reconcile_folders)
    if cur["expose_sensor"] and not out["exposeSensor"]:
        background.add_task(ha_sensors.delete_maintenance_entity_blocking, cur["entity_slug"])
    _push(background)
    return out


@router.delete("/maintenance/items/{item_id}", status_code=204)
def delete_item(item_id: str, background: BackgroundTasks, acting: dict = Depends(get_acting_user)):
    mt.require_enabled()
    with db.get_conn() as conn:
        it = mt.load_item(conn, item_id)
        has_files = conn.execute(
            "SELECT COUNT(*) FROM maint_files WHERE item_id = ? OR done_id IN (SELECT id FROM maint_done WHERE item_id = ?)",
            (item_id, item_id)).fetchone()[0]
    root = maint_files.require_online() if has_files else None
    with db.get_conn() as conn:
        thumbs = [r["thumb"] for r in conn.execute(
            "SELECT thumb FROM maint_files WHERE item_id = ? OR done_id IN (SELECT id FROM maint_done WHERE item_id = ?)",
            (item_id, item_id))]
        conn.execute("DELETE FROM maint_files WHERE item_id = ? OR done_id IN (SELECT id FROM maint_done WHERE item_id = ?)",
                     (item_id, item_id))
        conn.execute("DELETE FROM maint_notify_log WHERE ref = ?", (item_id,))
        conn.execute("DELETE FROM maint_items WHERE id = ?", (item_id,))
    if root and it["folder"]:
        maint_files.to_deleted(root, [it["folder"]])
        maint_files.remove_thumbs(root, thumbs)
    if it["expose_sensor"]:
        background.add_task(ha_sensors.delete_maintenance_entity_blocking, it["entity_slug"])
    _push(background)
    return Response(status_code=204)


def _clean_done(body: dict, today: date) -> tuple[date, str | None, int | None]:
    d = recurrence.parse_date(body.get("date")) if body.get("date") else today
    if d is None:
        raise HTTPException(422, "date must be a date (YYYY-MM-DD).")
    if d > today:
        raise HTTPException(422, "It can't be marked done in the future.")
    if (today - d).days > 3660:
        raise HTTPException(422, "The date must be within the last 10 years.")
    note = body.get("note")
    if note is not None:
        if not isinstance(note, str) or len(note.strip()) > 500:
            raise HTTPException(422, "The note can be at most 500 characters.")
        note = note.strip() or None
    cost = body.get("cost")
    cents = None
    if cost not in (None, ""):
        if isinstance(cost, bool) or not isinstance(cost, (int, float)) or cost < 0 or cost > 10_000_000:
            raise HTTPException(422, "The cost must be a number from 0 to 10,000,000.")
        cents = int(round(cost * 100))
    return d, note, cents


@router.post("/maintenance/items/{item_id}/done", status_code=201)
def mark_done(item_id: str, background: BackgroundTasks, body: dict | None = Body(default=None),
              acting: dict = Depends(get_acting_user)):
    """Record it as done (today unless `date` says earlier) and work out the next due date."""
    mt.require_enabled()
    body = body or {}
    today = config.today()
    d, note, cents = _clean_done(body, today)
    with db.get_conn() as conn:
        it = mt.load_item(conn, item_id)
        due = mt.raw_due(it)
        prev = mt.state_snapshot(it)
        upd = {"snooze_due": None, "snooze_to": None}
        if it["mode"] == "interval":
            last = mt._d(it["last_done"])
            upd["last_done"] = max(d, last).isoformat() if last else d.isoformat()
        else:
            latest = mt.last_occurrence_on_or_before(it["rule"], it["anchor_date"], max(d, today))
            cands = [x for x in (due, latest) if x]
            if cands:
                upd["cleared_through"] = max(cands).isoformat()
        conn.execute(f"UPDATE maint_items SET {', '.join(k + ' = :' + k for k in upd)}, updated_at = :u WHERE id = :id",
                     dict(upd, u=config.now_iso(), id=item_id))
        rid = db.new_id()
        conn.execute("INSERT INTO maint_done (id, item_id, done_on, done_by, note, cost_cents, due_was, prev_state, created_at) "
                     "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                     (rid, item_id, d.isoformat(), acting["id"], note, cents, due.isoformat() if due else None, prev,
                      config.now_iso()))
        out = _item_out(conn, item_id)
    _push(background)
    return {"recordId": rid, "item": out}


@router.post("/maintenance/items/{item_id}/undo")
def undo_done(item_id: str, background: BackgroundTasks, acting: dict = Depends(get_acting_user)):
    """Take back the latest Mark done: the item returns to how it was, and that record's files go to _deleted."""
    mt.require_enabled()
    with db.get_conn() as conn:
        mt.load_item(conn, item_id)
        rec = conn.execute("SELECT * FROM maint_done WHERE item_id = ? ORDER BY created_at DESC LIMIT 1", (item_id,)).fetchone()
        if not rec:
            raise HTTPException(409, "There's nothing to undo.")
        n_files = conn.execute("SELECT COUNT(*) FROM maint_files WHERE done_id = ?", (rec["id"],)).fetchone()[0]
    root = maint_files.require_online() if n_files else None
    with db.get_conn() as conn:
        files = [dict(r) for r in conn.execute("SELECT rel_path, thumb FROM maint_files WHERE done_id = ?", (rec["id"],))]
        prev = json.loads(rec["prev_state"])
        conn.execute("UPDATE maint_items SET last_done = ?, start_date = ?, cleared_through = ?, snooze_due = ?, "
                     "snooze_to = ?, updated_at = ? WHERE id = ?",
                     (prev.get("last_done"), prev.get("start_date"), prev.get("cleared_through"), prev.get("snooze_due"),
                      prev.get("snooze_to"), config.now_iso(), item_id))
        conn.execute("DELETE FROM maint_files WHERE done_id = ?", (rec["id"],))
        conn.execute("DELETE FROM maint_done WHERE id = ?", (rec["id"],))
        out = _item_out(conn, item_id)
    if root:
        maint_files.to_deleted(root, [f["rel_path"] for f in files])
        maint_files.remove_thumbs(root, [f["thumb"] for f in files])
    _push(background)
    return out


@router.post("/maintenance/items/{item_id}/snooze")
def snooze(item_id: str, background: BackgroundTasks, body: dict = Body(...), acting: dict = Depends(get_acting_user)):
    """{"until": "YYYY-MM-DD"} or {"days": n} moves this one due date; {"until": null} cancels it."""
    mt.require_enabled()
    today = config.today()
    with db.get_conn() as conn:
        it = mt.load_item(conn, item_id)
        due = mt.raw_due(it)
        if due is None:
            raise HTTPException(409, "A paused item has no due date to snooze.")
        if "days" in body:
            days = body["days"]
            if isinstance(days, bool) or not isinstance(days, int) or not 1 <= days <= 366:
                raise HTTPException(422, "days must be a whole number from 1 to 366.")
            until = max(today, due) + timedelta(days=days)
        elif body.get("until") is None:
            until = None
        else:
            until = recurrence.parse_date(body.get("until"))
            if until is None:
                raise HTTPException(422, "until must be a date (YYYY-MM-DD).")
            if until <= today or until <= due:
                raise HTTPException(422, "Snooze to a date after today and after it's due.")
            if (until - today).days > 366:
                raise HTTPException(422, "You can snooze for at most a year.")
        conn.execute("UPDATE maint_items SET snooze_due = ?, snooze_to = ?, updated_at = ? WHERE id = ?",
                     (due.isoformat() if until else None, until.isoformat() if until else None, config.now_iso(), item_id))
        out = _item_out(conn, item_id)
    _push(background)
    return out


# ---------------------------------------------------------------------------
# history
# ---------------------------------------------------------------------------
def _history_rows(conn, *, item_id=None, year=None, category=None, limit=500):
    sql = ("SELECT d.*, i.name AS item_name, i.icon AS item_icon, i.category, u.name AS by_name "
           "FROM maint_done d JOIN maint_items i ON i.id = d.item_id LEFT JOIN users u ON u.id = d.done_by WHERE 1=1")
    args = []
    if item_id:
        sql += " AND d.item_id = ?"
        args.append(item_id)
    if year:
        sql += " AND substr(d.done_on, 1, 4) = ?"
        args.append(str(year))
    if category:
        sql += " AND i.category = ?"
        args.append(category)
    sql += " ORDER BY d.done_on DESC, d.created_at DESC LIMIT ?"
    args.append(limit)
    rows = conn.execute(sql, args).fetchall()
    files: dict[str, list] = {}
    ids = [r["id"] for r in rows]
    for i in range(0, len(ids), 500):
        chunk = ids[i:i + 500]
        for f in conn.execute(f"SELECT * FROM maint_files WHERE done_id IN ({','.join('?' * len(chunk))}) ORDER BY created_at", chunk):
            files.setdefault(f["done_id"], []).append(mt.file_json(f))
    latest = {r["item_id"]: r["id"] for r in conn.execute(
        "SELECT item_id, id FROM maint_done d1 WHERE created_at = (SELECT MAX(created_at) FROM maint_done d2 WHERE d2.item_id = d1.item_id)")}
    return [{"id": r["id"], "itemId": r["item_id"], "itemName": r["item_name"], "itemIcon": r["item_icon"],
             "category": r["category"], "categoryLabel": cat.CATEGORIES.get(r["category"], "Other"),
             "date": r["done_on"], "by": r["by_name"], "note": r["note"],
             "cost": r["cost_cents"] / 100 if r["cost_cents"] is not None else None, "dueWas": r["due_was"],
             "files": files.get(r["id"], []), "latest": latest.get(r["item_id"]) == r["id"]} for r in rows]


def _year(v):
    if v in (None, ""):
        return None
    if not str(v).isdigit() or not 1900 <= int(v) <= 2200:
        raise HTTPException(422, "year must be a year like 2026.")
    return int(v)


@router.get("/maintenance/history")
def history(year: str | None = Query(default=None), category: str | None = Query(default=None),
            item: str | None = Query(default=None), acting: dict = Depends(get_acting_user)):
    mt.require_enabled()
    y = _year(year)
    if category and category not in cat.CATEGORIES:
        raise HTTPException(422, "Unknown category.")
    with db.get_conn() as conn:
        rows = _history_rows(conn, item_id=item, year=y, category=category or None)
        years = [r[0] for r in conn.execute("SELECT DISTINCT substr(done_on, 1, 4) FROM maint_done ORDER BY 1 DESC")]
        totals = {r[0]: r[1] / 100 for r in conn.execute(
            "SELECT substr(done_on, 1, 4), SUM(cost_cents) FROM maint_done WHERE cost_cents IS NOT NULL GROUP BY 1")}
    return {"records": rows, "years": years, "yearTotals": totals, "currency": config.CURRENCY}


@router.get("/maintenance/history.csv")
def history_csv(year: str | None = Query(default=None), acting: dict = Depends(get_acting_user)):
    mt.require_enabled()
    y = _year(year)
    with db.get_conn() as conn:
        rows = _history_rows(conn, year=y, limit=100_000)
    # Text that people typed goes through the formula guard (app/common/csv_export.py); dates and costs don't.
    t = csv_export.text
    text = csv_export.to_text(
        ["Date", "Item", "Category", "Done by", "Note", f"Cost{' (' + config.CURRENCY + ')' if config.CURRENCY else ''}",
         "Was due", "Files"],
        ([r["date"], t(r["itemName"]), t(r["categoryLabel"]), t(r["by"] or ""), t(r["note"] or ""),
          "" if r["cost"] is None else f"{r['cost']:.2f}", r["dueWas"] or "",
          t("; ".join(f["name"] for f in r["files"]))] for r in rows), bom=True)
    name = f"maintenance-history{'-' + str(y) if y else ''}.csv"
    return Response(text.encode("utf-8"), media_type="text/csv",
                    headers={"Content-Disposition": f'attachment; filename="{name}"'})


# ---------------------------------------------------------------------------
# suggestions
# ---------------------------------------------------------------------------
@router.get("/maintenance/suggestions")
def get_suggestions(acting: dict = Depends(get_acting_user)):
    mt.require_enabled()
    with db.get_conn() as conn:
        return mt.suggestions(conn, config.today())


def _known_key(conn, key: str) -> None:
    if not any(s["key"] == key for s in mt.all_suggestions(conn)):
        raise HTTPException(404, "Unknown suggestion.")


@router.put("/maintenance/suggestions/{key}/hidden", status_code=204)
def hide_suggestion(key: str, acting: dict = Depends(get_acting_user)):
    mt.require_enabled()
    with db.get_conn() as conn:
        _known_key(conn, key)
        conn.execute("INSERT OR IGNORE INTO maint_hidden (key, hidden_by, created_at) VALUES (?, ?, ?)",
                     (key, acting["id"], config.now_iso()))
    return Response(status_code=204)


@router.delete("/maintenance/suggestions/{key}/hidden", status_code=204)
def unhide_suggestion(key: str, acting: dict = Depends(get_acting_user)):
    mt.require_enabled()
    with db.get_conn() as conn:
        conn.execute("DELETE FROM maint_hidden WHERE key = ?", (key,))
    return Response(status_code=204)


# ---------------------------------------------------------------------------
# files
# ---------------------------------------------------------------------------
def _check_owner(conn, item_id, done_id, task_id) -> None:
    given = [x for x in (item_id, done_id, task_id) if x]
    if len(given) != 1:
        raise HTTPException(422, "Attach the file to exactly one item, record or job.")
    if item_id:
        mt.load_item(conn, item_id)
    elif done_id:
        if not conn.execute("SELECT 1 FROM maint_done WHERE id = ?", (done_id,)).fetchone():
            raise HTTPException(404, "That record doesn't exist (any more).")
    else:
        row = conn.execute("SELECT l.role FROM tasks t JOIN lists l ON l.id = t.list_id WHERE t.id = ?", (task_id,)).fetchone()
        if not row:
            raise HTTPException(404, "Task not found.")
        if row["role"] != mt.LIST_ROLE:
            raise HTTPException(422, "Files can only be attached to jobs in the Maintenance list.")


@router.post("/maintenance/files", status_code=201)
def upload(file: UploadFile = File(...), item_id: str | None = Query(default=None), done_id: str | None = Query(default=None),
           task_id: str | None = Query(default=None), acting: dict = Depends(get_acting_user)):
    mt.require_enabled()
    with db.get_conn() as conn:
        _check_owner(conn, item_id, done_id, task_id)
    root = maint_files.require_online()
    with db.get_conn() as conn:
        rel_dir, owner = maint_files.target_dir(conn, item_id=item_id, done_id=done_id, task_id=task_id)
    row = maint_files.save_upload(root, rel_dir, file.filename, file.file.read, acting["id"], owner,
                                  keep=bool(task_id))          # a job's receipts and photos stay when it's done
    return mt.file_json(row)


@router.get("/maintenance/files")
def list_files(task_id: str = Query(), acting: dict = Depends(get_acting_user)):
    mt.require_enabled()
    with db.get_conn() as conn:
        taskview.load_task(conn, task_id, acting["id"])
        return [mt.file_json(r) for r in task_files.listed(conn, task_id)]


def _file_row(file_id: str, acting_id: str) -> dict:
    """A file, if the acting person may see it: a task's file only for those who see the task (SPEC §17), and
    not one that's been removed with its task."""
    with db.get_conn() as conn:
        r = conn.execute("SELECT * FROM maint_files WHERE id = ?", (file_id,)).fetchone()
        if r and r["task_id"]:
            taskview.load_task(conn, r["task_id"], acting_id)
            r = conn.execute(f"SELECT * FROM maint_files WHERE id = ? AND {task_files.LISTED}", (file_id,)).fetchone()
    if not r:
        raise HTTPException(404, "That file doesn't exist (any more).")
    return dict(r)


@router.get("/maintenance/files/{file_id}")
def download(file_id: str, thumb: int = Query(default=0), acting: dict = Depends(get_acting_user)):
    mt.require_enabled()
    f = _file_row(file_id, acting["id"])
    root = maint_files.require_online()
    rel = f["thumb"] if thumb and f["thumb"] else f["rel_path"]
    path = maint_files._within(root, rel)
    if not os.path.isfile(path):
        raise HTTPException(404, "The file is no longer in the files folder.")
    mime = "image/jpeg" if thumb and f["thumb"] else (f["mime"] or "application/octet-stream")
    inline = mime in maint_files.INLINE
    return FileResponse(path, media_type=mime, filename=f["name"], content_disposition_type="inline" if inline else "attachment",
                        headers={"X-Content-Type-Options": "nosniff", "Content-Security-Policy": "sandbox",
                                 "Cache-Control": "private, max-age=3600"})


@router.delete("/maintenance/files/{file_id}", status_code=204)
def delete_file(file_id: str, acting: dict = Depends(get_acting_user)):
    mt.require_enabled()
    f = _file_row(file_id, acting["id"])
    root = maint_files.require_online()
    with db.get_conn() as conn:
        conn.execute("DELETE FROM maint_files WHERE id = ?", (file_id,))
    maint_files.to_deleted(root, [f["rel_path"]])
    maint_files.remove_thumbs(root, [f["thumb"]])
    return Response(status_code=204)


# ---------------------------------------------------------------------------
# Admin → Maintenance
# ---------------------------------------------------------------------------
ADMIN_KEYS = ("maintenance_enabled", "maintenance_recipients", "maintenance_profile", "maintenance_sensor")


def _admin_json(conn) -> dict:
    v = settings.all()
    return {
        "enabled": v["maintenance_enabled"], "recipients": v["maintenance_recipients"],
        "profile": v["maintenance_profile"], "sensor": v["maintenance_sensor"],
        "features": [{"key": k, "label": l} for k, l in cat.FEATURES.items()],
        "categories": [{"key": k, "label": l} for k, l in cat.CATEGORIES.items()],
        "custom": [mt.suggestion_json(s, config.today(), mt.south()) for s in mt.custom_suggestions(conn)],
        "files": maint_files.admin_status(),
        "listId": mt.list_id(conn),
        "sensorEntity": mt.OVERDUE_SENSOR,
    }


@router.get("/admin/maintenance")
def admin_get(admin: dict = Depends(require_admin)):
    with db.get_conn() as conn:
        return _admin_json(conn)


@router.put("/admin/maintenance")
def admin_put(background: BackgroundTasks, body: dict = Body(...), admin: dict = Depends(require_admin)):
    """{enabled, recipients, profile, sensor} — any subset. Turning it on the first time creates the
    Maintenance list and, with nobody chosen yet, makes every admin a recipient."""
    names = {"enabled": "maintenance_enabled", "recipients": "maintenance_recipients",
             "profile": "maintenance_profile", "sensor": "maintenance_sensor"}
    if not isinstance(body, dict) or set(body) - set(names):
        raise HTTPException(422, "Send any of enabled, recipients, profile, sensor.")
    partial = {names[k]: v for k, v in body.items()}
    with db.get_conn() as conn:
        if "maintenance_recipients" in partial and isinstance(partial["maintenance_recipients"], list):
            known = {r["id"] for r in conn.execute("SELECT id FROM users")}
            if any(not isinstance(x, str) or x not in known for x in partial["maintenance_recipients"]):
                raise HTTPException(422, "Recipients contains an unknown person.")
        turning_on = partial.get("maintenance_enabled") is True and not settings.get("maintenance_enabled")
        if turning_on and not settings.get("maintenance_recipients") and "maintenance_recipients" not in partial:
            partial["maintenance_recipients"] = mt.admin_ids(conn)
    try:
        settings.update(partial, admin)
    except settings.SettingsError as e:
        raise HTTPException(422, str(e))
    with db.get_conn() as conn:
        if settings.get("maintenance_enabled"):
            mt.ensure_list(conn)
        out = _admin_json(conn)
    _push(background)
    return out


def _clean_suggestion(body: dict) -> dict:
    if not isinstance(body, dict):
        raise HTTPException(422, "Send an object.")
    name = mt._text(body, "name", 60, required=True, label="Name")
    category = body.get("category") or "other"
    if category not in cat.CATEGORIES:
        raise HTTPException(422, "Unknown category.")
    needs = body.get("needs") or None
    if needs is not None and needs not in cat.FEATURES:
        raise HTTPException(422, "Unknown home feature.")
    seasons = body.get("seasons") or None
    every_n = every_unit = None
    if seasons is not None:
        if not isinstance(seasons, list) or not seasons or any(s not in cat.SEASONS for s in seasons):
            raise HTTPException(422, "seasons must be a list of spring, summer, autumn, winter.")
        seasons = [s for s in cat.SEASONS if s in seasons]
    else:
        every_n, every_unit = mt.clean_every(body.get("every_n"), body.get("every_unit"))
    who = body.get("who") or "diy"
    if who not in ("diy", "pro"):
        raise HTTPException(422, "who must be diy or pro.")
    minutes = body.get("minutes")
    if minutes is not None:
        minutes = mt._int(minutes, 1, 10000, "Minutes")
    return {"name": name, "icon": mt.clean_icon(body.get("icon")), "category": category, "needs": needs,
            "every_n": every_n, "every_unit": every_unit, "seasons": json.dumps(seasons) if seasons else None,
            "why": mt._text(body, "why", 300, label="Why"), "howto": mt._text(body, "howto", 2000, label="How"),
            "who": who, "minutes": minutes}


@router.post("/admin/maintenance/suggestions", status_code=201)
def add_suggestion(body: dict = Body(...), admin: dict = Depends(require_admin)):
    vals = _clean_suggestion(body)
    with db.get_conn() as conn:
        if conn.execute("SELECT COUNT(*) FROM maint_suggestions").fetchone()[0] >= 200:
            raise HTTPException(422, "There can be at most 200 suggestions of your own.")
        vals.update(id=db.new_id(), created_by=admin["id"], created_at=config.now_iso())
        conn.execute(f"INSERT INTO maint_suggestions ({', '.join(vals)}) VALUES ({', '.join(':' + k for k in vals)})", vals)
        return _admin_json(conn)


@router.patch("/admin/maintenance/suggestions/{sid}")
def edit_suggestion(sid: str, body: dict = Body(...), admin: dict = Depends(require_admin)):
    vals = _clean_suggestion(body)
    with db.get_conn() as conn:
        if not conn.execute("SELECT 1 FROM maint_suggestions WHERE id = ?", (sid,)).fetchone():
            raise HTTPException(404, "Unknown suggestion.")
        conn.execute(f"UPDATE maint_suggestions SET {', '.join(k + ' = :' + k for k in vals)} WHERE id = :id", dict(vals, id=sid))
        return _admin_json(conn)


@router.delete("/admin/maintenance/suggestions/{sid}")
def delete_suggestion(sid: str, admin: dict = Depends(require_admin)):
    with db.get_conn() as conn:
        conn.execute("DELETE FROM maint_suggestions WHERE id = ?", (sid,))
        conn.execute("DELETE FROM maint_hidden WHERE key = ?", ("c:" + sid,))
        return _admin_json(conn)


# the files folder (App settings)
@router.post("/admin/settings/check-maintenance-folder")
def check_folder(body: dict = Body(...), admin: dict = Depends(require_admin)):
    try:
        path = maint_files.clean_path(body.get("path"))
    except ValueError as e:
        raise HTTPException(422, str(e))
    return maint_files.inspect(path)


@router.post("/admin/maintenance/files/check")
def recheck_folder(body: dict | None = Body(default=None), admin: dict = Depends(require_admin)):
    """Check the folder again now; {"useThisFolder": true} sets up a folder that lost (or never had) our marker."""
    use = bool((body or {}).get("useThisFolder"))
    if use and maint_files.configured():
        info = maint_files.inspect(maint_files.clean_path(maint_files.configured()))
        if info["verdict"] in ("other",) and not (body or {}).get("confirm"):
            raise HTTPException(409, info["message"])
    maint_files.check(allow_setup=use)
    return maint_files.admin_status()
