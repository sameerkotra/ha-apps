"""AI estimate, AI Assistant chat, warm-up and status — for everyone. What is
sent: the food description typed for "✨ Estimate with AI", or the chat
message; nothing else from the database. Provider, address and model come
from Admin → App settings (ai_client reads them at each request)."""
from fastapi import APIRouter, Depends, HTTPException

from .. import ai_client, schemas
from ..auth import get_current_user

router = APIRouter(prefix="/api/ai", tags=["ai"])


def _http_error(e: ai_client.AIError) -> HTTPException:
    # 503 = nothing to talk to yet (not set up); 502 = the provider failed.
    return HTTPException(503 if e.kind == "not_configured" else 502, str(e))


@router.get("/status")
async def ai_status(user: dict = Depends(get_current_user)):
    return ai_client.status()


@router.post("/warmup")
async def warmup_endpoint(user: dict = Depends(get_current_user)):
    """Ollama: loads the model with a tiny "hi". Other providers need no
    warm-up (it would only cost money): nothing is sent."""
    try:
        ai_client.check_configured(ai_client.current())
    except ai_client.AIError as e:
        raise _http_error(e)
    st = ai_client.status()
    if not st["warmup"]:
        return {"ok": True, "model": st["model"], "skipped": True}
    if not await ai_client.warmup():
        st = ai_client.status()
        raise HTTPException(502, st["last_error"] or f"Could not reach Ollama at {st['url']}.")
    return {"ok": True, "model": st["model"]}


@router.post("/estimate")
async def estimate_food(payload: schemas.AIEstimateRequest, user: dict = Depends(get_current_user)):
    await ai_client.ensure_warm()
    try:
        return await ai_client.estimate(payload.description)
    except ai_client.AIError as e:
        raise _http_error(e)


@router.post("/chat")
async def chat(payload: schemas.AIChatRequest, user: dict = Depends(get_current_user)):
    await ai_client.ensure_warm()
    try:
        reply = await ai_client.chat(payload.message)
    except ai_client.AIError as e:
        raise _http_error(e)
    return {"reply": reply}
