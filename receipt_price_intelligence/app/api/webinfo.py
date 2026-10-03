"""Store hours and deals found on the web through your local search server."""

import asyncio
from urllib.parse import urlparse

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.auth import CurrentUser, get_current_user, require_admin
from app.db import get_db
from app.api.common import require_home as _home
from app.services import textllm, webinfo, websearch, websites

router = APIRouter(prefix="/api/v1", tags=["web lookups"])


class PricesRequest(BaseModel):
    item_ids: list[str] | None = Field(None, description="Items to check (default: the ones you buy most)")
    chain_ids: list[str] | None = Field(None, description="Only these stores (default: every store you bought the item from)")
    force: bool = Field(False, description="Check again even if it was checked recently")
    all_stores: bool = Field(False, description="Also try every item at every other store (the trip planner compares all of them)")


class IgnoreRequest(BaseModel):
    item_id: str
    chain_id: str
    ignored: bool = Field(True, description="true: stop using this online price; false: use it again")


class WebsitesRequest(BaseModel):
    search: bool = Field(True, description="Also search the web for chains whose website the store pages do not give")


class HoursRequest(BaseModel):
    location_ids: list[str] | None = Field(None, description="Only these store locations (default: every one without hours)")
    force: bool = Field(False, description="Check again even if it was checked recently")


def _job(fn, *args):
    try:
        return fn(*args)
    except webinfo.WebInfoError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))


@router.get("/webinfo/status")
async def web_status(_: CurrentUser = Depends(get_current_user)):
    """Whether a search server is set up (``configured``) and which one."""
    return webinfo.status()


@router.post("/webinfo/test")
async def web_test(_: CurrentUser = Depends(require_admin)):
    """Try the search server and the model once and report what came back, to check the set-up.

    Searches for a fixed phrase, fetches the first result, and reports the shape of what the server
    answered, so a mismatch in field names is easy to see.
    """
    if not websearch.configured():
        return {"ok": False, "step": "config", "error": "No web search is set up. Choose a provider in Admin → App settings (tavily needs its API key; custom and searxng need an address)."}
    report: dict = {"ok": False, "server": websearch.describe(), "provider": websearch.provider()}
    try:
        hits = await asyncio.to_thread(websearch.search, "opening hours", 3)
    except websearch.WebSearchError as e:
        return {**report, "step": "search", "error": str(e)}
    report["hits"] = [{"title": h["title"][:80], "url": h["url"], "snippet": h["snippet"][:120]} for h in hits]
    if not hits:
        return {**report, "step": "search", "error": "The search worked but returned no results, or in a shape that could not be read."}
    # Try up to three results: one site refusing automated visits is normal and says nothing about the set-up.
    urls = [h["url"] for h in hits if h["url"]][:3]
    failures: list[str] = []
    for url in urls:
        try:
            page = await asyncio.to_thread(websearch.fetch, url)
        except websearch.WebSearchError as e:
            failures.append(f"{urlparse(url).netloc}: {e}")
            continue
        report["fetch_preview"] = page["text"][:200]
        report["fetch_chars"] = len(page["text"])
        report["fetch_url"] = url
        break
    if failures:
        report["fetch_failures"] = failures
    if urls and "fetch_preview" not in report:
        if websearch.provider() == "custom":
            return {**report, "step": "fetch", "error": "; ".join(failures)}
        fallback = (f"the page text {websearch.describe()} sends with each result" if websearch.provider() in websearch.HOSTED
                    else "the short description in each search result (less to go on)")
        report["fetch_note"] = (f"None of the first {len(urls)} pages could be opened by the app. Sites that refuse are "
                                f"read from {fallback} instead; if every site fails the same way, check the reasons below.")
    try:
        answer = await asyncio.to_thread(textllm.complete_json, "Answer with JSON only.", 'Reply with {"ok": true}.', 50)
        report["model"] = "answered" if isinstance(answer, dict) else "no answer"
    except textllm.TextLLMError as e:
        return {**report, "step": "model", "error": str(e)}
    return {**report, "ok": True, "step": "done"}


@router.get("/webinfo/jobs/{job_id}")
async def web_job(job_id: str, _: CurrentUser = Depends(get_current_user)):
    """Progress of a lookup: ``done`` of ``total``, ``message``, ``error`` and ``finished``."""
    job = webinfo.get_job(job_id)
    if job is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Lookup not found")
    return job


@router.get("/homes/{home_id}/online-price-ignores")
async def list_ignores(home_id: str, _: CurrentUser = Depends(get_current_user), db: Session = Depends(get_db)):
    """Online prices this home chose not to use: [[item id, chain id]]."""
    _home(db, home_id)
    return [list(p) for p in webinfo.ignored_pairs(db, home_id)]


