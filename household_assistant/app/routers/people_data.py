"""One person's data: export, import and delete it (Admin → People → Data; PERSON_DATA_SPEC.md §2 and §8,
spec/SPEC.md "One person's data"). The file format, its checks and the dialogs are shared (app/common/person_data.py,
static/common/person-data.js); this module says what is a person's here and how it is loaded back.

What is theirs: their briefing settings (`briefings`), and — only when the admin ticks **Include their questions and
answers** — their questions (`questions`) with the tool calls behind each answer (`calls`). The `users` row goes in
the file as information (their name, and the admin's switches as they were) and is never loaded: this install's admin
decides who may ask and who is a child. Never in a file: `rate_events` (the hourly limits), the learned quick phrases,
misses and words (they store no person), the usage totals, the catalogue, App settings and the AI key.

Questions are the one thing an admin can't otherwise see, so the option is off by default and every export's log line
says whether it was on.
"""
import json
import logging
import os
import threading
import time
from datetime import datetime, timedelta

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from fastapi.responses import Response
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator
from starlette.concurrency import run_in_threadpool

from .. import briefing, config, db, engine, settings
from ..auth import require_admin
from ..common import app_bus as bus
from ..common import backup_core
from ..common import person_data as pd
from ..quick import vocab as quick_vocab

logger = logging.getLogger("people_data")
router = APIRouter(prefix="/api/admin/people")

# The data version of this app's export files: 1 = the tables as they are now (users with `reviewed`, questions with
# mode/quick/understood, briefings with the evening and the speaker). Raise it when a later update changes what a
# row in the file means, and bring older files up to date in _rows() (columns added since are optional there).
SCHEMA = 1
APP = config.SLUG
TABLES = ("users", "briefings", "questions", "calls")
UPLOAD_PREFIX = "household-assistant-person-"            # uploads waiting for their import, swept after an hour
MAX_UPLOAD = 64 << 20                                    # main.py lets this route's body be this big
LIMITS = pd.Limits(max_upload=MAX_UPLOAD, max_unpacked=256 << 20, max_members=20, max_json=MAX_UPLOAD,
                   max_rows=500_000)
STOP_WAIT = 5.0                                          # seconds a stopped question's thread gets to end first

QUESTION_COLS = ("id", "asked_at", "text", "answer", "state", "error", "rounds", "input_tokens", "output_tokens",
                 "seconds", "mode", "quick", "understood")
CALL_COLS = ("id", "question_id", "app", "tool", "args", "say", "bus_id", "round", "confirmed_at", "sent_at",
             "answered_at", "state", "result", "reason")
BRIEFING_COLS = ("on_", "at", "days", "last_sent", "updated_at", "evening_on", "evening_at", "evening_last_sent",
                 "speaker")

uploads = pd.Uploads(config.DATA_DIR, prefix=UPLOAD_PREFIX)
jobs = pd.Jobs()                # every export here is small (no files) and downloads at once; kept for the API's shape
_busy = threading.Lock()


# --------------------------------------------------------------------------------------------- rows in a file

def _when(v):
    """A timestamp from the file: ISO 8601 with its zone, at most 40 characters (or None)."""
    if v is None:
        return None
    if not isinstance(v, str) or len(v) > 40:
        raise ValueError("isn't a time")
    try:
        dt = datetime.fromisoformat(v.replace("Z", "+00:00"))
    except ValueError:
        raise ValueError("isn't a time") from None
    if dt.tzinfo is None:
        raise ValueError("has no time zone")
    return v


def _json_text(v, *, want=dict):
    """A JSON column (args, result, quick): text that reads as a JSON object, as the page reads it."""
    if v is None:
        return None
    if not isinstance(v, str):
        raise ValueError("isn't JSON text")
    try:
        got = json.loads(v)
    except ValueError:
        raise ValueError("isn't JSON") from None
    if not isinstance(got, want):
        raise ValueError("isn't a JSON object")
    return v


