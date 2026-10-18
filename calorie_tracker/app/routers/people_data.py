"""One person's data — export, import, delete (Admin → Users → ⋯ Data; PERSON_DATA_SPEC.md §2 and §4).

The file is the shared one (app/common/person_data.py): a .zip with manifest.json and data/<table>.json, rows as
this app stores them (columns by name). Calorie Tracker has no files, so an export is always small and sent at
once; the job routes exist because the shared dialogs know them, and never have a job to report.

What is a person's: their `users` row (enabled only — the name comes from Home Assistant), `goals`, `weight_logs`,
`saved_foods`, `food_logs` and `symptom_logs`. Never exported, imported or deleted here: App settings (the AI key,
the household's symptom and tag lists), the app-messages tables, other people's rows. `ai_usage` (the AI limit's
counter) isn't exported or imported; a delete removes the person's rows (§4).

Admin only (require_admin: the real signed-in admin, never "acting as"); the ingress and cross-site guards in
main.py apply to these routes like every other.
"""
import logging
import os
import re
from typing import Literal, Optional

from fastapi import APIRouter, BackgroundTasks, Depends, File, HTTPException, UploadFile
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import Response
from pydantic import BaseModel, ConfigDict, ValidationError

from .. import auth, config, db, foodrows, ha_sync, schemas, settings
from ..auth import require_admin
from ..common import backup_core
from ..common import person_data as pd

logger = logging.getLogger("person_data")
router = APIRouter(prefix="/api/admin/people", tags=["person-data"])

APP = "calorie_tracker"
# The data version of the export file. 1 = the tables and columns below (this app's database as its MIGRATIONS
# leave it). Bump it when a person's table gains or changes a column, and teach _upgrade() to bring an older file
# up to date the way db._migrate does for the database; a file with a newer number is refused.
SCHEMA = 1
UPLOAD_MAX = 512 << 20          # the same as a database restore (main.RESTORE_MAX_BYTES)
UPLOAD_PREFIX = "calorie-person-data-up-"
UPLOADS = pd.Uploads(config.DATA_DIR, prefix=UPLOAD_PREFIX)
JOBS = pd.Jobs()                # never started here (no files: every export is small), see the module docstring

# Exported columns per table, as stored (no user_id: the person is the file's; ids only to link rows inside it).
COLUMNS = {
    "users": ("name", "enabled"),
    "goals": ("calorie_goal", "protein_goal", "carb_goal", "fat_goal", "starting_weight_kg", "target_weight_kg"),
    "weight_logs": ("id", "date", "weight_kg", "note"),
    "saved_foods": ("id", "name", "serving_size", "serving_unit", "calories", "no_calories", "protein", "carbs", "fat",
                    "notes", "tags"),
    "food_logs": ("id", "date", "meal_type", "food_name", "servings", "calories", "no_calories", "protein", "carbs",
                  "fat", "saved_food_id", "eaten_at", "tags", "created_at"),
    "symptom_logs": ("id", "date", "time", "symptom", "severity", "note", "created_at"),
}
ORDER = {"weight_logs": "date, id", "saved_foods": "id", "food_logs": "date, id", "symptom_logs": "date, time, id"}
LABELS = {"food_logs": "food entries", "symptom_logs": "symptoms", "weight_logs": "weights",
          "saved_foods": "saved foods"}
KINDS = ("food_logs", "symptom_logs", "weight_logs", "saved_foods")      # what the dialogs count
GOAL_DEFAULTS = {"calorie_goal": 2000, "protein_goal": 150, "carb_goal": 200, "fat_goal": 65,
                 "starting_weight_kg": None, "target_weight_kg": None}     # the goals table's column defaults
STAMP = re.compile(r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}$")              # created_at as SQLite writes it


class RowError(pd.PackageError):
    """A row the app's own rules refuse (422, with the table and row number)."""


def _who(admin: dict) -> str:
    return admin.get("username") or admin["name"]


def _person(conn, user_id: str) -> dict:
    row = conn.execute("SELECT id, name, enabled, created_at FROM users WHERE id = ?", (user_id,)).fetchone()
    if row is None:
        raise HTTPException(404, "That person isn't in this app.")
    return dict(row)


