"""The admin page's API (SPEC.md §7). Every route but /api/health needs Home Assistant's identity headers and a
person on admin_users; main.py's guard admits only the Supervisor's ingress proxy and refuses cross-site writes.
"""
import asyncio
import os

from fastapi import APIRouter, Body, Depends, File, HTTPException, Request, UploadFile
from pydantic import BaseModel, ConfigDict, Field, StrictBool

from .. import auth, callers, config, db, gateway, keys, machine, models, network, ollama, schedule, server, \
    settings, usage
from ..auth import get_current_user, require_admin
from ..common import auth_core, backup_core
from ..common import whoami as whoami_core

router = APIRouter(prefix="/api")

RESTORE_PATH = "/api/admin-storage-import-db"
RESTORE_MAX = 256 << 20          # a restored database: settings, key hashes and 30 days of usage — far less than this


def _schedule():
    if server.SCHEDULE is None:
        raise HTTPException(503, "Starting — try again in a moment.")
    return server.SCHEDULE


def _need_ready():
    if server.SERVER.state != "ready":
        raise HTTPException(409, "Turn the model server on first (and wait until it is Ready).")


# --------------------------------------------------------------------------------------------- the person

@router.get("/me")
def me(user: dict = Depends(get_current_user)):
    return {"user": {"id": user["id"], "name": user["name"], "isAdmin": user["is_admin"]},
            "noAdmin": auth.no_admins(), "version": config.APP_VERSION}


@router.get("/whoami")
def whoami(request: Request, user: dict = Depends(get_current_user)):
    return whoami_core.build(
        request, user_id=user["id"], username=user["username"], display_name=user["name"],
        is_admin=user["is_admin"], admin_entries=len(config.ADMIN_USERS),
        display_name_only=(not user["is_admin"]) and auth_core.display_name_listed(user["name"], config.ADMIN_USERS))


# --------------------------------------------------------------------------------------------- status

@router.get("/status")
async def status(admin: dict = Depends(require_admin)):
    """Polled by the page every few seconds while the gateway streams answers on the same event loop: the file and
    database reads (machine, settings, key labels) run in a worker thread."""
    sched = _schedule()
    raw = []
    if server.SERVER.state == "ready":
        try:
            raw = await ollama.ps()
        except Exception:
            raw = []
    queue = gateway.QUEUE.snapshot()          # on the loop: the queue belongs to it
    lan = await network.published_port()

    def parts():
        values = settings.all()
        return values, machine.summary(), sched.describe(raw), _named(queue)
    values, mach, loaded, named = await asyncio.to_thread(parts)
    switches = gateway.STATS["switches"] if gateway.STATS["day"] == config.today().isoformat() else 0
    return {
        "server": server.SERVER.status(),
        "machine": mach,
        "loaded": loaded,
        "schedule": {"day": sched.is_day(values), "dayStart": values["day_start"], "dayEnd": values["day_end"],
                     "kept": sched.kept(values), "manualUnload": sched.manual_unload},
        "queue": named,
        "switchesToday": switches,
        "network": {"known": lan["known"], "port": lan["port"], "requireKey": values["require_key"]},
        "version": config.APP_VERSION,
    }


# The app's own places in the queue (server.turn and the day's loading), as the page names them.
OWN_JOBS = {"household_ai": "Household AI (loading for the day)",
            "household_ai:restart": "Household AI (restarting the model server)",
            "household_ai:delete": "Household AI (deleting a model)"}


def _named(snap: dict) -> dict:
    names = keys.labels()

    def name(c):
        return OWN_JOBS.get(c) or names.get(c) or callers.label(c)
    if snap["running"]:
        snap["running"]["caller"] = name(snap["running"]["caller"])
    for w in snap["waiting"]:
        w["caller"] = name(w["caller"])
    return snap


class ServerIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    on: StrictBool


@router.put("/server")
async def put_server(body: ServerIn, admin: dict = Depends(require_admin)):
    await asyncio.to_thread(settings.update, {"server_on": body.on}, admin)
    if body.on:
        await server.SERVER.start()
    else:
        await server.SERVER.stop("Turned off by an admin")
    return server.SERVER.status()


@router.post("/unload")
async def unload(admin: dict = Depends(require_admin)):
    _need_ready()
    try:
        names = await _schedule().unload_now()
    except Exception as e:
        raise HTTPException(502, f"The model server didn't answer: {e}")
    return {"unloaded": names}


