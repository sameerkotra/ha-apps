"""The page's API (HOUSEHOLD_ASSISTANT_SPEC.md §9). Every route needs Home Assistant's identity headers; every
question, answer and action is the signed-in person's own — admins can't see or ask "as" anyone else.
"""
import asyncio
import json
import os

from fastapi import APIRouter, Body, Depends, File, HTTPException, Query, Request, UploadFile
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, ConfigDict, Field, StrictBool, ValidationError
from starlette.concurrency import run_in_threadpool

from .. import ai_client, app_messages, auth, briefing, catalogue, config, db, engine, settings
from ..auth import get_current_user, require_admin
from ..common import auth_core, backup_core
from ..common import whoami as whoami_core

router = APIRouter(prefix="/api")
PING_SECONDS = 20                     # a comment line on a quiet stream, so proxies keep it open

_AI_KEYS = {"ai_provider", "ai_url", "ai_model", "ai_api_key", "ai_max_tokens", "ai_tool_calls"}
DEFAULT_SUGGESTIONS = ["What's on my list today?", "Spending this month", "What's on the shopping list?",
                       "Find a document"]


# --------------------------------------------------------------------------------------------- the person

@router.get("/me")
def me(user: dict = Depends(get_current_user)):
    with db.get_conn() as conn:
        tools = catalogue.for_user(conn, user)
        names = catalogue.app_names(conn)
        apps = conn.execute("SELECT app, enabled, app_on FROM assist_apps ORDER BY app").fetchall()
    why = engine.can_ask(user)
    st = ai_client.status()
    return {
        "user": {"id": user["id"], "name": user["name"], "isAdmin": user["is_admin"]},
        "canAsk": why is None, "why": why, "noAdmin": auth.no_admins(),
        "suggestions": catalogue.suggestions(tools) or DEFAULT_SUGGESTIONS,
        "apps": [{"slug": a["app"], "name": names.get(a["app"], a["app"]), "on": bool(a["enabled"] and a["app_on"])}
                 for a in apps],
        "privacy": st["privacy"], "sharedOpen": bool(settings.get("shared_open")),
        "recorderWarning": user["is_admin"] and not settings.get("recorder_excluded"),
        "version": config.APP_VERSION,
    }


@router.get("/whoami")
def whoami(request: Request, user: dict = Depends(get_current_user)):
    """"How the app sees you": what Home Assistant sent and whether it matched admin_users (never the list)."""
    return whoami_core.build(
        request, user_id=user["id"], username=user["username"], display_name=user["name"],
        is_admin=user["is_admin"], admin_entries=len(config.ADMIN_USERS),
        display_name_only=(not user["is_admin"]) and auth_core.display_name_listed(user["name"], config.ADMIN_USERS))


@router.get("/tools")
def tools(user: dict = Depends(get_current_user)):
    """What this person may ask about, in words (the suggestions and "What can I ask?")."""
    with db.get_conn() as conn:
        t = catalogue.for_user(conn, user)
    return {"tools": [{"app": v["app"], "appName": v["appName"], "name": k, "what": v["spec"]["what"],
                       "acts": v["spec"]["acts"], "examples": v["spec"].get("examples", [])} for k, v in t.items()]}


# --------------------------------------------------------------------------------------------- asking

class AskIn(BaseModel):
    model_config = ConfigDict(extra="ignore")
    text: str = Field(min_length=1, max_length=engine.MAX_QUESTION)


@router.post("/ask")
def ask(body: AskIn, user: dict = Depends(get_current_user)):
    try:
        return {"id": engine.ask(user, body.text)}
    except engine.LimitError as e:
        raise HTTPException(429, str(e))
    except PermissionError as e:
        raise HTTPException(403, str(e))
    except ValueError as e:
        raise HTTPException(422, str(e))


def _own(conn, qid: str, user: dict):
    q = conn.execute("SELECT * FROM questions WHERE id = ? AND user_id = ?", (qid, user["id"])).fetchone()
    if q is None:
        raise HTTPException(404, "No such question.")
    return q


@router.get("/ask/{qid}")
def get_question(qid: str, user: dict = Depends(get_current_user)):
    with db.get_conn() as conn:
        return engine.view(conn, _own(conn, qid, user))