def _counts(conn, uid: str) -> dict:
    out = {t: conn.execute(f"SELECT COUNT(*) FROM {t} WHERE user_id = ?", (uid,)).fetchone()[0] for t in KINDS}
    days = conn.execute(
        "SELECT COUNT(*), MIN(d), MAX(d) FROM (SELECT date AS d FROM food_logs WHERE user_id = ? UNION "
        "SELECT date FROM symptom_logs WHERE user_id = ? UNION SELECT date FROM weight_logs WHERE user_id = ?)",
        (uid, uid, uid)).fetchone()
    out["days"], out["first"], out["last"] = days[0], days[1], days[2]
    return out


def _count_items(counts: dict) -> list:
    items = pd.count_list([(t, LABELS[t], counts.get(t, 0)) for t in KINDS])
    if "days" in counts:
        day = {"key": "days", "label": "days logged", "count": int(counts["days"] or 0)}
        if counts.get("first"):
            day["detail"] = (counts["first"] if counts["first"] == counts["last"]
                             else f"{counts['first']} to {counts['last']}")
        items.append(day)
    return items


def _counts_text(counts: dict) -> str:
    return ", ".join(f"{counts.get(t, 0)} {LABELS[t]}" for t in KINDS)


# ---------- the dialogs' summary (also the delete's dry run) ----------

@router.get("/{user_id}/data")
def summary(user_id: str, admin: dict = Depends(require_admin)):
    with db.get_conn() as conn:
        person = _person(conn, user_id)
        counts = _counts(conn, user_id)
    return {
        "person": {"id": person["id"], "name": person["name"]},
        "counts": _count_items(counts),
        "exportOptions": [],
        "exportNote": "The file holds health data (symptoms and weight, as well as the food log) and isn't "
                      "encrypted — keep it somewhere safe.",
        "keeps": ["Everyone else's food log, symptoms, weights and saved foods",
                  "The household's symptom and food-tag lists and every other App setting"],
        "cannotReach": ["whole-app backups (Admin → Storage) and Home Assistant backups made before now still hold "
                        "their data"],
        "deleteChoices": [],
        "forgetNote": "Their row goes too. If they still have a Home Assistant login, they come back as a new, empty "
                      "person the next time they open the app — remove them in Home Assistant first if they "
                      "shouldn't come back.",
        "deleteNote": "Their daily-calories sensor is removed from Home Assistant. This can't be undone.",
    }


# ---------- export ----------

class ExportBody(BaseModel):
    model_config = ConfigDict(extra="ignore")
    options: dict = {}


def _rows(conn, uid: str) -> dict:
    tables = {}
    for t, cols in COLUMNS.items():
        key = "id" if t == "users" else "user_id"
        order = f" ORDER BY {ORDER[t]}" if t in ORDER else ""
        tables[t] = db.rows_to_list(conn.execute(f"SELECT {', '.join(cols)} FROM {t} WHERE {key} = ?{order}", (uid,)))
    return tables


@router.post("/{user_id}/data/export")
def export(user_id: str, body: Optional[ExportBody] = None, admin: dict = Depends(require_admin)):
    # no options: there are no files to leave out
    with db.get_conn() as conn:
        person = _person(conn, user_id)
        tables = _rows(conn, user_id)
    data = pd.bytes_io_package(
        app=APP, app_version=config.APP_VERSION, schema=SCHEMA,
        person={"id": person["id"], "name": person["name"]}, exported_by=_who(admin), tables=tables,
        title=config.APP_TITLE, what="their food log, weight and symptoms — health data")
    name = pd.file_name(config.APP_TITLE, person["name"], now=config.now())
    logger.info("Person data export by %s (%s) of %s (%s): %s", admin["name"], admin["id"], person["name"],
                person["id"], _counts_text({t: len(tables[t]) for t in KINDS}))
    return Response(data, media_type="application/zip",
                    headers={"Content-Disposition": f'attachment; filename="{name}"', "Cache-Control": "no-store"})


@router.get("/data/jobs/{job}")
def job_status(job: str, admin: dict = Depends(require_admin)):
    st = JOBS.status(job, admin["id"])
    if st is None:
        raise HTTPException(404, "That export isn't here any more — export again.")
    return st