@router.get("/log")
def log(admin: dict = Depends(require_admin)):
    return {"lines": list(server.SERVER.log)[-50:]}


# --------------------------------------------------------------------------------------------- models

@router.get("/models")
async def get_models(admin: dict = Depends(require_admin)):
    if server.SERVER.state == "ready":
        try:
            await ollama.MODELS.refresh()
        except Exception:
            pass
    out = await models.listing()
    out["serverReady"] = server.SERVER.state == "ready"
    return out


class PullIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1, max_length=240)


@router.post("/models/pull")
async def pull(body: PullIn, admin: dict = Depends(require_admin)):
    _need_ready()
    try:
        p = await models.start_pull(body.name)
    except ValueError as e:
        raise HTTPException(400, str(e))
    return p.view()


@router.get("/models/pull/{pull_id}")
def pull_status(pull_id: str, admin: dict = Depends(require_admin)):
    p = models.PULLS.get(pull_id)
    if p is None:
        raise HTTPException(404, "No such download.")
    return p.view()


@router.delete("/models/pull/{pull_id}")
def pull_cancel(pull_id: str, admin: dict = Depends(require_admin)):
    if not models.cancel(pull_id):
        raise HTTPException(404, "No such download running.")
    return {"status": "ok"}


@router.delete("/downloaded/{name:path}")
async def delete_model(name: str, admin: dict = Depends(require_admin)):
    """Delete a downloaded model. Its own path (not under /models/, where /models/pull/<id> is a download), and in
    turn: after the answer running now, so no answer loses its model half-way."""
    _need_ready()
    if not settings.MODEL_NAME.match(name):
        raise HTTPException(400, "That isn't a model name.")
    async with server.turn(gateway.QUEUE, "household_ai:delete"):
        try:
            await ollama.delete(name)
        except ollama.OllamaError as e:
            raise HTTPException(400, str(e))
        await ollama.MODELS.refresh()

    def forget_kept():
        kept = settings.get("keep_model")
        if kept and kept != "none" and schedule.norm_model(kept) == schedule.norm_model(name):
            settings.update({"keep_model": ""}, admin)
    await asyncio.to_thread(forget_kept)
    return {"status": "ok"}


class KeepIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(max_length=240)       # "" = the first text model downloaded, "none" = keep nothing


@router.put("/models/keep")
def keep_model(body: KeepIn, admin: dict = Depends(require_admin)):
    try:
        settings.update({"keep_model": body.name}, admin)
    except settings.SettingsError as e:
        raise HTTPException(422, str(e))
    return {"keep": settings.get("keep_model")}


# --------------------------------------------------------------------------------------------- callers & keys

def connection() -> dict:
    host = config.own_hostname()
    return {"host": host, "address": f"http://{host}:11434", "openaiAddress": f"http://{host}:11434/v1",
            "prefixKnown": callers.prefix(host) is not None}


@router.get("/callers")
def get_callers(admin: dict = Depends(require_admin)):
    first = set(settings.get("answer_first") or [])
    key_labels = keys.labels()
    rows = []
    for r in usage.callers():
        c = r["caller"]
        name = key_labels.get(c) or callers.label(c)
        rows.append({**r, "name": name, "answerFirst": c in first, "advice": callers.ADVICE.get(c)})
    return {"connection": connection(), "callers": rows, "requireKey": settings.get("require_key"),
            "known": {a: s for a, s in callers.RESOLVER.by_address.items()},
            "advice": callers.ADVICE, "apps": callers.APPS}


class FirstIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    on: StrictBool


@router.put("/callers/{caller}/answer-first")
def answer_first(caller: str, body: FirstIn, admin: dict = Depends(require_admin)):
    if not settings.CALLER_NAME.match(caller) and not caller.startswith("key_"):
        raise HTTPException(400, "That isn't a caller.")
    try:
        return {"answerFirst": _set_answer_first(caller, body.on, admin)}
    except settings.SettingsError as e:
        raise HTTPException(422, str(e))


@router.get("/usage")
def get_usage(admin: dict = Depends(require_admin)):
    names = keys.labels()
    return {"days": [{**d, "name": names.get(d["caller"]) or callers.label(d["caller"])} for d in usage.days()]}


