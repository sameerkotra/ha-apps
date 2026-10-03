"""The Web debug page's API (administrators only): what the web lookups send and get back."""

import asyncio

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.auth import CurrentUser, require_admin
from app.db import get_db, get_db_session
from app.api.common import require_home as _home
from app.services import webdebug, webdebug_runs

router = APIRouter(prefix="/api/v1/webdebug", tags=["web debug"])


class SearchTry(BaseModel):
    query: str = Field(..., min_length=1, max_length=300)
    site: str | None = Field(None, max_length=255, description="Limit to this website's domain")
    limit: int = Field(5, ge=1, le=10)
    use_cache: bool = Field(True, description="Answer from saved results when this search was made recently")


class FetchTry(BaseModel):
    url: str = Field(..., min_length=8, max_length=2000)
    use_cache: bool = Field(True, description="Use the saved page when it was opened in the last day")


class HoursTry(BaseModel):
    home_id: str
    location_id: str


class PriceTry(BaseModel):
    home_id: str
    item_id: str
    chain_id: str


class PresetRun(BaseModel):
    home_id: str
    preset: str = Field(..., pattern="^(queries|hours|prices|deals)$")
    limit: int = Field(5, ge=1, le=25, description="How many locations, items or stores")


class DealsTry(BaseModel):
    home_id: str
    chain_id: str


async def _in_thread(fn, *args):
    """Run a dry run in a worker thread with its own database session (the trace follows it there)."""
    def work():
        with get_db_session()() as db:
            return fn(db, *args)
    return await asyncio.to_thread(work)


@router.get("/setup")
async def setup(_: CurrentUser = Depends(require_admin)):
    """The web search provider in use and its settings (no secrets: only whether a key or token is set)."""
    return webdebug_runs.setup()


@router.post("/resume")
async def resume(_: CurrentUser = Depends(require_admin)):
    """End a pause in web searches early (after the search engines stopped blocking)."""
    from app.services import websearch
    websearch.resume()
    return {"paused": None}


@router.delete("/cache")
async def clear_cache(_: CurrentUser = Depends(require_admin)):
    """Forget all saved search results and pages (the next lookups search again)."""
    from app.services import webcache
    await asyncio.to_thread(webcache.clear)
    return {"cleared": True}


@router.get("/options")
async def options(home_id: str, _: CurrentUser = Depends(require_admin), db: Session = Depends(get_db)):
    """Stores, locations and items to try lookups with."""
    _home(db, home_id)
    return webdebug_runs.options(db, home_id)


@router.get("/log")
async def log(limit: int = 100, service: str | None = None, _: CurrentUser = Depends(require_admin)):
    """The latest requests made by any lookup, newest first (kept in memory, the last 150; cleared on restart).
    ``service``: tavily, searxng, custom, page (pages the app opens itself) or model."""
    return webdebug.entries(max(1, min(limit, webdebug.KEEP)), service)


@router.delete("/log")
async def clear_log(_: CurrentUser = Depends(require_admin)):
    webdebug.clear()
    return {"cleared": True}


@router.post("/search")
async def try_search(request: SearchTry, _: CurrentUser = Depends(require_admin)):
    """One search, exactly as lookups send it, with the hits read from the answer."""
    return await asyncio.to_thread(webdebug_runs.run_search, request.query, request.site, request.limit, request.use_cache)


@router.post("/fetch")
async def try_fetch(request: FetchTry, _: CurrentUser = Depends(require_admin)):
    """Open one page as lookups do, and show what is read from it: text, structured prices and hours, and the
    part the model would be given for hours."""
    return await asyncio.to_thread(webdebug_runs.run_fetch, request.url, request.use_cache)


@router.post("/hours")
async def try_hours(request: HoursTry, _: CurrentUser = Depends(require_admin), db: Session = Depends(get_db)):
    """A full hours lookup for one location, nothing saved."""
    _home(db, request.home_id)
    return await _in_thread(webdebug_runs.run_hours, request.home_id, request.location_id)


@router.post("/price")
async def try_price(request: PriceTry, _: CurrentUser = Depends(require_admin), db: Session = Depends(get_db)):
    """A full online price check of one item at one store, nothing saved."""
    _home(db, request.home_id)
    return await _in_thread(webdebug_runs.run_price, request.home_id, request.item_id, request.chain_id)


@router.post("/preset")
async def run_preset(request: PresetRun, _: CurrentUser = Depends(require_admin), db: Session = Depends(get_db)):
    """``queries``: what every lookup would open and search for (nothing is sent). ``hours``, ``prices``, ``deals``:
    that lookup for every location / item-at-store pair / store, as a dry run in the background (nothing saved);
    follow ``job`` like other lookups, then read the results from ``GET /webdebug/preset/{job_id}``."""
    _home(db, request.home_id)
    if request.preset == "queries":
        return {"queries": webdebug_runs.preset_queries(db, request.home_id, request.limit)}
    from app.services import webinfo
    try:
        return {"job": await asyncio.to_thread(webdebug_runs.start_preset, request.home_id, request.preset, request.limit)}
    except webinfo.WebInfoError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))


@router.get("/preset/{job_id}")
async def preset_results(job_id: str, _: CurrentUser = Depends(require_admin)):
    """Results of a preset run so far (``finished`` once all are done), each with its requests (``detail.trace``)."""
    found = webdebug_runs.preset_results(job_id)
    if found is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="No results for that run (they are kept for the last few runs only)")
    return found


@router.post("/deals")
async def try_deals(request: DealsTry, _: CurrentUser = Depends(require_admin), db: Session = Depends(get_db)):
    """A deals lookup at one store (its first few pages), nothing saved."""
    _home(db, request.home_id)
    return await _in_thread(webdebug_runs.run_deals, request.home_id, request.chain_id)