@router.get("/data/jobs/{job}/file")
def job_file(job: str, admin: dict = Depends(require_admin)):
    j = JOBS.get(job, admin["id"])
    if not j or j["state"] != "done" or not j.get("path") or not os.path.exists(j["path"]):
        raise HTTPException(404, "That export isn't here any more — export again.")
    return backup_core.send_file(j["path"], j["filename"], media_type="application/zip")


# ---------- import: checking a file ----------

def _upgrade(tables: dict, schema: int) -> dict:
    """Brings a file made with an older SCHEMA up to date, table by table (as db._migrate does for the database).
    Nothing to do yet: 1 is the first."""
    return tables


def _why(e: ValidationError) -> str:
    return settings.readable(e)


def _check(table: str, i: int, model, data: dict):
    try:
        return model(**data)
    except (ValidationError, TypeError) as e:
        why = _why(e) if isinstance(e, ValidationError) else "not a row"
        raise RowError(f"{table} row {i + 1} can't be imported: {why}") from None


def _calories(row: dict):
    return None if row.get("no_calories") else row.get("calories")


def _stamp(v) -> Optional[str]:
    return v if isinstance(v, str) and STAMP.match(v) else None


def prepare(pkg: pd.Package) -> dict:
    """The file's rows checked with the API's own models (unknown columns dropped), ready to insert. Raises
    RowError naming the table and row."""
    t = _upgrade({k: pkg.tables.get(k) or [] for k in COLUMNS}, pkg.schema)
    out = {"users": None, "goals": None, "weight_logs": [], "saved_foods": [], "food_logs": [], "symptom_logs": []}
    if len(t["users"]) > 1 or len(t["goals"]) > 1:
        raise RowError("The file has more than one person in it.")
    if t["users"]:
        enabled = t["users"][0].get("enabled", 1)
        if enabled not in (0, 1, True, False):
            raise RowError("users row 1 can't be imported: enabled: should be 0 or 1")
        out["users"] = {"enabled": 1 if enabled else 0}
    if t["goals"]:
        g = pd.pick(t["goals"][0], COLUMNS["goals"])
        m = _check("goals", 0, schemas.GoalUpdate, g)
        out["goals"] = {**GOAL_DEFAULTS, **m.model_dump(exclude_unset=True)}
    for i, r in enumerate(t["weight_logs"]):
        m = _check("weight_logs", i, schemas.WeightCreate, pd.pick(r, ("date", "weight_kg", "note")))
        out["weight_logs"].append({"old": r.get("id"), "date": str(m.date), "weight_kg": m.weight_kg,
                                   "note": m.note or None})
    for i, r in enumerate(t["saved_foods"]):
        data = {**pd.pick(r, ("name", "serving_size", "serving_unit", "protein", "carbs", "fat", "notes", "tags")),
                "calories": _calories(r)}
        m = _check("saved_foods", i, schemas.SavedFoodCreate, data)
        out["saved_foods"].append({"old": r.get("id"), **m.model_dump()})
    for i, r in enumerate(t["food_logs"]):
        data = {**pd.pick(r, ("date", "meal_type", "food_name", "servings", "protein", "carbs", "fat",
                              "saved_food_id", "eaten_at", "tags")), "calories": _calories(r)}
        m = _check("food_logs", i, schemas.FoodLogCreate, data)
        d = m.model_dump()
        d.update(old=r.get("id"), date=str(m.date), created_at=_stamp(r.get("created_at")))
        out["food_logs"].append(d)
    for i, r in enumerate(t["symptom_logs"]):
        m = _check("symptom_logs", i, schemas.SymptomCreate,
                   pd.pick(r, ("date", "time", "symptom", "severity", "note")))
        out["symptom_logs"].append({"old": r.get("id"), "date": str(m.date), "time": m.time,
                                    "symptom": " ".join(m.symptom.split()), "severity": m.severity,
                                    "note": m.note or None, "created_at": _stamp(r.get("created_at"))})
    return out