class QuestionRow(BaseModel):
    """A question as the engine stores it (the same limits as asking: MAX_QUESTION, MAX_ANSWER)."""
    model_config = ConfigDict(extra="ignore")
    id: str = Field(min_length=1, max_length=64)
    asked_at: str
    text: str = Field(min_length=1, max_length=engine.MAX_QUESTION)
    answer: str | None = Field(None, max_length=engine.MAX_ANSWER)
    state: str = Field(pattern="^(planning|calling|answering|done|failed|stopped)$")
    error: str | None = Field(None, max_length=1000)
    rounds: int = Field(0, ge=0, le=100)
    input_tokens: int | None = Field(None, ge=0)
    output_tokens: int | None = Field(None, ge=0)
    seconds: float | None = Field(None, ge=0, le=86400)
    mode: str = Field("ai", pattern="^(ai|quick)$")
    quick: str | None = Field(None, max_length=20000)
    understood: str | None = Field(None, max_length=500)

    @field_validator("asked_at")
    @classmethod
    def _asked(cls, v):
        if v is None:
            raise ValueError("is missing")
        return _when(v)

    @field_validator("quick")
    @classmethod
    def _quick(cls, v):
        return _json_text(v)


class CallRow(BaseModel):
    """A tool call behind an answer: the app, the tool, what was asked and what came back."""
    model_config = ConfigDict(extra="ignore")
    id: str = Field(min_length=1, max_length=64)
    question_id: str = Field(min_length=1, max_length=64)
    app: str = Field(min_length=1, max_length=64, pattern=r"^[a-z0-9_]+$")
    tool: str = Field(min_length=1, max_length=100)
    args: str = Field(max_length=20000)
    say: str | None = Field(None, max_length=1000)
    round: int = Field(0, ge=0, le=100)
    confirmed_at: str | None = None
    sent_at: str | None = None
    answered_at: str | None = None
    state: str = Field(pattern="^(proposed|sent|ok|nack|timeout)$")
    result: str | None = Field(None, max_length=1 << 20)
    reason: str | None = Field(None, max_length=1000)

    @field_validator("confirmed_at", "sent_at", "answered_at")
    @classmethod
    def _times(cls, v):
        return _when(v)

    @field_validator("args", "result")
    @classmethod
    def _json(cls, v):
        return _json_text(v)


class BriefingRow(BaseModel):
    """The briefing settings, checked as the Briefings dialog checks them (briefing.TIME_RE, DAYS, SPEAKER_RE)."""
    model_config = ConfigDict(extra="ignore")
    on_: int = Field(0, ge=0, le=1)
    at: str = Field("07:30", pattern=briefing.TIME_RE.pattern)
    days: str = Field("every", pattern="^(every|weekdays)$")
    last_sent: str | None = Field(None, pattern=r"^\d{4}-\d{2}-\d{2}$")
    updated_at: str | None = None
    evening_on: int = Field(0, ge=0, le=1)
    evening_at: str = Field("20:00", pattern=briefing.TIME_RE.pattern)
    evening_last_sent: str | None = Field(None, pattern=r"^\d{4}-\d{2}-\d{2}$")
    speaker: str | None = Field(None, pattern=briefing.SPEAKER_RE.pattern)

    @field_validator("updated_at")
    @classmethod
    def _updated(cls, v):
        return _when(v)


MODELS = {"questions": QuestionRow, "calls": CallRow, "briefings": BriefingRow}
LABELS = {"questions": "questions", "calls": "calls", "briefings": "briefings"}


def _rows(pkg: pd.Package) -> dict:
    """Every row of the file checked with this app's own limits; a row that doesn't fit refuses the whole file with
    its table and row number. Unknown columns are dropped. Returns {table: [clean dicts]}."""
    out = {}
    for table, model in MODELS.items():
        clean = []
        for i, raw in enumerate(pkg.tables.get(table) or []):
            try:
                clean.append(model(**raw).model_dump())
            except (ValidationError, TypeError) as e:
                where = ""
                if isinstance(e, ValidationError) and e.errors():
                    err = e.errors()[0]
                    where = (".".join(str(x) for x in err.get("loc") or ()) + ": " if err.get("loc") else "") \
                        + str(err.get("msg") or "")
                raise pd.PackageError(f"{table}, row {i + 1}, doesn't fit: {where or 'not a row'}"[:300]) from None
        out[table] = clean
    if len(out["briefings"]) > 1:
        raise pd.PackageError("briefings, row 2: a person has one row of briefing settings.")
    qids = {q["id"] for q in out["questions"]}
    if len(qids) != len(out["questions"]):
        raise pd.PackageError("questions: the same question id is in the file twice.")
    for i, c in enumerate(out["calls"]):
        if c["question_id"] not in qids:
            raise pd.PackageError(f"calls, row {i + 1}: its question isn't in the file.")
    return out