@router.get("/ask/{qid}/events")
async def question_events(qid: str, request: Request, user: dict = Depends(get_current_user)):
    """The question as GET /api/ask/{qid} gives it, sent again each time it changes (Server-Sent Events), until it
    is answered, failed or stopped. The page falls back to polling when the stream can't be opened."""
    def load() -> dict:
        with db.get_conn() as conn:
            return engine.view(conn, _own(conn, qid, user))

    first = await run_in_threadpool(load)

    async def gen():
        yield "retry: 3000\n\n"
        q, last, seen, quiet = first, None, 0, 0.0
        while True:
            body = json.dumps(q, separators=(",", ":"))
            if body != last:
                yield f"event: question\ndata: {body}\n\n"
                last, quiet = body, 0.0
            if q["state"] not in engine.RUNNING:
                return
            if quiet >= PING_SECONDS:
                if await request.is_disconnected():
                    return
                yield ": ping\n\n"
                quiet = 0.0
            seen = await run_in_threadpool(engine.wait_change, qid, seen, 1.0)   # a change, or a second
            quiet += 1.0
            q = await run_in_threadpool(load)

    return StreamingResponse(gen(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache, no-transform", "X-Accel-Buffering": "no"})


@router.post("/ask/{qid}/stop")
def stop_question(qid: str, user: dict = Depends(get_current_user)):
    with db.get_conn() as conn:
        _own(conn, qid, user)
    return {"stopped": engine.stop(qid, user["id"])}


@router.post("/ask/{qid}/act/{cid}")
def act(qid: str, cid: str, user: dict = Depends(get_current_user)):
    """The tap on a proposed action: sends it with confirm, as the person; the app checks it again."""
    try:
        return {"result": engine.act(qid, cid, user)}
    except LookupError as e:
        raise HTTPException(404, str(e))
    except PermissionError as e:
        raise HTTPException(403, str(e))
    except ValueError as e:
        raise HTTPException(409, str(e))


@router.get("/history")
def history(before: str | None = Query(None, max_length=40), limit: int = Query(20, ge=1, le=50),
            user: dict = Depends(get_current_user)):
    """The person's questions, newest first (`before`: an asked_at from the last page)."""
    with db.get_conn() as conn:
        rows = conn.execute("SELECT * FROM questions WHERE user_id = ? AND asked_at < ? ORDER BY asked_at DESC, id DESC "
                            "LIMIT ?", (user["id"], before or "9999", limit + 1)).fetchall()
        items = [engine.view(conn, q) for q in rows[:limit]]
    return {"questions": items, "more": len(rows) > limit}


@router.delete("/history")
def clear_history(user: dict = Depends(get_current_user)):
    with db.get_conn() as conn:
        n = conn.execute(f"DELETE FROM questions WHERE user_id = ? AND state NOT IN "
                         f"({','.join('?' * len(engine.RUNNING))})", (user["id"], *engine.RUNNING)).rowcount
    return {"deleted": n}


# --------------------------------------------------------------------------------------------- the briefings

def _briefing_view(conn, user: dict) -> dict:
    sp = briefing.speakers()
    return {**briefing.get(conn, user["id"]), "phones": briefing.phones(user["id"]),
            "parts": briefing.parts_for(conn, user), "eveningParts": briefing.parts_for(conn, user, "evening"),
            "speakers": sp["speakers"], "tts": sp["tts"], "why": briefing.may_have(user)}


@router.get("/briefing")
def get_briefing(user: dict = Depends(get_current_user)):
    """The person's briefings: morning and evening on or off with their times, days, the speaker, their phones,
    the speakers Home Assistant has, and what each briefing would include."""
    with db.get_conn() as conn:
        return _briefing_view(conn, user)


class BriefingIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    on: StrictBool
    time: str = Field(max_length=5)
    days: str = Field(max_length=10)
    eveningOn: StrictBool | None = None
    eveningTime: str | None = Field(None, max_length=5)
    speaker: str | None = Field(None, max_length=120)        # "" = none


@router.put("/briefing")
def put_briefing(body: BriefingIn, user: dict = Depends(get_current_user)):
    if (body.on or body.eveningOn) and briefing.may_have(user):
        raise HTTPException(403, briefing.may_have(user))
    with db.get_conn() as conn:
        try:
            briefing.save(conn, user["id"], body.on, body.time, body.days, evening_on=body.eveningOn,
                          evening_time=body.eveningTime,
                          speaker=False if "speaker" not in body.model_fields_set else (body.speaker or None))
        except ValueError as e:
            raise HTTPException(422, str(e))
        return _briefing_view(conn, user)


@router.post("/briefing/send")
async def send_briefing(kind: str = Query("morning", pattern="^(morning|evening)$"),
                        user: dict = Depends(get_current_user)):
    """"Send me one now": the briefing made and sent at once (it doesn't count as the day's own)."""
    why = briefing.may_have(user)
    if why:
        raise HTTPException(403, why)
    out = await run_in_threadpool(briefing.send, user, kind)
    with db.get_conn() as conn:
        q = conn.execute("SELECT * FROM questions WHERE id = ?", (out["question"],)).fetchone()
        view = engine.view(conn, q)
    return {"question": view, "phones": out["phones"], "sent": out["sent"], "spoken": out["spoken"]}


# --------------------------------------------------------------------------------------------- admin

@router.get("/admin/settings")
def get_settings(admin: dict = Depends(require_admin)):
    return settings.payload()


@router.put("/admin/settings")
def put_settings(body: dict = Body(...), admin: dict = Depends(require_admin)):
    try:
        changed = settings.update(body, admin)
    except settings.SettingsError as e:
        raise HTTPException(422, str(e))
    if _AI_KEYS & set(changed):
        ai_client.forget_state()
    return settings.payload()


class AIProbe(BaseModel):
    model_config = ConfigDict(extra="ignore")
    ai_provider: str | None = None
    ai_url: str | None = None
    ai_model: str | None = None
    ai_api_key: str | None = None
    clear_ai_api_key: bool = False


@router.post("/admin/settings/test-ai")
async def test_ai(payload: AIProbe | None = None, admin: dict = Depends(require_admin)):
    """Test connection: which models the provider offers, with the values typed on the page (unsaved)."""
    p = payload or AIProbe()
    current = settings.all()
    probe = {k: v for k, v in p.model_dump().items() if k != "clear_ai_api_key" and v is not None}
    if p.clear_ai_api_key:
        probe["ai_api_key"] = ""
    elif not (probe.get("ai_api_key") or "").strip():
        probe.pop("ai_api_key", None)
    try:
        v = settings.AppSettings(**{**current, **probe}).model_dump()
    except ValidationError as e:
        raise HTTPException(422, settings.readable(e))
    cfg = ai_client.Config(provider=v["ai_provider"], url=settings.ai_url(v["ai_provider"], v["ai_url"]),
                           model=v["ai_model"], api_key=v["ai_api_key"], max_tokens=v["ai_max_tokens"])
    return await asyncio.to_thread(ai_client.test_connection, cfg)


@router.get("/admin/tools")
def admin_tools(admin: dict = Depends(require_admin)):
    with db.get_conn() as conn:
        return {"apps": catalogue.admin_rows(conn)}


@router.post("/admin/tools/refresh")
def admin_tools_refresh(admin: dict = Depends(require_admin)):
    return {"asked": catalogue.refresh()}


class AppSwitch(BaseModel):
    enabled: StrictBool


@router.put("/admin/tools/{app}")
def admin_tools_switch(app: str, body: AppSwitch, admin: dict = Depends(require_admin)):
    with db.get_conn() as conn:
        if not catalogue.set_enabled(conn, app, body.enabled):
            raise HTTPException(404, "No such app.")
        return {"apps": catalogue.admin_rows(conn)}


@router.get("/admin/usage")
def admin_usage(admin: dict = Depends(require_admin)):
    """30 days of questions, tool calls and tokens (counts only: never who asked what)."""
    with db.get_conn() as conn:
        rows = conn.execute("SELECT day, questions, calls, input_tokens, output_tokens FROM usage_days "
                            "ORDER BY day DESC LIMIT 30").fetchall()
    return {"days": [dict(r) for r in rows], "ai": ai_client.status()}


@router.get("/admin/people")
def admin_people(admin: dict = Depends(require_admin)):
    with db.get_conn() as conn:
        rows = conn.execute("SELECT id, name, enabled, is_child FROM users ORDER BY name COLLATE NOCASE").fetchall()
    return {"people": [{"id": r["id"], "name": r["name"], "enabled": bool(r["enabled"]), "isChild": bool(r["is_child"])}
                       for r in rows]}


class PersonIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    enabled: StrictBool | None = None
    isChild: StrictBool | None = None


@router.put("/admin/people/{user_id}")
def admin_person(user_id: str, body: PersonIn, admin: dict = Depends(require_admin)):
    sets = {}
    if body.enabled is not None:
        if user_id == admin["id"] and not body.enabled:
            raise HTTPException(422, "You can't turn yourself off.")
        sets["enabled"] = int(body.enabled)
    if body.isChild is not None:
        sets["is_child"] = int(body.isChild)
    with db.get_conn() as conn:
        if sets and not conn.execute(f"UPDATE users SET {', '.join(f'{k} = ?' for k in sets)} WHERE id = ?",
                                     (*sets.values(), user_id)).rowcount:
            raise HTTPException(404, "No such person.")
    return admin_people(admin)


@router.get("/admin/connected-apps")
def connected_apps(admin: dict = Depends(require_admin)):
    return app_messages.connected_apps()


@router.get("/admin-storage-download-db")
def download_db(admin: dict = Depends(require_admin)):
    """A full copy of the database (everyone's questions included); the AI access key is blanked in it."""
    tmp_path = db.backup_to_tempfile(after=settings.REGISTRY.scrub_secrets)
    return backup_core.send_file(tmp_path, backup_core.file_name("household-assistant-backup", ".db",
                                                                 now=config.now()))


@router.post("/admin-storage-import-db")
async def import_db(file: UploadFile = File(...), admin: dict = Depends(require_admin)):
    """Replaces the whole database with a downloaded backup (validated first); this install keeps its AI key."""
    saved = settings.REGISTRY.saved_secrets()
    tmp_path = await backup_core.receive(file, config.DATA_DIR)
    try:
        try:
            db.validate_backup_file(tmp_path)
        except ValueError as e:
            raise HTTPException(400, str(e))
        db.import_from_tempfile(tmp_path)
        settings.REGISTRY.keep_secrets(saved)
        ai_client.forget_state()
    finally:
        if os.path.exists(tmp_path):
            os.remove(tmp_path)
    return {"status": "ok"}