def _warnings(rows: dict) -> list:
    w = []
    saved_ids = {str(s["old"]) for s in rows["saved_foods"] if s["old"] is not None}
    n = sum(1 for s in rows["saved_foods"] if s["calories"] is None)
    if n:
        w.append(f"{n} saved food{'s have' if n != 1 else ' has'} no calories.")
    n = sum(1 for f in rows["food_logs"] if f["calories"] is None)
    if n:
        w.append(f"{n} food entr{'ies have' if n != 1 else 'y has'} no calories.")
    n = sum(1 for f in rows["food_logs"] if f["saved_food_id"] is not None and str(f["saved_food_id"]) not in saved_ids)
    if n:
        w.append(f"{n} food entr{'ies point' if n != 1 else 'y points'} to a saved food that isn't in the file; "
                 "they're imported without the link.")
    return w


def _read(path: str) -> tuple:
    """(manifest, prepared rows) of an uploaded file, or PackageError."""
    with pd.read_package(path, app=APP, schema=SCHEMA, limits=pd.Limits(max_upload=UPLOAD_MAX),
                         tables=COLUMNS.keys()) as pkg:
        return pkg.manifest, prepare(pkg)


def _refuse(e: pd.PackageError):
    return HTTPException(422 if isinstance(e, RowError) else 400, str(e))


@router.post("/data/import")
async def upload(file: UploadFile = File(...), admin: dict = Depends(require_admin)):
    """The file is checked (§9) and kept for an hour under a token only this admin can use."""
    def too_big():
        return HTTPException(413, f"The file is larger than the {pd.bytes_text(UPLOAD_MAX)} limit.")
    path = await backup_core.receive(file, config.DATA_DIR, suffix=".zip", prefix=UPLOAD_PREFIX,
                                     max_bytes=UPLOAD_MAX, too_big=too_big)
    try:
        manifest, rows = await run_in_threadpool(_read, path)
    except pd.PackageError as e:
        os.remove(path)
        raise _refuse(e) from None
    except BaseException:
        os.remove(path)
        raise
    who = manifest.get("person") or {}

    def target():
        with db.get_conn() as conn:
            return conn.execute("SELECT id FROM users WHERE id = ?", (str(who.get("id") or ""),)).fetchone()
    found = await run_in_threadpool(target)
    token = UPLOADS.add(path, admin["id"])
    return {"token": token, "preview": {
        "app": config.APP_TITLE, "appVersion": manifest.get("appVersion"), "exportedAt": manifest.get("exportedAt"),
        "exportedBy": manifest.get("exportedBy"),
        "person": {k: who.get(k) for k in ("id", "name", "username") if who.get(k) is not None},
        "counts": _count_items({k: len(rows[k]) for k in KINDS}),
        "warnings": _warnings(rows), "options": [], "target": found["id"] if found else None}}


# ---------- import: loading it ----------

class ImportBody(BaseModel):
    model_config = ConfigDict(extra="ignore")
    mode: Literal["add", "replace"] = "add"
    confirm: Optional[str] = None
    options: dict = {}


def _fold(text) -> str:
    return " ".join(str(text or "").split()).casefold()


def _food_key(date, eaten_at, meal_type, food_name, servings) -> tuple:
    return date, eaten_at or None, meal_type, _fold(food_name), float(servings)


def _clear(conn, uid: str) -> None:
    """Replace: the person's rows go (their users row stays — they aren't forgotten)."""
    for t in ("food_logs", "symptom_logs", "weight_logs", "saved_foods"):
        conn.execute(f"DELETE FROM {t} WHERE user_id = ?", (uid,))


def _set_goals(conn, uid: str, goals: dict) -> None:
    conn.execute("INSERT OR IGNORE INTO goals (user_id) VALUES (?)", (uid,))
    cols = list(GOAL_DEFAULTS)
    conn.execute(f"UPDATE goals SET {', '.join(c + ' = ?' for c in cols)} WHERE user_id = ?",
                 (*[goals.get(c) for c in cols], uid))


def _untouched_goals(conn, uid: str) -> bool:
    row = conn.execute("SELECT * FROM goals WHERE user_id = ?", (uid,)).fetchone()
    return row is None or all(row[c] == v for c, v in GOAL_DEFAULTS.items())


