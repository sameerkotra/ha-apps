"""The page's API (HOUSEHOLD_ASSISTANT_SPEC.md §9). Every route needs Home Assistant's identity headers; every
question, answer and action is the signed-in person's own — admins can't see or ask "as" anyone else.
"""
import asyncio
import json
import os
import threading

from fastapi import APIRouter, Body, Depends, File, HTTPException, Query, Request, UploadFile
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, ConfigDict, Field, StrictBool, ValidationError
from starlette.concurrency import run_in_threadpool

from .. import ai_client, app_messages, auth, briefing, catalogue, config, db, engine, settings
from ..quick import intents as quick_intents
from ..quick import learn as quick_learn
from ..quick import match as quick_match
from ..quick import understand as quick_understand
from ..auth import get_current_user, require_admin
from ..common import auth_core, backup_core
from ..common import whoami as whoami_core

router = APIRouter(prefix="/api")
PING_SECONDS = 20                     # a comment line on a quiet stream, so proxies keep it open
MAX_STREAMS_PER_PERSON = 6            # live question streams one person may have open at once (tabs, retries)

_AI_KEYS = {"ai_on", "ai_provider", "ai_url", "ai_model", "ai_api_key", "ai_max_tokens", "ai_tool_calls"}
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
    quick = settings.quick_on()
    suggestions = [x["text"] for x in engine.suggestions(user, tools=tools)[:4]] if quick else []
    return {
        "user": {"id": user["id"], "name": user["name"], "isAdmin": user["is_admin"]},
        "canAsk": why is None, "why": why, "noAdmin": auth.no_admins(),
        "suggestions": suggestions or catalogue.suggestions(tools) or DEFAULT_SUGGESTIONS,
        "ai": {"on": settings.ai_in_use(), "model": settings.get("ai_model") if settings.ai_in_use() else None},
        "quick": quick, "quickOnly": quick and not settings.ai_in_use(),
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


_streams: dict[str, int] = {}
_streams_lock = threading.Lock()


def _open_stream(user_id: str) -> bool:
    with _streams_lock:
        if _streams.get(user_id, 0) >= MAX_STREAMS_PER_PERSON:
            return False
        _streams[user_id] = _streams.get(user_id, 0) + 1
        return True


def _close_stream(user_id: str) -> None:
    with _streams_lock:
        n = _streams.get(user_id, 0) - 1
        if n > 0:
            _streams[user_id] = n
        else:
            _streams.pop(user_id, None)


@router.get("/ask/{qid}/events")
async def question_events(qid: str, request: Request, user: dict = Depends(get_current_user)):
    """The question as GET /api/ask/{qid} gives it, sent again each time it changes (Server-Sent Events), until it
    is answered, failed or stopped. The page falls back to polling when the stream can't be opened.

    The stream sleeps until the engine says the question changed (engine.touch sets an asyncio.Event on this loop)
    and reads it again only then — or after PING_SECONDS of quiet, in case something changed it without saying so.
    At most MAX_STREAMS_PER_PERSON at once per person (429 beyond)."""
    def load() -> dict:
        with db.get_conn() as conn:
            return engine.view(conn, _own(conn, qid, user))

    loop = asyncio.get_running_loop()
    changed = asyncio.Event()
    engine.watch(qid, loop, changed)
    seen = engine.version(qid)                 # read before each load: a change during the load isn't missed
    try:
        first = await run_in_threadpool(load)
    except BaseException:
        engine.unwatch(qid, loop, changed)
        raise
    if not _open_stream(user["id"]):
        engine.unwatch(qid, loop, changed)
        raise HTTPException(429, "Too many live answers open at once — close a tab or two.")

    async def gen():
        try:
            yield "retry: 3000\n\n"
            q, last, version = first, None, seen
            while True:
                body = json.dumps(q, separators=(",", ":"))
                if body != last:
                    yield f"event: question\ndata: {body}\n\n"
                    last = body
                if q["state"] not in engine.RUNNING:
                    return
                changed.clear()
                if engine.version(qid) == version:     # nothing since the last load: sleep until something
                    try:
                        await asyncio.wait_for(changed.wait(), PING_SECONDS)
                    except asyncio.TimeoutError:
                        if await request.is_disconnected():
                            return
                        yield ": ping\n\n"
                version = engine.version(qid)
                q = await run_in_threadpool(load)
        finally:
            engine.unwatch(qid, loop, changed)
            _close_stream(user["id"])

    return StreamingResponse(gen(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache, no-transform", "X-Accel-Buffering": "no"})


@router.post("/ask/{qid}/stop")
def stop_question(qid: str, user: dict = Depends(get_current_user)):
    with db.get_conn() as conn:
        _own(conn, qid, user)
    return {"stopped": engine.stop(qid, user["id"])}


class ChooseIn(BaseModel):
    model_config = ConfigDict(extra="ignore")
    choice: int = Field(ge=0, le=10)


@router.post("/ask/{qid}/choose")
def choose(qid: str, body: ChooseIn, user: dict = Depends(get_current_user)):
    """A *Did you mean* choice: the same question answered with it, and that way of asking learned (§17.8)."""
    try:
        return engine.choose(qid, body.choice, user)
    except LookupError as e:
        raise HTTPException(404, str(e))
    except engine.LimitError as e:
        raise HTTPException(429, str(e))
    except PermissionError as e:
        raise HTTPException(403, str(e))
    except ValueError as e:
        raise HTTPException(409, str(e))


@router.post("/ask/{qid}/not-this")
def not_this(qid: str, user: dict = Depends(get_current_user)):
    """*Not what I asked*: forgets a learned phrase and asks again — the model when AI is in use, else the choices."""
    try:
        return {"id": engine.not_this(qid, user)}
    except LookupError as e:
        raise HTTPException(404, str(e))
    except engine.LimitError as e:
        raise HTTPException(429, str(e))
    except PermissionError as e:
        raise HTTPException(403, str(e))
    except ValueError as e:
        raise HTTPException(409, str(e))


@router.get("/quick/suggestions")
def quick_suggestions(user: dict = Depends(get_current_user)):
    """The one-tap questions: the quick intents the person may use, most used first."""
    return {"suggestions": engine.suggestions(user) if settings.quick_on() else []}


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
        items = engine.views(conn, rows[:limit])
    return {"questions": items, "more": len(rows) > limit}


@router.delete("/history")
def clear_history(user: dict = Depends(get_current_user)):
    """Deletes the person's questions (not a running one). The hourly limits are counted apart (rate_events), so
    clearing doesn't reset them."""
    with db.get_conn() as conn:
        n = conn.execute(f"DELETE FROM questions WHERE user_id = ? AND state NOT IN "
                         f"({','.join('?' * len(engine.RUNNING))})", (user["id"], *engine.RUNNING)).rowcount
    return {"deleted": n}


# --------------------------------------------------------------------------------------------- the briefings

def _briefing_view(user: dict) -> dict:
    """Home Assistant is asked for its speakers first, outside any database transaction."""
    sp = briefing.speakers()
    with db.get_conn() as conn:
        mine = briefing.get(conn, user["id"])
        parts, evening = briefing.parts_for(conn, user), briefing.parts_for(conn, user, "evening")
    return {**mine, "phones": briefing.phones(user["id"]), "parts": parts, "eveningParts": evening,
            "speakers": sp["speakers"], "tts": sp["tts"], "why": briefing.may_have(user),
            "quiet": {"from": settings.get("quiet_from"), "until": settings.get("quiet_until")}}


@router.get("/briefing")
def get_briefing(user: dict = Depends(get_current_user)):
    """The person's briefings: morning and evening on or off with their times, days, the speaker, their phones,
    the speakers Home Assistant has (those the admin allows), and what each briefing would include."""
    return _briefing_view(user)


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
    speaker = False if "speaker" not in body.model_fields_set else (body.speaker or None)
    with db.get_conn() as conn:
        old = briefing.get(conn, user["id"])["speaker"]
    try:
        if speaker not in (False, None, old):
            briefing.check_speaker(speaker)                       # asks Home Assistant: before the transaction
        with db.get_conn() as conn:
            briefing.save(conn, user["id"], body.on, body.time, body.days, evening_on=body.eveningOn,
                          evening_time=body.eveningTime, speaker=speaker, speaker_checked=True)
    except ValueError as e:
        raise HTTPException(422, str(e))
    return _briefing_view(user)


@router.post("/briefing/send")
async def send_briefing(kind: str = Query("morning", pattern="^(morning|evening)$"),
                        user: dict = Depends(get_current_user)):
    """"Send me one now": the briefing made and sent at once (it doesn't count as the day's own)."""
    why = briefing.may_have(user)
    if why:
        raise HTTPException(403, why)
    if not await run_in_threadpool(briefing.reserve, user["id"]):        # counted first: two at once can't both pass
        raise HTTPException(429, "That's the most briefings for one hour — try again a little later.")
    out = await run_in_threadpool(briefing.send, user, kind)

    def read():
        with db.get_conn() as conn:
            q = conn.execute("SELECT * FROM questions WHERE id = ?", (out["question"],)).fetchone()
            return engine.view(conn, q)
    view = await run_in_threadpool(read)
    return {"question": view, "phones": out["phones"], "sent": out["sent"], "spoken": out["spoken"],
            "quiet": out.get("quiet", False)}


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
    current = await asyncio.to_thread(settings.all)
    if not current["ai_on"]:                          # Use AI off: no request to any model server (§17.10)
        return {"provider": current["ai_provider"], "label": settings.PROVIDERS.get(current["ai_provider"], "AI"),
                "ok": False, "message": "Use AI is off — turn it on (and save) to test the connection."}
    probe = {k: v for k, v in p.model_dump().items() if k != "clear_ai_api_key" and v is not None}
    if p.clear_ai_api_key:
        probe["ai_api_key"] = ""
    elif not (probe.get("ai_api_key") or "").strip():
        probe.pop("ai_api_key", None)
        if current["ai_api_key"] and settings.key_target_changed(current, probe):
            # the saved key goes only where it was saved for: another provider or address needs it typed again
            provider = probe.get("ai_provider", current["ai_provider"])
            if provider in ("openai", "anthropic"):
                return {"provider": provider, "label": settings.PROVIDERS.get(provider, "AI"), "ok": False,
                        "message": "Type the access key again to test another provider or address: the saved key "
                                   "is only sent where it was saved for."}
            probe["ai_api_key"] = ""
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


# --------------------------------------------------------------------------------------------- admin: quick answers

class QuickTestIn(BaseModel):
    model_config = ConfigDict(extra="ignore")
    text: str = Field(min_length=1, max_length=engine.MAX_QUESTION)


@router.post("/admin/quick/test")
def admin_quick_test(body: QuickTestIn, admin: dict = Depends(require_admin)):
    """How a question would be understood (as the admin, with the admin's names): calls nothing (§17.11)."""
    with db.get_conn() as conn:
        tools = catalogue.for_user(conn, admin)
        ctx = engine.quick_ctx(conn, admin, tools)
        out = quick_understand.explain(body.text, ctx)
        tpl = out["template"]
        hit = quick_learn.lookup(conn, tpl, ai_now=bool(settings.get("quick_ai_now")))
    out["learned"] = hit
    return out


@router.get("/admin/quick/intents")
def admin_quick_intents(admin: dict = Depends(require_admin)):
    """What a phrase may mean (Add a phrase): every quick intent, with whether its app answers now."""
    with db.get_conn() as conn:
        tools = catalogue.for_user(conn, admin)
    ctx = quick_intents.Ctx(tools=tools, today=config.today())
    return {"intents": [{"intent": i.key, "question": i.question, "tool": i.tool, "acts": i.acts,
                         "available": i.available(ctx),
                         "args": sorted((tools.get(i.tool) or {}).get("spec", {}).get("args", {}).keys())}
                        for i in quick_intents.INTENTS]}


@router.get("/admin/quick/phrases")
def admin_quick_phrases(admin: dict = Depends(require_admin)):
    with db.get_conn() as conn:
        return {"phrases": quick_learn.phrases(conn)}


class PhraseIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    words: str = Field(min_length=1, max_length=200)
    intent: str = Field(min_length=1, max_length=40)
    args: dict = Field(default_factory=dict)


@router.post("/admin/quick/phrases")
def admin_quick_add_phrase(body: PhraseIn, admin: dict = Depends(require_admin)):
    """Add a phrase: these words (their template) mean this intent, with these fixed arguments."""
    intent = quick_intents.BY_KEY.get(body.intent)
    if intent is None:
        raise HTTPException(422, "No such quick answer.")
    if intent.acts:
        raise HTTPException(422, "A phrase can only lead to an answer, not to a change.")
    with db.get_conn() as conn:
        tools = catalogue.for_user(conn, admin)
        spec = (tools.get(intent.tool) or {}).get("spec") or {"args": {}}
        args = engine._clean_args(spec, body.args)
        if len(args) != len({k: v for k, v in body.args.items() if v not in (None, "")}):
            raise HTTPException(422, "One of the fixed values doesn't fit that answer.")
        ctx = engine.quick_ctx(conn, admin, tools)
        u = quick_match.read(body.words, ctx.today, ctx.vocab)
        tpl = quick_match.template(u)
        if not tpl or tpl in ("{when}", "{name}", "{month}"):
            raise HTTPException(422, "Use a few words, not only a time or a name.")
        slot_args = [k for k in ("when", "days", "month", "date") if k not in args]
        pid = quick_learn.remember(conn, tpl, intent.key, args, slot_args, "admin")
        return {"id": pid, "phrase": tpl, "phrases": quick_learn.phrases(conn)}


@router.delete("/admin/quick/phrases/{pid}")
def admin_quick_delete_phrase(pid: str, admin: dict = Depends(require_admin)):
    with db.get_conn() as conn:
        if not quick_learn.delete(conn, pid):
            raise HTTPException(404, "No such phrase.")
        return {"phrases": quick_learn.phrases(conn)}


@router.get("/admin/quick/misses")
def admin_quick_misses(admin: dict = Depends(require_admin)):
    """Asked but not answered quickly: normalised words and how often, never who asked."""
    with db.get_conn() as conn:
        return {"misses": quick_learn.misses(conn)}


@router.delete("/admin/quick/misses")
def admin_quick_clear_misses(admin: dict = Depends(require_admin)):
    with db.get_conn() as conn:
        n = conn.execute("DELETE FROM quick_misses").rowcount
    return {"deleted": n}


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
        rows = conn.execute("SELECT id, name, enabled, is_child, reviewed, created_at FROM users "
                            "ORDER BY reviewed, name COLLATE NOCASE").fetchall()
    return {"people": [{"id": r["id"], "name": r["name"], "enabled": bool(r["enabled"]), "isChild": bool(r["is_child"]),
                        "new": not r["reviewed"], "since": r["created_at"]} for r in rows]}


class PersonIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    enabled: StrictBool | None = None
    isChild: StrictBool | None = None
    reviewed: StrictBool | None = None          # "Looks right": no longer shown as new


@router.put("/admin/people/{user_id}")
def admin_person(user_id: str, body: PersonIn, admin: dict = Depends(require_admin)):
    sets = {}
    if body.enabled is not None:
        if user_id == admin["id"] and not body.enabled:
            raise HTTPException(422, "You can't turn yourself off.")
        sets["enabled"] = int(body.enabled)
    if body.isChild is not None:
        sets["is_child"] = int(body.isChild)
    if sets or body.reviewed:
        sets["reviewed"] = 1                    # an admin has looked at them
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
    saved = await run_in_threadpool(settings.REGISTRY.saved_secrets)
    tmp_path = await backup_core.receive(file, config.DATA_DIR)

    def swap():
        db.validate_backup_file(tmp_path)
        db.import_from_tempfile(tmp_path)
        settings.REGISTRY.keep_secrets(saved)
        ai_client.forget_state()
    try:
        await run_in_threadpool(swap)
    except ValueError as e:
        raise HTTPException(400, str(e))
    finally:
        if os.path.exists(tmp_path):
            os.remove(tmp_path)
    return {"status": "ok"}