def _cutoff() -> str:
    """Questions asked before this are past *Keep questions for*: housekeeping would delete them, so they aren't
    imported."""
    return engine._iso(config.utcnow() - timedelta(days=settings.get("keep_days")))


def _asked(v: str) -> str:
    """asked_at as the engine writes it (UTC, to the second), so a duplicate compares equal and history sorts."""
    return engine._iso(datetime.fromisoformat(v.replace("Z", "+00:00")))


# --------------------------------------------------------------------------------------------- reading a person

def _person(conn, user_id: str):
    row = conn.execute("SELECT id, name, enabled, is_child, created_at FROM users WHERE id = ?", (user_id,)).fetchone()
    if row is None:
        raise HTTPException(404, "No such person.")
    return row


def _counts(conn, user_id: str) -> dict:
    q = conn.execute("SELECT COUNT(*), MIN(asked_at) FROM questions WHERE user_id = ?", (user_id,)).fetchone()
    calls = conn.execute("SELECT COUNT(*) FROM calls c JOIN questions q ON q.id = c.question_id WHERE q.user_id = ?",
                         (user_id,)).fetchone()[0]
    b = conn.execute("SELECT on_, evening_on, speaker FROM briefings WHERE user_id = ?", (user_id,)).fetchone()
    running = conn.execute(f"SELECT COUNT(*) FROM questions WHERE user_id = ? AND state IN "
                           f"({','.join('?' * len(engine.RUNNING))})", (user_id, *engine.RUNNING)).fetchone()[0]
    marks = conn.execute("SELECT COUNT(*) FROM rate_events WHERE user_id = ?", (user_id,)).fetchone()[0]
    return {"questions": q[0], "oldest": q[1], "calls": calls, "briefing_row": b is not None,
            "briefings_on": (b["on_"] + b["evening_on"]) if b else 0, "speaker": b["speaker"] if b else None,
            "running": running, "rate_events": marks}


def _summary(conn, user_id: str) -> dict:
    p = _person(conn, user_id)
    n = _counts(conn, user_id)
    counts = [
        {"key": "questions", "label": "questions and answers kept", "count": n["questions"],
         **({"detail": f"the oldest from {n['oldest'][:10]}"} if n["oldest"] else {})},
        {"key": "calls", "label": "app answers behind them", "count": n["calls"]},
        {"key": "briefings", "label": "briefings on", "count": n["briefings_on"],
         **({"detail": f"read aloud on {n['speaker']}"} if n["speaker"] else {})},
    ]
    return {
        "person": {"id": p["id"], "name": p["name"]},
        "counts": counts,
        "exportOptions": [{"key": "questions", "label": "Include their questions and answers", "default": False,
                           "help": "Off: only their settings (to move them). On: their own history, to give to them "
                                   "— the one way an admin could read someone's questions, so the app's log records "
                                   "it."}],
        "exportNote": "The file isn't encrypted. With questions and answers it holds what they asked and what the "
                      "household apps answered about them — keep it somewhere safe.",
        "keeps": ["Learned quick phrases and words, and the questions the rules couldn't place (they store no person)",
                  "The household's usage totals (Admin → Usage)"],
        "cannotReach": ["whole-app backups and Home Assistant backups made before",
                        "app messages already sent to other apps (gone within 7 days)",
                        "Home Assistant's own event history, unless its recorder excludes household_apps "
                        "(App settings → Privacy)"],
        "deleteChoices": [],
        "forgetNote": "Their row goes too: the next time they open the assistant or ask through Assist they are "
                      "someone new, shown as New until an admin checks them. Remove them in Home Assistant first if "
                      "they shouldn't come back.",
        "deleteNote": (f"A question of theirs is still being answered: it is stopped first. " if n["running"] else "")
        + "Their questions, answers, briefings and hourly-limit marks go for good.",
    }