def load(conn, uid: str, rows: dict, mode: str) -> dict:
    """Inserts the prepared rows for `uid` (new ids; a food entry's saved food mapped). Add skips what's already
    there (§4: a food entry with the same date, time, meal, food and servings; a symptom with the same date, time
    and symptom; a weight on the same date; a saved food with the same name, folded) — compared with what the
    person had before the import, so two equal rows in the file both arrive. Returns {added: {table: n},
    skipped: n}."""
    if mode == "replace":
        _clear(conn, uid)
    added = {t: 0 for t in KINDS}
    skipped = 0
    ids = pd.IdMap()
    have_saved = {_fold(r["name"]): r["id"] for r in conn.execute(
        "SELECT id, name FROM saved_foods WHERE user_id = ? ORDER BY id DESC", (uid,))}   # the oldest wins
    have_food = {_food_key(*r) for r in conn.execute(
        "SELECT date, eaten_at, meal_type, food_name, servings FROM food_logs WHERE user_id = ?", (uid,))}
    have_sym = {(r[0], r[1], _fold(r[2])) for r in conn.execute(
        "SELECT date, time, symptom FROM symptom_logs WHERE user_id = ?", (uid,))}
    have_weight = {r[0] for r in conn.execute("SELECT date FROM weight_logs WHERE user_id = ?", (uid,))}

    # Goals: Replace takes the file's; Add keeps the person's — unless theirs were never set (still the defaults),
    # so that "delete, keep them" followed by an import brings their goals back too.
    if rows["goals"] and (mode == "replace" or _untouched_goals(conn, uid)):
        _set_goals(conn, uid, rows["goals"])
    if rows["users"] and mode == "replace":
        conn.execute("UPDATE users SET enabled = ? WHERE id = ?", (rows["users"]["enabled"], uid))

    for s in rows["saved_foods"]:
        existing = have_saved.get(_fold(s["name"]))
        if existing is not None:
            ids.put("saved_foods", s["old"], existing)
            skipped += 1
            continue
        cur = conn.execute(
            "INSERT INTO saved_foods (user_id, name, serving_size, serving_unit, calories, no_calories, protein, "
            "carbs, fat, notes, tags) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (uid, s["name"], s["serving_size"], s["serving_unit"], s["calories"] or 0, 1 if s["calories"] is None else 0,
             s["protein"], s["carbs"], s["fat"], s["notes"] or None, foodrows.tags_text(s["tags"])))
        ids.put("saved_foods", s["old"], cur.lastrowid)
        added["saved_foods"] += 1
    for f in rows["food_logs"]:
        if _food_key(f["date"], f["eaten_at"], f["meal_type"], f["food_name"], f["servings"]) in have_food:
            skipped += 1
            continue
        conn.execute(
            "INSERT INTO food_logs (user_id, date, meal_type, food_name, servings, calories, no_calories, protein, "
            "carbs, fat, saved_food_id, eaten_at, tags, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, COALESCE(?, datetime('now')))",
            (uid, f["date"], f["meal_type"], f["food_name"], f["servings"], f["calories"] or 0,
             1 if f["calories"] is None else 0, f["protein"], f["carbs"], f["fat"],
             ids.get("saved_foods", f["saved_food_id"]), f["eaten_at"], foodrows.tags_text(f["tags"]),
             f["created_at"]))
        added["food_logs"] += 1
    for s in rows["symptom_logs"]:
        if (s["date"], s["time"], _fold(s["symptom"])) in have_sym:
            skipped += 1
            continue
        conn.execute(
            "INSERT INTO symptom_logs (user_id, date, time, symptom, severity, note, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, COALESCE(?, datetime('now')))",
            (uid, s["date"], s["time"], s["symptom"], s["severity"], s["note"], s["created_at"]))
        added["symptom_logs"] += 1
    for w in rows["weight_logs"]:
        if w["date"] in have_weight:
            skipped += 1
            continue
        conn.execute("INSERT INTO weight_logs (user_id, date, weight_kg, note) VALUES (?, ?, ?, ?)",
                     (uid, w["date"], w["weight_kg"], w["note"]))
        added["weight_logs"] += 1
    return {"added": added, "skipped": skipped}