@router.post("/homes/{home_id}/online-price-ignores")
async def set_ignore(home_id: str, request: IgnoreRequest, _: CurrentUser = Depends(get_current_user), db: Session = Depends(get_db)):
    """Stop (``ignored: true``) or start again (``false``) using the online price of an item at a store. While ignored,
    that item at that store uses its receipt price everywhere (trip planner, shopping list, Home Assistant) and price
    checks skip it."""
    _home(db, home_id)
    webinfo.set_ignored(db, home_id, request.item_id, request.chain_id, request.ignored)
    return {"ignored": request.ignored}


@router.post("/homes/{home_id}/store-websites/fill")
async def fill_websites(home_id: str, request: WebsitesRequest, _: CurrentUser = Depends(get_current_user), db: Session = Depends(get_db)):
    """Fill in the website of this home's store chains that have none.

    Websites taken from the stores' own pages (set by hand or from OpenStreetMap) are set straight away
    (``applied``). For the rest, a web search runs in the background (``job``, followed like other lookups);
    the finished job's ``suggestions`` are only saved when the person accepts them (``PUT /store-chains/{id}/web``).
    ``missing`` counts chains still without a website."""
    _home(db, home_id)
    return websites.fill(db, home_id, request.search)


@router.get("/homes/{home_id}/webinfo/hours")
async def hours_suggestions(home_id: str, _: CurrentUser = Depends(get_current_user), db: Session = Depends(get_db)):
    """Opening hours found online for this home's stores, waiting to be applied."""
    _home(db, home_id)
    return webinfo.list_hours(db, home_id)


@router.post("/homes/{home_id}/webinfo/hours")
async def find_hours(home_id: str, request: HoursRequest, _: CurrentUser = Depends(get_current_user), db: Session = Depends(get_db)):
    """Start looking up opening hours online (in the background). Returns the lookup to follow."""
    _home(db, home_id)
    return _job(webinfo.start_hours_job, home_id, request.location_ids, request.force)


@router.post("/webinfo/hours/{location_id}/apply")
async def apply_hours(location_id: str, _: CurrentUser = Depends(get_current_user), db: Session = Depends(get_db)):
    """Use the suggested hours for this store location."""
    result = webinfo.apply_hours(db, location_id)
    if result is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="No suggestion for this store")
    return result


@router.post("/webinfo/hours/{location_id}/dismiss")
async def dismiss_hours(location_id: str, _: CurrentUser = Depends(get_current_user), db: Session = Depends(get_db)):
    """Turn a suggestion down (it will not be offered again unless the hours found change)."""
    if not webinfo.dismiss_hours(db, location_id):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="No suggestion for this store")
    return {"status": "dismissed", "location_id": location_id}


@router.get("/homes/{home_id}/webinfo/deals")
async def deals(home_id: str, kind: str | None = None, _: CurrentUser = Depends(get_current_user), db: Session = Depends(get_db)):
    """What was found online for items you buy, each compared with what you usually pay at that store.

    ``kind`` is ``DEAL`` (a promotion) or ``PRICE`` (the current shelf price on the store's website); omit it
    for both. ``saving_pct`` is how much less it is than your usual price (negative when it costs more).
    """
    _home(db, home_id)
    rows = webinfo.deal_rows(db, home_id)
    return [r for r in rows if not kind or r["kind"] == kind.upper()]


@router.post("/homes/{home_id}/webinfo/deals/refresh")
async def refresh_deals(home_id: str, _: CurrentUser = Depends(get_current_user), db: Session = Depends(get_db)):
    """Start looking for deals online (in the background). Returns the lookup to follow."""
    _home(db, home_id)
    return _job(webinfo.start_deals_job, home_id)


@router.post("/homes/{home_id}/webinfo/prices/refresh")
async def refresh_prices(home_id: str, request: PricesRequest, _: CurrentUser = Depends(get_current_user), db: Session = Depends(get_db)):
    """Start checking the current price of items on the websites of the stores you buy them at (in the background).

    A price is kept only if a listing clearly is that item and can be compared in the item's own unit. An
    item-at-store pair already checked within `WEB_PRICE_FRESHNESS_HOURS` is skipped and its stored price
    used instead, unless `force` is set. Returns the lookup to follow; its result carries `skipped`, how
    many pairs were left out for already being fresh.
    """
    _home(db, home_id)
    return _job(webinfo.start_prices_job, home_id, request.item_ids, request.chain_ids, request.force, request.all_stores)


@router.get("/homes/{home_id}/webinfo/prices/history")
async def price_history(home_id: str, days: int = 30, _: CurrentUser = Depends(get_current_user), db: Session = Depends(get_db)):
    """Every online price check of the last ``days`` days (up to 30), grouped by the date it was checked,
    newest first. Each check says how it was read (``method``: structured, text or model) and whether it
    is the one currently used for that item at that store (``latest``)."""
    _home(db, home_id)
    return webinfo.price_history(db, home_id, max(1, min(days, webinfo.PRICE_HISTORY_DAYS)))
