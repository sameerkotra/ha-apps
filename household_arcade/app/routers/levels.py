"""Level lists (SPEC §11): the levels a game plays, and Admin → Levels.

    GET  /api/levels?game=&mode=                 the playable levels (anyone signed in)
    GET  /api/admin/levels                       a card per list: levels, who got furthest, the last build, today's use
    GET  /api/admin/levels/{id}                  one level
    POST /api/admin/levels/build {game, count}   build more levels now
    GET  /api/admin/levels/builds/{id}           a build's progress
    POST /api/admin/levels/{id}/retire|restore|approve
    POST /api/admin/levels/{id}/move {to}         reorder
    DELETE /api/admin/levels/{id}                 delete a level the AI made
    POST /api/admin/ai/test                      Test connection (App settings)
    GET  /api/admin/ai/usage?days=30             AI usage: totals, by day / game / model, latest requests
"""
from fastapi import APIRouter, Body, Depends, HTTPException, Query

import time

from .. import ai_client, ai_usage, db, games, level_builder, levels, settings
from ..auth import get_current_user, require_admin

router = APIRouter(prefix="/api", tags=["levels"])


@router.get("/levels")
def playable(game: str = Query(...), mode: str | None = Query(default=None), current: dict = Depends(get_current_user)):
    if not games.exists(game) or not settings.game_enabled(game):
        raise HTTPException(404, "Unknown game.")
    set_id = levels.set_for(game, mode)
    if not set_id:
        raise HTTPException(404, "That game and mode don't play through levels.")
    with db.get_conn() as conn:
        return {"game": game, "levels": levels.playable(conn, set_id)}


def _card(conn, set_id: str) -> dict:
    items = levels.all_levels(conn, set_id)
    count = lambda **kw: sum(1 for x in items if all(x[k] == v for k, v in kw.items()))  # noqa: E731
    return {
        "id": set_id, "label": levels.SETS[set_id]["label"], "game": set_id, "modes": levels.SETS[set_id]["modes"],
        "levels": items,
        "counts": {"builtin": count(source="builtin"), "ai": count(source="ai"), "ready": count(status="ready"),
                   "waiting": count(status="waiting"), "retired": count(status="retired")},
        "reached": levels.reached(conn, set_id),
        "lastBuild": level_builder.latest(conn, set_id),
        "building": level_builder._open_build(conn, set_id) is not None,
    }


@router.get("/admin/levels")
def admin_levels(admin: dict = Depends(require_admin)):
    v = settings.all()
    with db.get_conn() as conn:
        return {
            "lists": [_card(conn, s) for s in levels.SETS],
            "ai": {"problem": settings.ai_problem(), "enabled": v["ai_levels_enabled"], "auto": v["ai_levels_auto"],
                   "review": v["ai_levels_review"], "batch": v["ai_levels_batch"], "ahead": v["ai_levels_ahead"],
                   "provider": settings.PROVIDER_LABELS.get(v["ai_provider"], v["ai_provider"]), "model": v["ai_model"],
                   "madeToday": level_builder.made_today(conn), "dailyLimit": v["ai_levels_daily_limit"],
                   "leftToday": level_builder.left_today(conn)},
        }


@router.get("/admin/levels/builds/{build_id}")
def build_status(build_id: str, admin: dict = Depends(require_admin)):
    with db.get_conn() as conn:
        b = level_builder.get(conn, build_id)
    if not b:
        raise HTTPException(404, "That build isn't known.")
    return b


@router.get("/admin/levels/{level_id}")
def one_level(level_id: str, admin: dict = Depends(require_admin)):
    with db.get_conn() as conn:
        lv = levels.get(conn, level_id)
    if not lv:
        raise HTTPException(404, "That level isn't known.")
    return lv


@router.post("/admin/levels/build", status_code=202)
def build(body: dict = Body(...), admin: dict = Depends(require_admin)):
    if not isinstance(body, dict):
        raise HTTPException(422, "Send {game, count}.")
    set_id, count = body.get("game"), body.get("count", settings.get("ai_levels_batch"))
    if not isinstance(set_id, str) or set_id not in levels.SETS:
        raise HTTPException(404, "That game has no level list.")
    if isinstance(count, bool) or not isinstance(count, int) or not 1 <= count <= 20:
        raise HTTPException(422, "Build 1 to 20 levels at a time.")
    try:
        return level_builder.request_build(set_id, count, admin.get("name") or admin.get("username") or "an admin")
    except level_builder.BuildError as e:
        raise HTTPException(e.status, str(e))