def _import_message(result: dict) -> str:
    a = result["added"]
    parts = [f"{a[t]} {LABELS[t]}" for t in KINDS if a[t]]
    text = ("Imported " + ", ".join(parts)) if parts else "Nothing new to import"
    n = result["skipped"]
    if n:
        text += f"; {n} already there {'was' if n == 1 else 'were'} skipped"
    return text + "."


@router.post("/{user_id}/data/import/{token}")
def do_import(user_id: str, token: str, background: BackgroundTasks, body: Optional[ImportBody] = None,
              admin: dict = Depends(require_admin)):
    body = body or ImportBody()
    item = UPLOADS.get(token, admin["id"])
    if item is None:
        raise HTTPException(404, "That file isn't here any more (they're kept an hour) — choose it again.")
    with db.get_conn() as conn:
        person = _person(conn, user_id)
    if body.mode == "replace" and not pd.names_match(body.confirm, person["name"]):
        raise HTTPException(409, f"To replace, type {person['name']}'s name exactly.")
    try:
        with pd.one_at_a_time():
            try:
                _, rows = _read(item["path"])
            except pd.PackageError as e:
                raise _refuse(e) from None
            with db.get_conn() as conn:          # one transaction: all of it or nothing
                _person(conn, user_id)
                result = load(conn, user_id, rows, body.mode)
    except pd.Busy as e:
        raise HTTPException(423, str(e)) from None
    UPLOADS.drop(token)
    background.add_task(ha_sync.push_for_user_blocking, user_id)      # today's total may have changed
    logger.info("Person data import by %s (%s) into %s (%s), %s: added %s; %d skipped", admin["name"], admin["id"],
                person["name"], person["id"], body.mode, _counts_text(result["added"]), result["skipped"])
    return {"message": _import_message(result), **result}


# ---------- delete ----------

class DeleteBody(BaseModel):
    model_config = ConfigDict(extra="ignore")
    confirm: str = ""
    forget: bool = False
    choices: dict = {}
    options: dict = {}


def _forget_cached_row(user_id: str) -> None:
    """auth remembers whose row is up to date (so a request writes nothing); after a delete that memory is wrong —
    a forgotten person's next request must put their row back, or their writes would fail."""
    with auth._known_lock:
        auth._known_rows.pop(user_id, None)


@router.post("/{user_id}/data/delete")
def delete(user_id: str, background: BackgroundTasks, body: Optional[DeleteBody] = None,
           admin: dict = Depends(require_admin)):
    body = body or DeleteBody()
    with db.get_conn() as conn:
        person = _person(conn, user_id)
    if not pd.names_match(body.confirm, person["name"]):
        raise HTTPException(409, f"To delete, type {person['name']}'s name exactly.")
    try:
        with pd.one_at_a_time():
            with db.get_conn() as conn:          # one transaction
                person = _person(conn, user_id)
                counts = _counts(conn, user_id)
                slug = auth.sensor_slug(conn, user_id, person["name"])      # before their row goes
                conn.execute("DELETE FROM users WHERE id = ?", (user_id,))  # every table of theirs cascades
                conn.execute("DELETE FROM ai_usage WHERE user_id = ?", (user_id,))
                if not body.forget:
                    # kept: in the list again, empty, as if new (same created_at, so nobody's sensor name moves)
                    conn.execute("INSERT INTO users (id, name, created_at) VALUES (?, ?, ?)",
                                 (user_id, person["name"], person["created_at"]))
                    conn.execute("INSERT INTO goals (user_id) VALUES (?)", (user_id,))
    except pd.Busy as e:
        raise HTTPException(423, str(e)) from None
    _forget_cached_row(user_id)
    background.add_task(ha_sync.remove_for_user, slug)
    if body.forget and ha_sync.exposed():
        background.add_task(ha_sync._sync_push_all)    # someone whose name gave the same slug may get it now
    logger.info("Person data delete by %s (%s) of %s (%s), %s: %s", admin["name"], admin["id"], person["name"],
                person["id"], "forgotten" if body.forget else "kept", _counts_text(counts))
    then = f"{person['name']} was forgotten." if body.forget else f"{person['name']} stays in the list, empty."
    return {"message": f"Deleted {_counts_text(counts)}. {then}", "deleted": {t: counts[t] for t in KINDS},
            "forgotten": body.forget}
