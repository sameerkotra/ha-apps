"""The settings page's API. No login on a Docker host (app/auth.py): main.py refuses only cross-site writes."""
import asyncio
import os

from fastapi import APIRouter, Body, Depends, File, HTTPException, Request, UploadFile
from pydantic import BaseModel, ConfigDict, Field, StrictBool

from .. import auth, callers, config, db, gateway, keys, machine, models, network, ollama, schedule, server, \
    settings, usage
from ..auth import get_current_user, require_admin
from ..common import backup_core

router = APIRouter(prefix="/api")


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
            "noAdmin": auth.no_admins(), "version": config.APP_VERSION, "standalone": True}


# --------------------------------------------------------------------------------------------- status

@router.get("/status")
async def status(admin: dict = Depends(require_admin)):
    sched = _schedule()
    loaded = []
    if server.SERVER.state == "ready":
        try:
            loaded = sched.describe(await ollama.ps())
        except Exception:
            loaded = []
    values = settings.all()
    lan = await network.published_port()
    switches = gateway.STATS["switches"] if gateway.STATS["day"] == config.today().isoformat() else 0
    return {
        "server": server.SERVER.status(),
        "machine": machine.summary(),
        "loaded": loaded,
        "schedule": {"day": sched.is_day(values), "dayStart": values["day_start"], "dayEnd": values["day_end"],
                     "kept": sched.kept(values), "manualUnload": sched.manual_unload},
        "queue": _named(gateway.QUEUE.snapshot()),
        "switchesToday": switches,
        "network": {"known": lan["known"], "port": lan["port"], "requireKey": values["require_key"]},
        "version": config.APP_VERSION,
    }


def _named(snap: dict) -> dict:
    names = keys.labels()

    def name(c):
        return "Household AI (loading for the day)" if c == "household_ai" else names.get(c) or callers.label(c)
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
    settings.update({"server_on": body.on}, admin)
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


@router.delete("/models/{name:path}")
async def delete_model(name: str, admin: dict = Depends(require_admin)):
    _need_ready()
    if not settings.MODEL_NAME.match(name):
        raise HTTPException(400, "That isn't a model name.")
    try:
        await ollama.delete(name)
    except ollama.OllamaError as e:
        raise HTTPException(400, str(e))
    if settings.get("keep_model") and schedule.norm_model(settings.get("keep_model")) == schedule.norm_model(name):
        settings.update({"keep_model": ""}, admin)
    await ollama.MODELS.refresh()
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

def connection(request: Request | None = None) -> dict:
    """The address to paste into the other apps: this machine's name or address as the browser reached the page,
    with the gateway's published port."""
    host = (request.url.hostname if request is not None else None) or config.own_hostname()
    if ":" in host and not host.startswith("["):
        host = f"[{host}]"                      # an IPv6 address
    port = config.PUBLIC_GATEWAY_PORT
    return {"host": host, "address": f"http://{host}:{port}", "openaiAddress": f"http://{host}:{port}/v1",
            "prefixKnown": False, "standalone": True}


@router.get("/callers")
def get_callers(request: Request, admin: dict = Depends(require_admin)):
    first = set(settings.get("answer_first") or [])
    key_labels = keys.labels()
    rows = []
    for r in usage.callers():
        c = r["caller"]
        name = key_labels.get(c) or callers.label(c)
        rows.append({**r, "name": name, "answerFirst": c in first, "advice": callers.ADVICE.get(c)})
    return {"connection": connection(request), "callers": rows, "requireKey": settings.get("require_key"),
            "known": {a: s for a, s in callers.RESOLVER.by_address.items()},
            "advice": callers.ADVICE, "apps": callers.APPS}


class FirstIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    on: StrictBool


@router.put("/callers/{caller}/answer-first")
def answer_first(caller: str, body: FirstIn, admin: dict = Depends(require_admin)):
    if not settings.CALLER_NAME.match(caller) and not caller.startswith("key_"):
        raise HTTPException(400, "That isn't a caller.")
    cur = list(settings.get("answer_first") or [])
    if body.on and caller not in cur:
        cur.append(caller)
    if not body.on:
        cur = [c for c in cur if c != caller]
    try:
        settings.update({"answer_first": cur}, admin)
    except settings.SettingsError as e:
        raise HTTPException(422, str(e))
    return {"answerFirst": settings.get("answer_first")}


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


@router.post("/keys")
def new_key(body: KeyIn, admin: dict = Depends(require_admin)):
    for m in body.models or []:
        if not settings.MODEL_NAME.match(m):
            raise HTTPException(400, f"{m!r} isn't a model name.")
    try:
        kid, key = keys.create(body.label, body.models, admin["id"])
    except ValueError as e:
        raise HTTPException(400, str(e))
    return {"id": kid, "key": key}


@router.delete("/keys/{kid}")
def revoke_key(kid: str, admin: dict = Depends(require_admin)):
    if not keys.revoke(kid):
        raise HTTPException(404, "No such key (or already revoked).")
    return {"status": "ok"}


# --------------------------------------------------------------------------------------------- settings

@router.get("/admin/settings")
def get_settings(admin: dict = Depends(require_admin)):
    return settings.payload()


@router.put("/admin/settings")
async def put_settings(body: dict = Body(...), admin: dict = Depends(require_admin)):
    try:
        changed = settings.update(body, admin)
    except settings.SettingsError as e:
        raise HTTPException(422, str(e))
    if settings.SERVER_KEYS & set(changed) and server.SERVER.running():
        asyncio.create_task(server.SERVER.restart())
    return settings.payload()


# --------------------------------------------------------------------------------------------- backups

@router.get("/admin-storage-download-db")
def download_db(admin: dict = Depends(require_admin)):
    """The database: settings, key hashes and usage. The models are not in it (download them again)."""
    tmp_path = db.backup_to_tempfile()
    return backup_core.send_file(tmp_path, backup_core.file_name("household-ai-backup", ".db", now=config.now()))


@router.post("/admin-storage-import-db")
async def import_db(file: UploadFile = File(...), admin: dict = Depends(require_admin)):
    tmp_path = await backup_core.receive(file, config.DATA_DIR)
    try:
        try:
            db.validate_backup_file(tmp_path)
        except ValueError as e:
            raise HTTPException(400, str(e))
        db.import_from_tempfile(tmp_path)
        settings.invalidate()
    finally:
        if os.path.exists(tmp_path):
            os.remove(tmp_path)
    return {"status": "ok"}
