"""Optional AI (SPEC §17.6): read text, summarise, text → checklist, table → sheet, ask about a folder, the per-folder
"read text from scans" switch; Admin → AI usage and Test connection. Every route refuses (409) while AI is off or
not set up, before anything is sent."""
import time

from fastapi import APIRouter, Body, Depends, HTTPException, Query
from starlette.concurrency import run_in_threadpool

from .. import ai, ai_client, ai_usage, db, settings
from ..auth import require_admin, require_user

router = APIRouter(prefix="/api", tags=["ai"])


def _body(body) -> dict:
    if not isinstance(body, dict):
        raise HTTPException(422, "Send a JSON object.")
    return body


def _str(body: dict, key: str, max_len: int = 70) -> str | None:
    v = body.get(key)
    if v is None:
        return None
    if not isinstance(v, str) or len(v) > max_len:
        raise HTTPException(422, f"{key} isn't right.")
    return v


@router.get("/ai")
def ai_state(user: dict = Depends(require_user)):
    with db.get_conn() as conn:
        return ai.public_state(conn)


@router.post("/ai/ocr")
async def ocr(body: dict = Body(...), user: dict = Depends(require_user)):
    nid = _str(_body(body), "id")
    if not nid:
        raise HTTPException(422, "Choose a file.")
    return await run_in_threadpool(ai.ocr_node, nid, user)


@router.post("/ai/suggest-filing")
async def suggest_filing(body: dict = Body(...), user: dict = Depends(require_user)):
    """§17.18: a suggested name and folder for a file from its text (shown to accept, never applied by itself)."""
    nid = _str(_body(body), "id")
    if not nid:
        raise HTTPException(422, "Choose a file.")
    return await run_in_threadpool(ai.suggest_filing, user, nid)


@router.post("/ai/summarise")
async def summarise(body: dict = Body(...), user: dict = Depends(require_user)):
    nid = _str(_body(body), "id")
    if not nid:
        raise HTTPException(422, "Choose a document.")
    return await run_in_threadpool(ai.summarise, user, nid)


@router.post("/ai/to-checklist")
async def to_checklist(body: dict = Body(...), user: dict = Depends(require_user)):
    b = _body(body)
    return await run_in_threadpool(ai.to_checklist, user, _str(b, "text", 200_000), _str(b, "id"))


@router.post("/ai/to-sheet")
async def to_sheet(body: dict = Body(...), user: dict = Depends(require_user)):
    b = _body(body)
    return await run_in_threadpool(ai.to_sheet, user, _str(b, "text", 200_000), _str(b, "image", 12_000_000))


@router.post("/ai/ask")
async def ask(body: dict = Body(...), user: dict = Depends(require_user)):
    b = _body(body)
    ref = _str(b, "folder")
    if not ref:
        raise HTTPException(422, "Choose a folder.")
    return await run_in_threadpool(ai.ask, user, ref, _str(b, "question", 2000) or "")


@router.get("/ai/folder/{ref}")
def folder_state(ref: str, user: dict = Depends(require_user)):
    with db.get_conn() as conn:
        return ai.folder_state(conn, user, ref[:70])


@router.put("/ai/folder/{ref}")
def set_folder(ref: str, body: dict = Body(...), user: dict = Depends(require_user)):
    with db.get_conn() as conn:
        return ai.set_folder(conn, user, ref[:70], bool(_body(body).get("on")))


# ---------------------------------------------------------------- admin
@router.post("/admin/ai/test")
def ai_test(body: dict = Body(default={}), admin: dict = Depends(require_admin)):
    """Test connection with what's on App settings, or with unsaved values from the page ({provider, url, model,
    apiKey}; an empty apiKey uses the saved one). Lists the provider's models and asks the model one tiny question."""
    v = settings.all_values()
    body = body if isinstance(body, dict) else {}
    provider = body.get("provider") if body.get("provider") in settings.PROVIDERS else v["ai_provider"]
    url = body.get("url") if isinstance(body.get("url"), str) else v["ai_url"]
    url = (url.strip() or settings.DEFAULT_URLS.get(provider, "")).rstrip("/")
    model = body.get("model").strip() if isinstance(body.get("model"), str) else v["ai_model"]
    key = body.get("apiKey") if isinstance(body.get("apiKey"), str) and body.get("apiKey") else v["ai_api_key"]
    cfg = ai_client.Config(provider=provider, url=url, model=model, api_key=key)
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