# --------------------------------------------------------------------------------------------- export

def _export(conn, user_id: str, with_questions: bool, by: str) -> tuple[bytes, str, dict]:
    p = _person(conn, user_id)
    tables = {"users": [{"id": p["id"], "name": p["name"], "enabled": p["enabled"], "is_child": p["is_child"],
                         "created_at": p["created_at"]}]}
    b = conn.execute(f"SELECT {', '.join(BRIEFING_COLS)} FROM briefings WHERE user_id = ?", (user_id,)).fetchall()
    tables["briefings"] = [dict(r) for r in b]
    if with_questions:
        qs = conn.execute(f"SELECT {', '.join(QUESTION_COLS)} FROM questions WHERE user_id = ? ORDER BY asked_at, id",
                          (user_id,)).fetchall()
        tables["questions"] = [dict(r) for r in qs]
        cs = conn.execute(f"SELECT {', '.join('c.' + c for c in CALL_COLS)} FROM calls c JOIN questions q ON "
                          "q.id = c.question_id WHERE q.user_id = ? ORDER BY q.asked_at, c.round, c.id",
                          (user_id,)).fetchall()
        tables["calls"] = [dict(r) for r in cs]
    data = pd.bytes_io_package(
        app=APP, app_version=config.APP_VERSION, schema=SCHEMA, person={"id": p["id"], "name": p["name"]},
        exported_by=by, tables=tables, options={"questions": with_questions}, title=config.APP_TITLE,
        what="their briefing settings" + (", their questions and the answers" if with_questions else ""))
    counts = {k: len(v) for k, v in tables.items() if k != "users"}
    return data, pd.file_name(config.APP_TITLE, p["name"], now=config.now()), counts


# --------------------------------------------------------------------------------------------- delete

def _stop_running(user_id: str) -> None:
    """Stops their questions still being answered, and gives each thread a moment to end, so nothing writes a call
    or an answer for a question that is about to be gone."""
    with db.get_conn() as conn:
        qids = [r[0] for r in conn.execute(f"SELECT id FROM questions WHERE user_id = ? AND state IN "
                                           f"({','.join('?' * len(engine.RUNNING))})",
                                           (user_id, *engine.RUNNING)).fetchall()]
    with engine._lock:
        qids += [q for q, owner in engine._owners.items() if owner == user_id and q not in qids]
    for qid in qids:
        engine.stop(qid, user_id)
    end = time.monotonic() + STOP_WAIT
    while time.monotonic() < end:
        with engine._lock:
            if not any(q in engine._stops for q in qids):
                return
        time.sleep(0.02)
    logger.warning("A stopped question's thread was still running when %s's data was deleted", user_id)


def _delete_rows(conn, user_id: str) -> dict:
    """Their questions (calls cascade), briefings and hourly-limit marks, in the caller's transaction."""
    calls = conn.execute("DELETE FROM calls WHERE question_id IN (SELECT id FROM questions WHERE user_id = ?)",
                         (user_id,)).rowcount
    return {"questions": conn.execute("DELETE FROM questions WHERE user_id = ?", (user_id,)).rowcount,
            "calls": calls,
            "briefings": conn.execute("DELETE FROM briefings WHERE user_id = ?", (user_id,)).rowcount,
            "rate_events": conn.execute("DELETE FROM rate_events WHERE user_id = ?", (user_id,)).rowcount}


def _forget_caches(user_id: str) -> None:
    engine.forget_recent(user_id)                    # the answer-reuse cache: their reads of the apps
    quick_vocab.forget(user_id)                      # the names fetched for their questions


# --------------------------------------------------------------------------------------------- import