def _status(level_id: str, status: str, admin: dict) -> dict:
    with db.get_conn() as conn:
        try:
            return levels.set_status(conn, level_id, status, admin)
        except levels.LevelError as e:
            raise HTTPException(404 if "isn't known" in str(e) else 409, str(e))


@router.post("/admin/levels/{level_id}/retire")
def retire(level_id: str, admin: dict = Depends(require_admin)):
    """Skip a level from the next game on; scores on it stay."""
    return _status(level_id, "retired", admin)


@router.post("/admin/levels/{level_id}/restore")
def restore(level_id: str, admin: dict = Depends(require_admin)):
    return _status(level_id, "ready", admin)


@router.post("/admin/levels/{level_id}/approve")
def approve(level_id: str, admin: dict = Depends(require_admin)):
    return _status(level_id, "ready", admin)


@router.post("/admin/levels/{level_id}/move")
def move_level(level_id: str, body: dict = Body(...), admin: dict = Depends(require_admin)):
    """{to: place} (1-based) in the list. Games started from now on play the new order."""
    with db.get_conn() as conn:
        try:
            return levels.move(conn, level_id, body.get("to") if isinstance(body, dict) else None)
        except levels.LevelError as e:
            raise HTTPException(404 if "isn't known" in str(e) else 422, str(e))


@router.delete("/admin/levels/{level_id}")
def delete_level(level_id: str, admin: dict = Depends(require_admin)):
    """Delete a level the AI made. Built-in levels can only be retired."""
    with db.get_conn() as conn:
        try:
            levels.delete(conn, level_id)
        except levels.LevelError as e:
            raise HTTPException(404 if "isn't known" in str(e) else 409, str(e))
    return {"status": "ok"}


@router.post("/admin/ai/test")
def ai_test(body: dict = Body(default={}), admin: dict = Depends(require_admin)):
    """Test connection with what's on App settings, or with unsaved values from the page ({provider, url, model,
    apiKey}; an empty apiKey uses the saved one). Lists the provider's models and asks the model one tiny question."""
    v = settings.all()
    body = body if isinstance(body, dict) else {}
    provider = body.get("provider") if body.get("provider") in settings.PROVIDERS else v["ai_provider"]
    url = body.get("url") if isinstance(body.get("url"), str) else v["ai_url"]
    url = (url.strip() or settings.DEFAULT_URLS.get(provider, "")).rstrip("/")
    model = body.get("model").strip() if isinstance(body.get("model"), str) else v["ai_model"]
    key = body.get("apiKey") if isinstance(body.get("apiKey"), str) and body.get("apiKey") else v["ai_api_key"]
    cfg = ai_client.Config(provider=provider, url=url, model=model, api_key=key, max_output_tokens=200)
    if not url:
        return {"ok": False, "error": "Enter the address of your Ollama server first."}
    try:
        models = ai_client.list_models(cfg)
    except ai_client.AIError as e:
        return {"ok": False, "error": str(e)}
    out = {"ok": True, "models": sorted(m for m in models if m)[:200], "answer": None}
    if model:
        t0 = time.monotonic()
        try:
            reply = ai_client.generate('Answer with this JSON only: {"ok": true}', want_json=True, timeout=60,
                                       max_tokens=50, cfg=cfg)
            out["answer"] = reply.text.strip()[:200]
            ai_usage.record(purpose="test", provider=provider, model=model, ok=True, tokens_in=reply.input_tokens,
                            tokens_out=reply.output_tokens, ms=int((time.monotonic() - t0) * 1000))
        except ai_client.AIError as e:
            out.update(ok=False, error=str(e))
            ai_usage.record(purpose="test", provider=provider, model=model, ok=False, error=str(e),
                            ms=int((time.monotonic() - t0) * 1000))
    return out


@router.get("/admin/ai/usage")
def ai_usage_report(days: int = Query(default=30, ge=7, le=90), admin: dict = Depends(require_admin)):
    with db.get_conn() as conn:
        return ai_usage.report(conn, days)