@router.get("/keys")
def get_keys(admin: dict = Depends(require_admin)):
    return {"keys": keys.rows(), "requireKey": settings.get("require_key")}


class KeyIn(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    label: str = Field(min_length=1, max_length=80)
    models: list[str] | None = Field(default=None, max_length=50)
    answerFirst: StrictBool = False


def _set_answer_first(caller: str, on: bool, admin: dict) -> list:
    cur = list(settings.get("answer_first") or [])
    if on and caller not in cur:
        cur.append(caller)
    if not on:
        cur = [c for c in cur if c != caller]
    settings.update({"answer_first": cur}, admin)
    return settings.get("answer_first")


@router.post("/keys")
def new_key(body: KeyIn, admin: dict = Depends(require_admin)):
    for m in body.models or []:
        if not settings.MODEL_NAME.match(m):
            raise HTTPException(400, f"{m!r} isn't a model name.")
    try:
        kid, key = keys.create(body.label, body.models, admin["id"])
    except ValueError as e:
        raise HTTPException(400, str(e))
    if body.answerFirst:
        try:
            _set_answer_first(kid, True, admin)
        except settings.SettingsError as e:
            raise HTTPException(422, str(e))
    return {"id": kid, "key": key, "answerFirst": body.answerFirst}


@router.delete("/keys/{kid}")
def revoke_key(kid: str, admin: dict = Depends(require_admin)):
    if not keys.revoke(kid):
        raise HTTPException(404, "No such key (or already revoked).")
    if kid in (settings.get("answer_first") or []):
        _set_answer_first(kid, False, admin)
    return {"status": "ok"}


# --------------------------------------------------------------------------------------------- settings

@router.get("/admin/settings")
def get_settings(admin: dict = Depends(require_admin)):
    return settings.payload()


@router.put("/admin/settings")
async def put_settings(body: dict = Body(...), admin: dict = Depends(require_admin)):
    try:
        changed = await asyncio.to_thread(settings.update, body, admin)
    except settings.SettingsError as e:
        raise HTTPException(422, str(e))
    if settings.SERVER_KEYS & set(changed) and server.SERVER.running():
        # In turn: after the answer running now (server.turn); the page says the server restarts.
        server.SERVER.restart_soon(gateway.QUEUE)
    return await asyncio.to_thread(settings.payload)


# --------------------------------------------------------------------------------------------- backups

@router.get("/admin-storage-download-db")
def download_db(admin: dict = Depends(require_admin)):
    """The database: settings, key hashes and usage. The models are not in it (download them again)."""
    tmp_path = db.backup_to_tempfile()
    return backup_core.send_file(tmp_path, backup_core.file_name("household-ai-backup", ".db", now=config.now()))


def _server_values() -> dict:
    values = settings.all()
    return {k: values[k] for k in ("server_on", *sorted(settings.SERVER_KEYS))}


def _restore(tmp_path: str) -> tuple[dict, dict]:
    """Validate and swap in the backup (blocking: run in a thread); the model server's settings before and after."""
    try:
        db.validate_backup_file(tmp_path)
    except ValueError as e:
        raise HTTPException(400, str(e))
    before = _server_values()
    db.import_from_tempfile(tmp_path)
    settings.invalidate()
    return before, _server_values()


@router.post("/admin-storage-import-db")
async def import_db(file: UploadFile = File(...), admin: dict = Depends(require_admin)):
    def too_big():
        return HTTPException(413, f"A backup of Household AI is never this large (over {RESTORE_MAX >> 20} MB).")
    tmp_path = await backup_core.receive(file, config.DATA_DIR, prefix=db.RESTORE_PREFIX, max_bytes=RESTORE_MAX,
                                         too_big=too_big)
    try:
        before, after = await asyncio.to_thread(_restore, tmp_path)
    finally:
        if os.path.exists(tmp_path):
            os.remove(tmp_path)
    # The model server follows the restored settings: on or off, and restarted for a changed context or models
    # loaded at once.
    if after["server_on"] != before["server_on"]:
        if after["server_on"]:
            await server.SERVER.start()
        else:
            await server.SERVER.stop("Turned off by the restored backup")
    elif after != before and server.SERVER.running():
        server.SERVER.restart_soon(gateway.QUEUE)
    return {"status": "ok"}