def _preview(pkg: pd.Package, rows: dict) -> dict:
    m = pkg.manifest
    person = pkg.person
    with db.get_conn() as conn:
        here = person.get("id") and conn.execute("SELECT 1 FROM users WHERE id = ?", (str(person["id"]),)).fetchone()
    cutoff = _cutoff()
    old = sum(1 for q in rows["questions"] if _asked(q["asked_at"]) < cutoff)
    proposed = sum(1 for c in rows["calls"] if c["state"] == "proposed")
    warnings = []
    if "questions" not in pkg.tables:
        warnings.append("Exported without their questions and answers: only the briefing settings come in.")
    if old:
        warnings.append(f"{old} of the questions are older than Keep questions for ({settings.get('keep_days')} "
                        "days) and won't be kept.")
    if proposed:
        warnings.append(f"{proposed} proposed changes that were never tapped come in as a record only.")
    spk = (rows["briefings"][0]["speaker"] if rows["briefings"] else None)
    if spk:
        try:
            known = briefing.is_speaker(spk)
        except Exception:                            # Home Assistant not answering: say nothing either way
            known = True
        if not known:
            warnings.append(f"The speaker {spk} isn't one of this Home Assistant's: it is kept and shown as not "
                            "found.")
    warnings.append("May ask and Child aren't imported: they stay as they are set here.")
    counts = [{"key": "questions", "label": "questions and answers", "count": len(rows["questions"])},
              {"key": "calls", "label": "app answers behind them", "count": len(rows["calls"])},
              {"key": "briefings", "label": "briefing settings", "count": len(rows["briefings"])}]
    return {"app": m.get("app"), "appVersion": m.get("appVersion"), "exportedAt": m.get("exportedAt"),
            "exportedBy": m.get("exportedBy"), "person": {k: person.get(k) for k in ("id", "name") if k in person},
            "counts": counts, "warnings": warnings, "options": [],
            "target": str(person["id"]) if here else None}


def _load(conn, user_id: str, rows: dict, mode: str) -> dict:
    """Loads the checked rows into `user_id` in the caller's transaction: new ids for every question and call, the
    calls pointed at their question's new id. Duplicates (a question asked at the same time with the same text) and
    questions past *Keep questions for* are skipped and counted. Imported questions count toward no limit and no
    usage total. A proposed change comes in as a record only (it is never one tap away from being sent again)."""
    got = {"questions": 0, "calls": 0, "briefings": 0, "skipped": 0, "old": 0}
    if rows["briefings"]:
        b = rows["briefings"][0]
        has = conn.execute("SELECT 1 FROM briefings WHERE user_id = ?", (user_id,)).fetchone()
        if mode == "replace" or not has:              # Add keeps the person's own settings
            conn.execute("DELETE FROM briefings WHERE user_id = ?", (user_id,))
            conn.execute(f"INSERT INTO briefings (user_id, {', '.join(BRIEFING_COLS)}) VALUES "
                         f"(?, {', '.join('?' * len(BRIEFING_COLS))})", (user_id, *(b[c] for c in BRIEFING_COLS)))
            got["briefings"] = 1
        else:
            got["skipped"] += 1
    seen = {(r[0], r[1]) for r in conn.execute("SELECT asked_at, text FROM questions WHERE user_id = ?", (user_id,))}
    ids = pd.IdMap()
    cutoff = _cutoff()
    same_second: dict[str, int] = {}
    # in the order they were asked (old ids are ULIDs too, so they break a tie the way the page did)
    for q in sorted(rows["questions"], key=lambda q: (_asked(q["asked_at"]), q["id"])):
        asked = _asked(q["asked_at"])
        if asked < cutoff:
            got["old"] += 1
            continue
        if (asked, q["text"]) in seen:
            got["skipped"] += 1
            continue
        seen.add((asked, q["text"]))
        # a new ULID from when it was asked, a millisecond apart within one second, so the history (sorted by
        # asked_at, then id) lists them as before
        k = same_second[asked] = same_second.get(asked, -1) + 1
        new = bus.new_ulid(datetime.fromisoformat(asked) + timedelta(milliseconds=min(k, 999)))
        ids.put("questions", q["id"], new)
        state = "stopped" if q["state"] in engine.RUNNING else q["state"]     # nothing is answering it here
        cols = [c for c in QUESTION_COLS if c not in ("id", "asked_at", "state")]
        conn.execute(f"INSERT INTO questions (id, user_id, asked_at, state, {', '.join(cols)}) VALUES "
                     f"(?, ?, ?, ?, {', '.join('?' * len(cols))})", (new, user_id, asked, state, *(q[c] for c in cols)))
        got["questions"] += 1
    for c in rows["calls"]:
        qid = ids.get("questions", c["question_id"])
        if qid is None:                               # its question was skipped
            continue
        state = "timeout" if c["state"] in ("proposed", "sent") else c["state"]
        cols = [k for k in CALL_COLS if k not in ("id", "question_id", "bus_id", "state")]
        conn.execute(f"INSERT INTO calls (id, question_id, state, {', '.join(cols)}) VALUES "
                     f"(?, ?, ?, {', '.join('?' * len(cols))})", (bus.new_ulid(), qid, state, *(c[k] for k in cols)))
        got["calls"] += 1
    return got


