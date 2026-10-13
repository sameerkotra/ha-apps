"""AI estimate, AI Assistant chat, warm-up and status — for everyone. What is
sent: the food description typed for "✨ Estimate with AI", or the chat
message; nothing else from the database. Provider, address and model come
from Admin → App settings (ai_client reads them at each request).

Plain functions (FastAPI runs them in the threadpool): the model call blocks. Each estimate and chat message
counts against the signed-in person's limits (App settings → AI limits; 429 past them), and at most two requests
reach the model at a time (ai_client.MAX_CONCURRENT)."""
from fastapi import APIRouter, Depends, HTTPException

from .. import ai_client, schemas
from ..auth import get_current_user

router = APIRouter(prefix="/api/ai", tags=["ai"])

# What someone who isn't an admin is told when the provider fails: the provider's own message names the household's
# server (its address), which is the admins' business.
MEMBER_MESSAGES = {
    "unreachable": "The AI couldn't be reached — an admin can check Admin → App settings.",
    "timeout": "The AI didn't answer in time — try again in a minute.",
    "auth": "The AI refused the app's access key — an admin can check Admin → App settings.",
    "not_found": "The AI model wasn't found — an admin can check Admin → App settings.",
}


def _http_error(e: ai_client.AIError, user: dict | None = None) -> HTTPException:
    # 503 = nothing to talk to yet (not set up); 429 = every slot busy here; 502 = the provider failed.
    status = 503 if e.kind == "not_configured" else 429 if e.kind == "busy" else 502
    message = str(e)
    if user is not None and not user["is_admin"]:
        message = MEMBER_MESSAGES.get(e.kind, message)
    return HTTPException(status, message)


def _turn(user: dict) -> None:
    try:
        ai_client.take_turn(user["id"])
    except ai_client.LimitReached as e:
        raise HTTPException(429, str(e))


@router.get("/status")
def ai_status(user: dict = Depends(get_current_user)):
    return ai_client.status(admin=user["is_admin"])


@router.post("/warmup")
def warmup_endpoint(user: dict = Depends(get_current_user)):
    """Ollama: loads the model with a tiny "hi". Other providers need no
    warm-up (it would only cost money): nothing is sent."""
    try:
        ai_client.check_configured(ai_client.current())
    except ai_client.AIError as e:
        raise _http_error(e)
    st = ai_client.status()
    if not st["warmup"]:
        return {"ok": True, "model": st["model"] if user["is_admin"] else None, "skipped": True}
    if not ai_client.warmup_blocking():
        st = ai_client.status()
        if not user["is_admin"]:
            raise HTTPException(502, "The AI couldn't be woken up — an admin can check Admin → App settings.")
        raise HTTPException(502, st["last_error"] or f"Could not reach Ollama at {st['url']}.")
    return {"ok": True, "model": st["model"] if user["is_admin"] else None}


@router.post("/estimate")
def estimate_food(payload: schemas.AIEstimateRequest, user: dict = Depends(get_current_user)):
    _turn(user)
    ai_client.ensure_warm_blocking()
    try:
        return ai_client.estimate_blocking(payload.description)
    except ai_client.AIError as e:
        raise _http_error(e, user)


@router.post("/chat")
def chat(payload: schemas.AIChatRequest, user: dict = Depends(get_current_user)):
    _turn(user)
    ai_client.ensure_warm_blocking()
    try:
        reply = ai_client.chat_blocking(payload.message)
    except ai_client.AIError as e:
        raise _http_error(e, user)
    return {"reply": reply}