def _plural(n: int, one: str, many: str) -> str:
    return f"{n} {one if n == 1 else many}"


# --------------------------------------------------------------------------------------------- routes

@router.get("/{user_id}/data")
def data_summary(user_id: str, admin: dict = Depends(require_admin)):
    """What they have here, for the Export and Delete dialogs (also the delete's dry run)."""
    with db.get_conn() as conn:
        return _summary(conn, user_id)


class ExportIn(BaseModel):
    model_config = ConfigDict(extra="ignore")
    options: dict = Field(default_factory=dict)


@router.post("/{user_id}/data/export")
def data_export(user_id: str, body: ExportIn | None = None, admin: dict = Depends(require_admin)):
    """The .zip, at once (it holds no files, so it is never big enough for a background job)."""
    with_q = (body or ExportIn()).options.get("questions") is True
    with db.get_conn() as conn:
        data, name, counts = _export(conn, user_id, with_q, admin["name"])
    logger.info("%s (%s) exported the data of %s: %s; questions and answers %s", admin["name"], admin["id"], user_id,
                counts, "included" if with_q else "left out")
    return Response(data, media_type="application/zip",
                    headers={"Content-Disposition": f'attachment; filename="{name}"', "Cache-Control": "no-store"})


@router.get("/data/jobs/{job}")
def data_job(job: str, admin: dict = Depends(require_admin)):
    st = jobs.status(job, admin["id"])
    if st is None:
        raise HTTPException(404, "No such export.")
    return st


@router.get("/data/jobs/{job}/file")
def data_job_file(job: str, admin: dict = Depends(require_admin)):
    j = jobs.get(job, admin["id"])
    if j is None or j["state"] != "done" or not j.get("path") or not os.path.exists(j["path"]):
        raise HTTPException(404, "No such export.")
    return backup_core.send_file(j["path"], j["filename"], media_type="application/zip")


def _too_big():
    return HTTPException(413, "That file is too big for an export of this app.")


@router.post("/data/import")
async def data_upload(file: UploadFile = File(...), admin: dict = Depends(require_admin)):
    """The export file, checked and kept an hour by token for the import step; answers its preview."""
    path = await backup_core.receive(file, config.DATA_DIR, suffix=".zip", prefix=UPLOAD_PREFIX + "up-",
                                     max_bytes=MAX_UPLOAD, too_big=_too_big)

    def check():
        with pd.read_package(path, app=APP, schema=SCHEMA, limits=LIMITS, tables=TABLES) as pkg:
            return _preview(pkg, _rows(pkg))
    try:
        preview = await run_in_threadpool(check)
    except pd.PackageError as e:
        os.remove(path)
        raise HTTPException(400, str(e))
    except BaseException:
        os.remove(path)
        raise
    token = uploads.add(path, admin["id"])
    return {"token": token, "preview": preview}


class ImportIn(BaseModel):
    model_config = ConfigDict(extra="ignore")
    mode: str = Field("add", pattern="^(add|replace)$")
    confirm: str | None = Field(None, max_length=200)
    options: dict = Field(default_factory=dict)


@router.post("/{user_id}/data/import/{token}")
def data_import(user_id: str, token: str, body: ImportIn, admin: dict = Depends(require_admin)):
    item = uploads.get(token, admin["id"])
    if item is None:
        raise HTTPException(404, "That file is no longer here (it is kept an hour) — choose it again.")
    try:
        with pd.one_at_a_time(_busy):
            with db.get_conn() as conn:
                target = _person(conn, user_id)
            if body.mode == "replace" and not pd.names_match(body.confirm, target["name"]):
                raise HTTPException(409, f"Type {target['name']} to replace their data.")
            try:
                with pd.read_package(item["path"], app=APP, schema=SCHEMA, limits=LIMITS, tables=TABLES) as pkg:
                    rows = _rows(pkg)
            except pd.PackageError as e:
                raise HTTPException(400, str(e))
            if body.mode == "replace":
                _stop_running(user_id)
            with db.get_conn() as conn:
                conn.execute("BEGIN IMMEDIATE")
                removed = _delete_rows(conn, user_id) if body.mode == "replace" else None
                got = _load(conn, user_id, rows, body.mode)
    except pd.Busy as e:
        raise HTTPException(423, str(e))
    if removed is not None:
        _forget_caches(user_id)
    uploads.drop(token)
    logger.info("%s (%s) imported data into %s (%s): %s question(s), %s call(s), %s briefing row(s); %s skipped as "
                "already there, %s too old%s", admin["name"], admin["id"], user_id, body.mode, got["questions"],
                got["calls"], got["briefings"], got["skipped"], got["old"],
                f"; replaced {removed}" if removed is not None else "")
    parts = [_plural(got["questions"], "question and answer", "questions and answers")]
    if got["briefings"]:
        parts.append("their briefing settings")
    msg = "Imported " + ", ".join(parts)
    extra = []
    if got["skipped"]:
        extra.append(f"{got['skipped']} already there {'was' if got['skipped'] == 1 else 'were'} skipped")
    if got["old"]:
        extra.append(f"{got['old']} too old to keep {'was' if got['old'] == 1 else 'were'} left out")
    return {"message": msg + ("; " + ", ".join(extra) if extra else "") + ".", "imported": got}


class DeleteIn(BaseModel):
    model_config = ConfigDict(extra="ignore")
    confirm: str | None = Field(None, max_length=200)
    forget: bool = False
    choices: dict = Field(default_factory=dict)
    options: dict = Field(default_factory=dict)


@router.post("/{user_id}/data/delete")
def data_delete(user_id: str, body: DeleteIn, admin: dict = Depends(require_admin)):
    """Their questions (with calls), briefings and hourly-limit marks, for good, in one transaction; a running
    question is stopped first; their row stays (same switches) unless `forget`. No files and no Home Assistant
    sensors here."""
    try:
        with pd.one_at_a_time(_busy):
            with db.get_conn() as conn:
                p = _person(conn, user_id)
            if not pd.names_match(body.confirm, p["name"]):
                raise HTTPException(409, f"Type {p['name']} to delete their data.")
            _stop_running(user_id)
            with db.get_conn() as conn:
                conn.execute("BEGIN IMMEDIATE")
                gone = _delete_rows(conn, user_id)
                if body.forget:
                    conn.execute("DELETE FROM users WHERE id = ?", (user_id,))
    except pd.Busy as e:
        raise HTTPException(423, str(e))
    _forget_caches(user_id)
    logger.info("%s (%s) deleted the data of %s%s: %s", admin["name"], admin["id"], user_id,
                " and forgot them" if body.forget else "", gone)
    msg = (f"Deleted {_plural(gone['questions'], 'question', 'questions')} and "
           f"{'their briefing settings' if gone['briefings'] else 'no briefing settings'}"
           + (f"; {p['name']} is forgotten." if body.forget else f"; {p['name']} stays, empty."))
    return {"message": msg, "deleted": gone, "forgot": bool(body.forget)}
