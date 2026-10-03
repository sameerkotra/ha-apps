"""Find store opening hours and deals on the web, through your local search server.

* **Hours**: for a store location, search for its hours, read the best pages with the model, and keep
  the answer as a *suggestion*. Nothing changes until a person applies it (or ``WEB_AUTO_HOURS`` is on
  and the store has no hours yet).
* **Deals**: for each chain you shop at, search for its weekly ad and sale pages, have the model list
  the sale items, and keep the ones that are items you buy.

Every lookup is bounded (a few searches and model calls) and runs in a background thread, with up
to ``WEB_SEARCH_CONCURRENCY`` of its items being worked on at once; progress is kept in memory for
the page to show. All answers pass ``webparse`` validation. A price already checked within
``WEB_PRICE_FRESHNESS_HOURS`` is skipped and the stored value reused.
"""

import json
import re
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta
from typing import Any, Callable
from urllib.parse import quote_plus, urlparse

from sqlalchemy.orm import Session

from app.config import get_settings
from app.db import get_db_session
from app.db.models import CommonItemAlias, PriceTarget, StoreChain, StoreLocation, WebDeal, WebHours
from app.logging_config import get_logger
from app.services import analytics, itemquery, priceextract, textllm, trips, webdebug, webparse, websearch, reqcache
from app.services.analysis_data import load_observations

logger = get_logger("webinfo")

MAX_LLM_CALLS_PER_JOB = 30
MAX_LLM_CALLS_PER_CHAIN = 4     # so the first chains cannot use up the whole job
PAGE_CHARS_FOR_MODEL = 9000
DEAL_MAX_AGE_DAYS = 21          # a deal older than this is dropped even if it gives no end date
PRICE_MAX_AGE_DAYS = 7          # a shelf price found online is only trusted for this long
PRICE_HISTORY_DAYS = 30         # price checks are kept this long, to show them by date
MAX_PRICE_LOOKUPS = 30          # item-at-store lookups in one price job
MAX_PRICE_LOOKUPS_TRIP = 60     # ... when the trip planner checks every item at every store
MAX_LLM_CALLS_PRICES = 45
DEFAULT_PRICE_ITEMS = 10        # items looked up when none are chosen: the ones you buy most
HOURS_FRESH_DAYS = 30
MAX_CHAINS_PER_JOB = 8
MIN_MATCH_SCORE = 0.6

NOT_SET_UP = ("No web search is set up. Choose a provider in Admin → App settings: tavily (free API key from tavily.com), "
              "searxng or your own search server.")

_jobs: dict[str, dict[str, Any]] = {}
_jobs_lock = threading.Lock()


class _Budget:
    """A count of model calls left for one job, safe to share between worker threads."""

    def __init__(self, limit: int):
        self._left = limit
        self._lock = threading.Lock()

    def take(self) -> bool:
        """Reserve one call. False, and nothing reserved, once the limit is used up."""
        with self._lock:
            if self._left <= 0:
                return False
            self._left -= 1
            return True


def _run_parallel(job: dict[str, Any], items: list, handler: Callable[[Any], bool]) -> None:
    """Work through ``items`` with up to ``web_search_concurrency`` running at once.

    ``handler(item)`` does the work for one item — including opening its own database session,
    since one session cannot be shared between threads — and returns True if it produced a
    result. ``job["done"]``/``["results"]`` are updated as each finishes. One item failing is
    logged and skipped so it never stops the rest; but if every single item failed with the
    search server or model being unreachable, that is a broken setup rather than one bad store,
    so it is raised once the batch finishes, the same as a lookup that never got started at all.
    """
    if not items:
        return
    workers = max(1, min(get_settings().web_search_concurrency, len(items)))
    lock = threading.Lock()
    connectivity_errors: list[Exception] = []

    def run_one(item: Any) -> None:
        try:
            ok = handler(item)
        except (websearch.WebSearchError, textllm.TextLLMError) as e:
            ok = False
            with lock:
                connectivity_errors.append(e)
            logger.warning("Lookup failed: %s", e)
        except WebInfoError as e:  # e.g. the job's model-call budget ran out: expected, not a crash
            ok = False
            logger.info("Lookup skipped: %s", e)
        except Exception:  # noqa: BLE001 - one item must never stop the rest of the batch
            ok = False
            logger.exception("Lookup failed unexpectedly")
        with lock:
            job["done"] += 1
            if ok:
                job["results"] += 1

    with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="webinfo") as pool:
        list(pool.map(run_one, items))

    blocked = next((e for e in connectivity_errors if isinstance(e, websearch.SearchBlocked)), None)
    if blocked is not None:
        # the search engines are refusing: say so plainly, even if some items were found before it started
        job["error"] = str(blocked)
        return
    if items and job["results"] == 0 and len(connectivity_errors) == len(items):
        raise connectivity_errors[0]


class WebInfoError(ValueError):
    """A lookup that cannot be done. The message is safe to show."""


# --------------------------------------------------------------------------- #
# Jobs (progress kept in memory)
# --------------------------------------------------------------------------- #

def _new_job(kind: str, home_id: str, total: int) -> dict[str, Any]:
    job = {"id": uuid.uuid4().hex[:12], "kind": kind, "home_id": home_id, "total": total, "done": 0, "message": "Starting…",
           "error": None, "finished": False, "started": time.time(), "results": 0}
    with _jobs_lock:
        for old in [k for k, v in _jobs.items() if v["finished"] and time.time() - v["started"] > 3600]:
            del _jobs[old]
        _jobs[job["id"]] = job
    return job


def get_job(job_id: str) -> dict[str, Any] | None:
    with _jobs_lock:
        return dict(_jobs[job_id]) if job_id in _jobs else None


def running_job(kind: str, home_id: str) -> dict[str, Any] | None:
    with _jobs_lock:
        for job in _jobs.values():
            if job["kind"] == kind and job["home_id"] == home_id and not job["finished"]:
                return dict(job)
    return None


def _run_in_thread(job: dict[str, Any], work: Callable[[dict[str, Any]], None]) -> None:
    def target() -> None:
        try:
            with reqcache.scope():  # the job's own reads are made once (see reqcache)
                work(job)
        except (websearch.WebSearchError, textllm.TextLLMError, WebInfoError) as e:
            job["error"] = str(e)
        except Exception:  # noqa: BLE001
            logger.exception("Web lookup failed")
            job["error"] = "The lookup failed unexpectedly. See the app's Log tab."
        finally:
            job["finished"] = True
            if not job["error"]:
                job["message"] = "Done"

    threading.Thread(target=target, daemon=True, name=f"webinfo-{job['kind']}").start()


def status() -> dict[str, Any]:
    settings = get_settings()
    return {"configured": websearch.configured(), "server": websearch.describe(), "provider": websearch.provider(), "auto_hours": settings.web_auto_hours, "deals_days": settings.web_deals_days}


# --------------------------------------------------------------------------- #
# Opening hours
# --------------------------------------------------------------------------- #

def _city(loc: dict[str, Any]) -> str | None:
    row = loc.get("_address_row")
    return getattr(row, "city", None) if row is not None else None


def _street(loc: dict[str, Any]) -> str | None:
    row = loc.get("_address_row")
    return getattr(row, "street_line_1", None) if row is not None else None


def _open(url: str, why: str) -> dict[str, str] | None:
    """A page, or None (logged with the reason) if it cannot be opened."""
    try:
        return websearch.fetch(url)
    except websearch.WebSearchError as e:
        logger.info("Skipped %s page %s: %s", why, url, e)
        return None


def _search(query: str, limit: int, site: str | None, why: str) -> list[dict[str, str]]:
    """Search, limited to ``site`` when given. A limited search that fails only skips this step."""
    if not site:
        return websearch.search(query, limit)
    try:
        return websearch.search(query, limit, site=site)
    except websearch.WebSearchError as e:
        logger.info("Search on %s for %s failed: %s", site, why, e)
        return []


def _full_text(page: dict[str, str]) -> str:
    """All of a page's text. ``page["text"]`` is cut to its first part, where store pages keep their menus;
    the hours are often further down, so the text is made again from the whole page."""
    return priceextract.page_text(page.get("raw") or "") or page.get("text") or ""


def hours_query(loc: dict[str, Any]) -> str:
    """The search words of an hours lookup (also shown on the Web debug page)."""
    return " ".join(x for x in (loc["chain_name"], _street(loc), _city(loc), "store hours") if x)


def price_queries(item: dict[str, Any], chain: dict[str, Any]) -> dict[str, str | None]:
    """What a price check opens and searches for, in order: {store_search_url, site_query, site, web_query}.

    The item's brand (from its receipts) is only searched for at stores it was bought at: elsewhere the store may
    not carry that brand, and the plain name finds what it does carry."""
    if item.get("fuel"):
        grade = item["fuel"]
        return {"store_search_url": None, "site": websearch.site_domain(chain.get("website")),
                "site_query": " ".join(x for x in ("gas prices", chain.get("zip") or chain.get("city")) if x),
                "web_query": " ".join(x for x in (chain["name"], "gas prices", grade if grade != "regular" else None,
                                                  chain.get("zip") or " ".join(x for x in (chain.get("street"), chain.get("city")) if x)) if x),
                "store_pages": chain.get("page_urls") or [], "fuel": grade}
    bought_here = not item.get("chain_ids") or chain.get("id") in item["chain_ids"]
    name = (item.get("search_name") if bought_here else item.get("plain_name")) or item["name"]
    return {
        "store_search_url": chain["search_url"].replace("{query}", quote_plus(item.get("plain_name") or item["name"])) if chain.get("search_url") else None,
        "site_query": " ".join(x for x in (name, "price") if x),
        "site": websearch.site_domain(chain.get("website")),
        "web_query": " ".join(x for x in (chain["name"], name, "price", chain.get("zip") or chain["city"]) if x),
    }


def find_fuel_price(item: dict[str, Any], chain: dict[str, Any], budget: "_Budget | None" = None) -> dict[str, Any] | None:
    """The price per gallon of the item's fuel grade posted for this station, or None.

    Stations post one price per grade and change it daily, so the lookup reads what the station posts rather than
    matching product listings: the location's own page first (a warehouse page lists its gas prices), then search
    results on the store's site and on the web, reading each result's snippet before opening its page (snippets
    often carry the prices). A page that shows prices the reader cannot place is given to the model, if allowed.
    """
    grade, q = item["fuel"], price_queries(item, chain)

    def picked(prices: dict[str, float], url: str, how: str) -> dict[str, Any] | None:
        value = prices.get(grade)
        if value is None:
            return None
        others = ", ".join(f"{g} ${v:.3f}" for g, v in prices.items() if g != grade)
        return {"product": f"{chain['name']} {grade} gas", "price": value, "unit": "GAL", "normalized": value, "on_sale": False,
                "method": "text", "score": 1.0, "source_url": url, "source_title": f"{chain['name']} gas prices",
                "note": f"{grade} price posted {how}" + (f" (also {others})" if others else ""), "alternatives": []}

    for url in q.get("store_pages") or []:
        page = _open(url, "fuel")
        if page:
            found = picked(priceextract.fuel_prices(_full_text(page)), url, "on the store's page")
            if found:
                return found
    searches = ([(q["site_query"], q["site"])] if q.get("site") else []) + [(q["web_query"], None)]
    for query, site in searches:
        hits = _search(query, 5, site, "fuel") if site else websearch.search(query, 5)
        for hit in hits[:3]:
            snippet = f"{hit.get('title', '')}\n{hit.get('snippet', '')}"
            found = picked(priceextract.fuel_prices(snippet), hit["url"], "in a search result")
            if found:
                return found
            page = _open(hit["url"], "fuel") if hit.get("url") else None
            if page:
                text = _full_text(page)
                found = picked(priceextract.fuel_prices(text), hit["url"], "on the page")
                if found:
                    return found
                if "$" in text and budget is not None and budget.take():
                    excerpt = webparse.text_around(text, PAGE_CHARS_FOR_MODEL, ["regular", "premium", "diesel", "unleaded", "gas", "fuel"])
                    answer = textllm.complete_json(webparse.PRICES_SYSTEM, webparse.prices_prompt(chain["name"], chain.get("city"), f"{grade} gasoline, price per gallon", excerpt), 800)
                    for x in webparse.clean_prices(answer):
                        value = x.get("price")
                        if value and 1.5 <= value <= 9.99 and re.search(grade if grade != "regular" else r"regular|unleaded|gas", x.get("product", ""), re.I):
                            return {**picked({grade: round(value, 3)}, hit["url"], "on the page (read by the model)"), "method": "model"}
    return None


# --------------------------------------------------------------------------- #
# Area search: one search per item in your ZIP code, its results filed under your stores
# --------------------------------------------------------------------------- #

_GENERIC_NAME_WORDS = {"whole", "fresh", "market", "markets", "food", "foods", "club", "super", "save", "family", "dollar",
                       "general", "city", "the", "grocery", "grocer", "mart", "store", "stores", "wholesale", "center", "the"}


def _chain_patterns(name: str) -> list[re.Pattern]:
    """Ways a store's name appears in a search result: in full ("Sam's Club", "Sams Club"), by its first two words
    ("Whole Foods"), or by a distinctive first word ("Costco" for Costco Wholesale)."""
    words = re.findall(r"[a-z0-9]+", (name or "").lower())
    if not words:
        return []
    join = lambda ws: re.compile(r"\b" + r"\W{0,2}".join(map(re.escape, ws)) + r"\b", re.I)  # noqa: E731
    patterns = [join(words)]
    if len(words) > 2:
        patterns.append(join(words[:2]))
    if len(words[0]) >= 4 and words[0] not in _GENERIC_NAME_WORDS:
        patterns.append(join(words[:1]))
    return patterns


def _same_site(url: str | None, website: str | None) -> bool:
    from app.services.websites import registrable_domain
    a, b = registrable_domain(url), registrable_domain(website)
    return bool(a and b and a == b)


def hit_chains(hit: dict[str, str], chains: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Which of your stores a search result is about: the store whose website it is on, else the stores it names."""
    on_site = [c for c in chains if c.get("website") and _same_site(hit.get("url"), c["website"])]
    if on_site:
        return on_site[:1]
    text = " ".join((hit.get("title") or "", hit.get("url") or "", hit.get("snippet") or ""))
    return [c for c in chains if any(p.search(text) for p in _chain_patterns(c["name"]))]


def station_prices(text: str, chains: list[dict[str, Any]]) -> dict[str, float]:
    """{chain id: regular price per gallon} from a page listing many stations ("Costco … $3.29 · Sam's Club … $3.19").

    Each of your stations takes the first price after its name, before the next station named; when the chain has
    several stations on the page, the one with your station's street number nearby is preferred."""
    text = text or ""
    names = [(m.start(), m.end(), c["id"]) for c in chains for p in _chain_patterns(c["name"]) for m in p.finditer(text)]
    names.sort()
    any_name = sorted({s for s, _, _ in names})
    out: dict[str, float] = {}
    for c in chains:
        mine = [(s, e) for s, e, cid in names if cid == c["id"]]
        number = re.match(r"\s*(\d+)", c.get("street") or "")
        if number:
            # the station's own entry: from just before its name up to the next station named
            def entry(s: int, e: int) -> str:
                before = max([x for x in any_name if x < s] + [max(0, s - 60)])
                after = next((x for x in any_name if x > e), len(text))
                return text[max(before, s - 60):after]
            near = [(s, e) for s, e in mine if re.search(r"\b" + number.group(1) + r"\b", entry(s, e))]
            mine = near or mine
        for s, e in mine:
            stop = next((x for x in any_name if x > e), len(text))
            window = text[e:min(stop, e + 160)]
            for m in priceextract._PRICE_TOKEN.finditer(window):
                if priceextract.AVERAGE_BEFORE.search(window[max(0, m.start() - 30):m.start()]):
                    continue  # "averages $3.30": an area average, not this station's price
                if m.group(1) or m.group(3):  # "$3.29" or "3.299": clearly a price
                    value = priceextract._fuel_value(m.group(2), m.group(3))
                    if value:
                        out[c["id"]] = value
                        break
            if c["id"] in out:
                break
    return out


def area_search(item: dict[str, Any], chains: list[dict[str, Any]], budget: "_Budget | None" = None,
                max_pages: int = 2) -> dict[str, dict[str, Any]]:
    """One search for the item in your ZIP code ("2% Milk price 80134", "gas prices 80134"), with each result filed
    under the store it is about. Returns {chain id: picked price} for the stores it found a price for; the others are
    then searched one by one. Saves a search per store when results cover several of them."""
    area = next((c.get("zip") or c.get("city") for c in chains if c.get("zip") or c.get("city")), None)
    if not area:
        return {}
    fuel = item.get("fuel")
    if fuel and fuel != "regular":
        return {}  # area listings show the regular price
    query = f"gas prices {area}" if fuel else f"{item.get('plain_name') or item['name']} price {area}"
    hits = websearch.search(query, 8)
    name, others = item.get("plain_name") or item["name"], [item["name"], *(item.get("aliases") or [])]
    candidates: dict[str, list[dict[str, Any]]] = {}
    found: dict[str, dict[str, Any]] = {}
    opened = 0
    by_id = {c["id"]: c for c in chains}

    def fuel_pick(chain_id: str, value: float, url: str, how: str) -> None:
        if chain_id not in found:
            found[chain_id] = {"product": f"{by_id[chain_id]['name']} regular gas", "price": value, "unit": "GAL", "normalized": value,
                               "on_sale": False, "method": "text", "score": 1.0, "source_url": url, "source_title": f"gas prices {area}",
                               "note": f"regular price found by searching {area} ({how})", "alternatives": []}

    for hit in hits:
        about = hit_chains(hit, chains)
        if not about:
            continue
        snippet = f"{hit.get('title', '')}\n{hit.get('snippet', '')}"
        if fuel:
            for cid, value in station_prices(snippet, chains).items():
                fuel_pick(cid, value, hit["url"], "in a search result")
            if len(about) == 1 and about[0]["id"] not in found:
                value = priceextract.fuel_prices(snippet).get("regular")
                if value:
                    fuel_pick(about[0]["id"], value, hit["url"], "in a search result")
            if opened < max_pages and hit.get("url") and any(c["id"] not in found for c in about):
                page = _open(hit["url"], "area fuel")
                opened += 1
                if page:
                    text = _full_text(page)
                    for cid, value in station_prices(text, chains).items():
                        fuel_pick(cid, value, hit["url"], "on a page listing stations")
                    if len(about) == 1 and about[0]["id"] not in found:
                        value = priceextract.fuel_prices(text).get("regular")
                        if value:
                            fuel_pick(about[0]["id"], value, hit["url"], "on the page")
            continue
        if len(about) != 1:
            continue  # a result naming several stores cannot say whose price it shows
        chain = about[0]
        listings = webparse.rank_listings(priceextract.extract(snippet), name, others, item.get("unit"), MIN_MATCH_SCORE)
        if not listings and opened < max_pages and hit.get("url"):
            page = _open(hit["url"], "area price")
            opened += 1
            if page:
                listings = webparse.rank_listings(priceextract.extract(page["raw"]), name, others, item.get("unit"), MIN_MATCH_SCORE)
        candidates.setdefault(chain["id"], []).extend({**x, "source_url": hit["url"], "source_title": hit.get("title", "")} for x in listings)
    for chain_id, cands in candidates.items():
        picked = webparse.choose_price(cands)
        if picked:
            found[chain_id] = {**picked, "note": f"found by searching {area}: {picked['note']}"}
    return found


def deal_queries(chain: dict[str, Any], target_items: list[str]) -> list[tuple[str, str | None]]:
    """(search words, website or None) a deals lookup searches for, in order (after the weekly ad page, if set)."""
    site = websearch.site_domain(chain.get("website"))
    ad = " ".join(x for x in (chain["name"], "weekly ad sale this week", chain.get("zip") or chain["city"]) if x)
    queries = [(ad, site)] if site else []
    queries += [(ad, None)]
    queries += [(" ".join(x for x in (name, chain["name"], "sale price", chain.get("zip") or chain["city"]) if x), site) for name in target_items]
    return queries


def find_hours(loc: dict[str, Any], budget: "_Budget | None" = None) -> dict[str, Any] | None:
    """Look for one store location's opening hours on the web. Returns {hours, confidence, note, source_url} or None.

    In order, stopping once sure: the store's own page (set on the Stores page) read from its structured data,
    else by the model; then a search limited to the chain's website; then a general search.
    """
    chain = loc["chain_name"]
    best: dict[str, Any] | None = None

    def ask(text: str, url: str) -> None:
        nonlocal best
        if budget is not None and not budget.take():
            raise WebInfoError("Stopped: too many model requests for one lookup")
        excerpt = webparse.hours_excerpt(text, PAGE_CHARS_FOR_MODEL, webparse.address_anchors(_street(loc), _city(loc), _postal(loc)))
        answer = textllm.complete_json(webparse.HOURS_SYSTEM, webparse.hours_prompt(chain, loc.get("address"), excerpt))
        cleaned = webparse.clean_hours_answer(answer, text, chain)
        if cleaned and (best is None or cleaned["confidence"] > best["confidence"]):
            best = {**cleaned, "source_url": url}

    def sure() -> bool:
        return best is not None and best["confidence"] >= 0.85

    # 1. the store's own page: structured hours need no model
    if loc.get("page_url"):
        with webdebug.step("hours: store page"):
            page = _open(loc["page_url"], "store")
        if page:
            hours = priceextract.extract_hours(page["raw"])
            if hours:
                return {"hours": hours, "confidence": 0.95, "note": "From the store's own page", "source_url": loc["page_url"]}
            full = _full_text(page)
            if webparse.has_clock_times(full):
                ask(full, loc["page_url"])
        if sure():
            return best

    query = hours_query(loc)
    site = websearch.site_domain(loc.get("website"))
    # 2. the chain's own site, then 3. the whole web
    with webdebug.step("hours: search on the store's site"):
        site_hits = _search(query, 6, site, "hours") if site else []
    opened_on_site = False
    for hits in (site_hits, None):
        if hits is None:
            if opened_on_site:
                break  # the store's own pages were read: searching the whole web as well costs a search for little
            with webdebug.step("hours: web search"):
                hits = webparse.rank_hits(websearch.search(query, 6), chain)[:3]
        for hit in hits[:3]:
            if hit["url"] == loc.get("page_url"):
                continue
            snippet = f"{hit.get('title', '')}\n{hit.get('snippet', '')}"
            if webparse.has_clock_times(snippet) and webparse.mentions(snippet, chain):
                ask(snippet, hit["url"])
            if not sure() and hit["url"]:
                page = _open(hit["url"], "hours")
                if page:
                    if hits is site_hits:
                        opened_on_site = True
                    hours = priceextract.extract_hours(page["raw"]) if site and websearch._on_site(hit["url"], site) else None
                    if hours:
                        return {"hours": hours, "confidence": 0.9, "note": "From the store's website", "source_url": hit["url"]}
                    full = _full_text(page)
                    if webparse.has_clock_times(full):
                        ask(full, hit["url"])
            if sure():
                return best
    return best


def _save_hours(db: Session, location_id: str, found: dict[str, Any], has_hours: bool) -> str:
    """Store a suggestion (or apply it automatically). Returns the resulting status."""
    settings = get_settings()
    text = json.dumps(found["hours"])
    row = db.get(WebHours, location_id)
    if row is not None and row.status == "DISMISSED" and row.hours == text:
        return "DISMISSED"  # the person already said no to exactly this
    if row is None:
        row = WebHours(store_location_id=location_id, hours=text, status="SUGGESTED", fetched_at=datetime.utcnow())
        db.add(row)
    unchanged = row.hours == text and row.status == "APPLIED"
    row.hours, row.source_url, row.confidence, row.note = text, found["source_url"][:1000], found["confidence"], found.get("note")
    row.fetched_at = datetime.utcnow()
    if not unchanged:
        row.status = "SUGGESTED"
    if settings.web_auto_hours and not has_hours and found["confidence"] >= 0.8:
        location = db.get(StoreLocation, location_id)
        if location is not None and not location.opening_hours:
            location.opening_hours = text
            row.status = "APPLIED"
    db.commit()
    return row.status


def start_hours_job(home_id: str, location_ids: list[str] | None = None, force: bool = False) -> dict[str, Any]:
    """Look up opening hours for a home's store locations in the background."""
    if not websearch.configured():
        raise WebInfoError(NOT_SET_UP)
    existing = running_job("hours", home_id)
    if existing:
        return existing

    with get_db_session()() as db:
        every = trips.home_locations(db, home_id)
        grocery = grocery_chain_ids(db, home_id, every)
        # all stores: only the ones you shop for groceries at (a restaurant's hours do not matter to a trip);
        # a store picked by hand is always looked up
        locations = [l for l in every if l["address"] and ((l["id"] in location_ids) if location_ids else l["chain_id"] in grocery)]
        cutoff = datetime.utcnow() - timedelta(days=HOURS_FRESH_DAYS)
        fresh = {r.store_location_id for r in db.query(WebHours).all() if r.fetched_at and r.fetched_at > cutoff}
        todo = [l["id"] for l in locations if force or location_ids or (l["id"] not in fresh and not l.get("hours"))]
    if not locations:
        if any(l["address"] for l in every):
            raise WebInfoError("Your stores with an address are restaurants or places where only fuel, fees or dishes were bought, "
                               "which are not looked up. Use a single store's menu to look one up anyway.")
        raise WebInfoError("None of your stores has an address to look up. Add addresses on this page first.")
    if not todo:
        raise WebInfoError("Every store already has hours, or was checked recently. Use a single store's menu to check again.")

    job = _new_job("hours", home_id, len(todo))

    def work(j: dict[str, Any]) -> None:
        budget = _Budget(MAX_LLM_CALLS_PER_JOB)
        j["message"] = f"Looking up hours for {len(todo)} store{'s' if len(todo) != 1 else ''}…"

        def handle(location_id: str) -> bool:
            with get_db_session()() as db:
                loc = next((l for l in trips.home_locations(db, home_id) if l["id"] == location_id), None)
                if loc is None:
                    return False
                # OpenStreetMap's hours, when the nearby-store search found this store, before any web search
                from app.services import nearby  # imported here: nearby -> trips -> webinfo
                found = nearby.hours_for_location(db, location_id) or find_hours(loc, budget)
                if not found:
                    return False
                location = db.get(StoreLocation, location_id)
                _save_hours(db, location_id, found, bool(location and location.opening_hours))
                return True

        _run_parallel(j, todo, handle)

    _run_in_thread(job, work)
    return dict(job)


def list_hours(db: Session, home_id: str) -> list[dict[str, Any]]:
    """Suggestions for this home's stores, with the hours they have now."""
    by_id = {l["id"]: l for l in trips.home_locations(db, home_id)}
    out = []
    for row in db.query(WebHours).filter(WebHours.store_location_id.in_(list(by_id))).all() if by_id else []:
        out.append({
            "location_id": row.store_location_id, "label": by_id[row.store_location_id]["label"], "hours": json.loads(row.hours),
            "source_url": websearch.safe_url(row.source_url) or None, "source": urlparse(row.source_url or "").netloc, "confidence": row.confidence, "note": row.note,
            "status": row.status, "fetched_at": row.fetched_at.isoformat() + "Z" if row.fetched_at else None,
        })
    return out


def apply_hours(db: Session, location_id: str) -> dict[str, Any] | None:
    row = db.get(WebHours, location_id)
    location = db.get(StoreLocation, location_id)
    if row is None or location is None:
        return None
    location.opening_hours = row.hours
    row.status = "APPLIED"
    db.commit()
    return {"location_id": location_id, "status": row.status}


def dismiss_hours(db: Session, location_id: str) -> bool:
    row = db.get(WebHours, location_id)
    if row is None:
        return False
    row.status = "DISMISSED"
    db.commit()
    return True


# --------------------------------------------------------------------------- #
# Deals
# --------------------------------------------------------------------------- #

def _chains(db: Session, home_id: str, limit: int | None = MAX_CHAINS_PER_JOB, fuel_stations: bool = False) -> list[dict[str, Any]]:
    """Chains this home has bought from, then the ones it added from the nearby search, each with a town.

    Nearby-only chains come after the ones with receipts, so a long list of added stores can never push
    a store you actually shop at out of a lookup. Each chain counts once, however many stores it has.
    """
    chains: dict[str, dict[str, Any]] = {}
    locations = trips.home_locations(db, home_id)
    grocery, fuel = _store_kinds(db, home_id, locations)  # one read of the receipts for both
    if fuel_stations:  # the price check also prices fuel where you bought it (a petrol station that sells nothing else)
        grocery |= fuel
    for loc in sorted(locations, key=lambda l: bool(l.get("nearby"))):
        if loc["chain_id"] not in grocery:
            continue  # a restaurant, a car park: no groceries to price or deals to find there
        c = chains.setdefault(loc["chain_id"], {"id": loc["chain_id"], "name": loc["chain_name"], "city": None, "website": loc.get("website"),
                                                "search_url": loc.get("search_url"), "deals_url": loc.get("deals_url"),
                                                "street": None, "page_urls": [], "zip": None})
        c["city"] = c["city"] or _city(loc)
        c["street"] = c["street"] or _street(loc)
        c["zip"] = c["zip"] or _postal(loc)
        if loc.get("page_url"):
            c["page_urls"].append(loc["page_url"])
    home_zip = _home_zip(db, home_id)
    for c in chains.values():
        c["zip"] = c["zip"] or home_zip
    return list(chains.values())[:limit]


_POSTAL = re.compile(r"\b(\d{5})(?:-\d{4})?\b|\b([A-Z]\d[A-Z]\s?\d[A-Z]\d)\b|\b([A-Z]{1,2}\d[A-Z\d]?\s?\d[A-Z]{2})\b")


def postal_from_text(text: str | None) -> str | None:
    """The postal code in an address: a US ZIP (the last 5 digits), a Canadian or a UK postcode."""
    found = [m for m in _POSTAL.finditer((text or "").upper())]
    if not found:
        return None
    m = found[-1]
    return next(g for g in m.groups() if g)


def _postal(loc: dict[str, Any]) -> str | None:
    row = loc.get("_address_row")
    code = getattr(row, "postal_code", None) if row is not None else None
    return (str(code).strip()[:10] or None) if code else postal_from_text(loc.get("address"))


def _home_zip(db: Session, home_id: str) -> str | None:
    from app.db.models import Home
    home = db.get(Home, home_id) if db is not None else None
    return postal_from_text(getattr(home, "address", None)) if home is not None else None


def _store_kinds(db: Session, home_id: str, locations: list[dict[str, Any]]) -> tuple[set[str], set[str]]:
    """(grocery store chain ids, fuel station chain ids) of the home, from one read of its receipts.

    Grocery: not restaurants, and not stores where everything bought was fuel, a fee, a restaurant dish or clothing
    (a car park, a petrol station); stores added from the nearby search are grocery stores by how they were found.
    Fuel: stores where the home has bought fuel."""
    bought: dict[str, list[dict[str, Any]]] = {}
    fuel: set[str] = set()
    for r in load_observations(db, home_id):
        item = {"name": r.get("item_name") or "", "category": r.get("category"), "aliases": [r.get("description") or ""],
                "store_names": [r.get("chain_name") or ""]}
        bought.setdefault(r["chain_id"], []).append(item)
        if itemquery.fuel_grade(item):
            fuel.add(r["chain_id"])
    grocery = {l["chain_id"] for l in locations if l.get("nearby") or itemquery.is_grocery_chain(l["chain_name"], bought.get(l["chain_id"]))}
    return grocery, fuel


def grocery_chain_ids(db: Session, home_id: str, locations: list[dict[str, Any]] | None = None) -> set[str]:
    """The home's stores worth looking items up at (see ``_store_kinds``)."""
    return _store_kinds(db, home_id, locations if locations is not None else trips.home_locations(db, home_id))[0]


def _match_items(db: Session, home_id: str) -> list[dict[str, Any]]:
    items = trips.available_items(db, home_id, None, 300)
    ids = [i["id"] for i in items]
    aliases: dict[str, list[str]] = {}
    if ids:
        for a in db.query(CommonItemAlias).filter(CommonItemAlias.common_item_id.in_(ids)).all():
            aliases.setdefault(a.common_item_id, []).append(a.alias)
    return describe_items([{**i, "aliases": aliases.get(i["id"], [])} for i in items])


def describe_items(out: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Add to each item what to search for and match against, or why it is not looked up (see ``itemquery``)."""
    for i in out:
        # what to search for (cleaned, spelled right, with the brand from its receipts) and what to match
        # listings against (the same without the brand); or why it is not looked up at all
        context = itemquery.context_words(i, out)
        i["search_name"] = itemquery.search_name(i, context=context)
        i["plain_name"] = itemquery.search_name(i, with_brand=False, context=context)
        i["skip"] = itemquery.skip_reason(i)
        i["fuel"] = itemquery.fuel_grade(i)  # fuel is priced per gallon by grade, on its own path
    return out


def _deal_sources(chain: dict[str, Any], target_items: list[str]):
    """Yield (raw, text, url, title) for pages that may list this chain's sales, lazily (pages are only fetched
    while the caller still wants more): its weekly ad page if set, then searches limited to its website, then
    general searches for its weekly ad and each item of interest."""
    seen: set[str] = set()

    def page_of(url: str, title: str, snippet: str):
        page = _open(url, "deals") if url else None
        text, raw = (page["text"], page["raw"]) if page else ("", "")
        text, raw = text or snippet, raw or snippet
        return (raw, text, url, title) if text.strip() else None

    if chain.get("deals_url"):
        seen.add(chain["deals_url"])
        found = page_of(chain["deals_url"], f"{chain['name']} weekly ad", "")
        if found:
            yield found
    opened_on_site = False
    for query, limit_to in deal_queries(chain, target_items):
        if not limit_to and opened_on_site and "weekly ad" in query:
            continue  # the store's own weekly ad pages were read: the whole-web search for it is left out
        hits = _search(query, 5, limit_to, "deals") if limit_to else webparse.rank_hits(websearch.search(query, 5), chain["name"])
        for hit in hits[:2]:
            if hit["url"] in seen:
                continue
            seen.add(hit["url"])
            found = page_of(hit["url"], hit.get("title", ""), f"{hit.get('title', '')}\n{hit.get('snippet', '')}")
            if found:
                if limit_to and found[0] != f"{hit.get('title', '')}\n{hit.get('snippet', '')}":
                    opened_on_site = True  # a real page, not just the search snippet
                yield found


def start_deals_job(home_id: str) -> dict[str, Any]:
    """Look for promotions on your items at the stores you shop at, in the background."""
    if not websearch.configured():
        raise WebInfoError(NOT_SET_UP)
    existing = running_job("deals", home_id)
    if existing:
        return existing
    with get_db_session()() as db:
        chains = _chains(db, home_id)
    if not chains:
        raise WebInfoError("Approve a receipt first, so there are stores to look up.")
    job = _new_job("deals", home_id, len(chains))

    def work(j: dict[str, Any]) -> None:
        budget = _Budget(MAX_LLM_CALLS_PER_JOB)
        today = date.today()
        with get_db_session()() as db:
            items = _match_items(db, home_id)
            targets = {t.common_item_id for t in db.query(PriceTarget).filter(PriceTarget.home_id == home_id, PriceTarget.active == 1).all()}
            names = {i["id"]: i.get("search_name") or i["name"] for i in items if not i.get("skip")}
        if not items:
            raise WebInfoError("There are no items with prices yet to look deals up for.")
        target_names = [names[t] for t in targets if t in names][:3]
        j["message"] = f"Looking for deals at {len(chains)} store{'s' if len(chains) != 1 else ''}…"

        def handle(chain: dict[str, Any]) -> bool:
            found: list[dict[str, Any]] = []
            chain_calls = 0
            for raw, text, url, title in _deal_sources(chain, target_names):
                # 1. sale prices read straight from the page: listings marked on sale (an end date, a
                #    "was" price, a price below the regular one) that have not ended
                deals = [
                    {"product": x["product"], "price": x["price"], "unit": x["unit"], "valid_from": None,
                     "valid_to": x["valid_to"], "conditions": None, "method": x["method"]}
                    for x in priceextract.extract(raw)
                    if x["on_sale"] and not (x["valid_to"] and x["valid_to"] < today.isoformat())
                ]
                matches = webparse.match_products([d["product"] for d in deals], items, MIN_MATCH_SCORE)
                page_found = [{**d, "item_id": m["id"], "source_url": url, "source_title": title} for d, m in zip(deals, matches) if m]
                # 2. only when that finds nothing on this page, ask the model to read it
                if not page_found and chain_calls < MAX_LLM_CALLS_PER_CHAIN and budget.take():
                    chain_calls += 1
                    answer = textllm.complete_json(webparse.DEALS_SYSTEM, webparse.deals_prompt(chain["name"], chain["city"], today, text[:PAGE_CHARS_FOR_MODEL]), 3000)
                    deals = [{**d, "method": "model"} for d in webparse.clean_deals(answer, today)]
                    matches = webparse.match_products([d["product"] for d in deals], items, MIN_MATCH_SCORE)
                    page_found = [{**d, "item_id": m["id"], "source_url": url, "source_title": title} for d, m in zip(deals, matches) if m]
                found.extend(page_found)
            if not found:
                return False
            with get_db_session()() as db:
                saved = _save_deals(db, home_id, chain["id"], found)
            return saved > 0

        _run_parallel(j, chains, handle)

        with get_db_session()() as db:
            try:
                from app.services import deals as deals_service  # imported here: deals -> alerts -> webinfo
                deals_service.refresh_home(db, home_id)  # recomputes the results and raises alerts for the new deals
            except Exception:  # noqa: BLE001 - alerts must never fail a lookup
                logger.exception("Could not raise alerts for new deals")

    _run_in_thread(job, work)
    return dict(job)


def _price_sources(query: str, chain_name: str, limit: int = 2, site: str | None = None, opened: list | None = None):
    """Yield (raw, text, url, title) for the best pages for a product-price search, lazily (pages are only fetched
    while wanted). ``opened`` collects the addresses of pages that could be opened."""
    hits = _search(query, 5, site, "price") if site else webparse.rank_hits(websearch.search(query, 5), chain_name)
    for hit in hits[:limit]:
        page = _open(hit["url"], "price") if hit["url"] else None
        if page is not None and opened is not None:
            opened.append(hit["url"])
        text, raw = (page["text"], page["raw"]) if page else ("", "")
        snippet = f"{hit.get('title', '')}\n{hit.get('snippet', '')}"
        text, raw = text or snippet, raw or snippet
        if text.strip():
            yield raw, text, hit["url"], hit.get("title", "")


def _price_pages(item: dict[str, Any], chain: dict[str, Any]):
    """Pages to read an item's price at a chain from, in order: the chain's product search page (opened
    directly, no search), a search limited to its website, then a general search."""
    q = price_queries(item, chain)
    if q["store_search_url"]:
        page = _open(q["store_search_url"], "store search")
        if page:
            yield page["raw"], page["text"], q["store_search_url"], f"{chain['name']} search: {item['name']}"
    if q["site"]:
        opened: list[str] = []
        yield from _price_sources(q["site_query"], chain["name"], site=q["site"], opened=opened)
        if opened:
            # the store's own pages were read and had no price for it: a search of the whole web mostly finds
            # the same site again or shops elsewhere, and costs a search, so it is left out
            return
    yield from _price_sources(q["web_query"], chain["name"])


def start_prices_job(
    home_id: str, item_ids: list[str] | None = None, chain_ids: list[str] | None = None, force: bool = False,
    all_stores: bool = False,
) -> dict[str, Any]:
    """Look up the current price of items on the websites of the stores you buy them at, in the background.

    With no ``item_ids`` the items you buy most are used. Only stores you have bought the item from are
    tried (optionally limited to ``chain_ids``). A price is kept only if a listing clearly is that item
    and its price can be compared in the item's own unit.

    An item-at-store pair already checked online within ``WEB_PRICE_FRESHNESS_HOURS`` is skipped and the
    price already on record is used instead, unless ``force`` is set.

    ``all_stores`` (the trip planner): every item is also tried at every other store of the home, since the planner
    compares each item at all of them; the stores it was bought at still come first.
    """
    if not websearch.configured():
        raise WebInfoError(NOT_SET_UP)
    existing = running_job("prices", home_id)
    if existing:
        return existing

    settings = get_settings()
    with get_db_session()() as db:
        items = _match_items(db, home_id)
        chains = {c["id"]: c for c in _chains(db, home_id, None, fuel_stations=True)}
        nearby_chain_ids = {l["chain_id"] for l in trips.home_locations(db, home_id) if l["nearby"]}
        # fuel, fees, restaurant dishes and clothing have no online price to compare
        wanted = [i for i in items if (not item_ids or i["id"] in item_ids) and not i.get("skip")][: (len(item_ids) if item_ids else DEFAULT_PRICE_ITEMS)]
        observations = load_observations(db, home_id, item_ids=[i["id"] for i in wanted]) if wanted else []
        fresh_pairs: set[tuple[str, str]] = set()
        if not force:
            cutoff = datetime.utcnow() - timedelta(hours=settings.web_price_freshness_hours)
            fresh_pairs = {
                (row.common_item_id, row.store_chain_id) for row in
                db.query(WebDeal).filter(WebDeal.home_id == home_id, WebDeal.kind == "PRICE", WebDeal.fetched_at > cutoff).all()
            }

    bought_at: dict[str, set[str]] = {}
    for r in observations:
        bought_at.setdefault(r["item_id"], set()).add(r["chain_id"])
    # Stores added from the nearby search are tried for every item: they have no receipts to go by.
    nearby_chains = sorted(nearby_chain_ids - {c for cs in bought_at.values() for c in cs}) if nearby_chain_ids else []
    # stores you buy the item at first, for every item, then the added ones, so the lookup limit never
    # crowds out the prices you already rely on
    all_pairs = [(i, chains[c]) for i in wanted for c in sorted(bought_at.get(i["id"], ()))
                 if c in chains and (not chain_ids or c in chain_ids)]
    # fuel only where you bought it (a grocery store added nearby, or another store, may have no pumps)
    all_pairs += [(i, chains[c]) for i in wanted for c in nearby_chains if c in chains and (not chain_ids or c in chain_ids) and not i.get("fuel")]
    if all_stores:
        # every other store too, one round per store across all items, so each item gets as many stores as the limit allows
        have = {(i["id"], c["id"]) for i, c in all_pairs}
        for chain_id in chains:
            for i in wanted:
                if (i["id"], chain_id) not in have and (not chain_ids or chain_id in chain_ids) and not i.get("fuel"):
                    all_pairs.append((i, chains[chain_id]))
    with get_db_session()() as db_ignored:
        ignored = ignored_pairs(db_ignored, home_id)
    all_pairs = [(i, c) for i, c in all_pairs if (i["id"], c["id"]) not in ignored]  # online price not wanted there
    if not all_pairs:
        raise WebInfoError("There is nothing to look up: no item you chose has been bought at a store with a known name yet.")

    pairs = [(i, c) for i, c in all_pairs if (i["id"], c["id"]) not in fresh_pairs][:MAX_PRICE_LOOKUPS_TRIP if all_stores else MAX_PRICE_LOOKUPS]
    skipped = len(all_pairs) - len(pairs)
    if not pairs:
        raise WebInfoError(
            f"Everything was already checked online within the last {settings.web_price_freshness_hours} hours; "
            "using those prices. Check a single store from its menu to look again sooner."
        )

    job = _new_job("prices", home_id, len(pairs))
    job["skipped"] = skipped

    def work(j: dict[str, Any]) -> None:
        budget = _Budget(MAX_LLM_CALLS_PRICES)
        n_items, n_stores = len({i["id"] for i, _ in pairs}), len({c["id"] for _, c in pairs})
        j["items"], j["stores"] = n_items, n_stores
        j["message"] = f"Checking {n_items} item{'s' if n_items != 1 else ''} at {n_stores} store{'s' if n_stores != 1 else ''}…"

        def handle(pair: tuple[dict[str, Any], dict[str, Any]]) -> bool:
            item, chain = pair
            j["message"] = f"{item.get('plain_name') or item['name']} at {chain['name']}"
            if item.get("fuel"):
                picked = find_fuel_price(item, chain, budget)
                if not picked:
                    return False
                with get_db_session()() as db:
                    _save_price(db, home_id, chain["id"], item["id"], picked)
                return True
            name, others = item.get("plain_name") or item["name"], [item["name"], *(item.get("aliases") or [])]
            candidates: list[dict[str, Any]] = []
            pages_after_first = 0
            for raw, text, url, title in _price_pages(item, chain):
                # 1. every matching listing on the page (read straight from it: no model needed)
                found = webparse.rank_listings(priceextract.extract(raw), name, others, item.get("unit"), MIN_MATCH_SCORE)
                # 2. only when the page gives none, ask the model to read it
                picked = found[0] if found else None
                if not picked and budget.take():
                    # the parts of the whole page that mention the item, not just its start (menus come first)
                    words = [w for w in re.findall(r"[A-Za-z]{3,}", " ".join([item.get("plain_name") or item["name"], item["name"], *(item.get("aliases") or [])]))]
                    excerpt = webparse.text_around(priceextract.page_text(raw) or text, PAGE_CHARS_FOR_MODEL, words)
                    answer = textllm.complete_json(webparse.PRICES_SYSTEM, webparse.prices_prompt(chain["name"], chain["city"], item.get("plain_name") or item["name"], excerpt), 1500)
                    listings = [{**x, "method": "model"} for x in webparse.clean_prices(answer)]
                    found = webparse.rank_listings(listings, name, others, item.get("unit"), MIN_MATCH_SCORE)
                candidates += [{**c, "source_url": url, "source_title": title} for c in found]
                # one more of the store's pages after the first with a match, to compare listings; no more searches
                if candidates:
                    pages_after_first += 1
                    if pages_after_first > 1:
                        break
            picked = webparse.choose_price(candidates)
            if not picked:
                return False
            with get_db_session()() as db:
                _save_price(db, home_id, chain["id"], item["id"], picked)
            return True

        # 1. one search per item in your ZIP code, its results filed under your stores (fewer searches: a result
        #    about one store prices it; results naming Costco and Sam's Club price both)
        by_item: dict[str, tuple[dict[str, Any], list[dict[str, Any]]]] = {}
        for item, chain in pairs:
            by_item.setdefault(item["id"], (item, []))[1].append(chain)
        done: set[tuple[str, str]] = set()
        for item, item_chains in by_item.values():
            if len(item_chains) < (2 if item.get("fuel") else 3):
                # few stores: their own searches cost about as much and are more precise (area fuel listings
                # name most stations, so for fuel one area search pays off from two stations)
                continue
            j["message"] = f"Searching the area for {item.get('plain_name') or item['name']}…"
            try:
                found = area_search(item, item_chains, budget)
            except websearch.SearchBlocked:
                raise
            except (websearch.WebSearchError, textllm.TextLLMError) as e:
                logger.info("Area search for %s failed: %s", item["name"], e)
                continue
            for chain_id, picked in found.items():
                with get_db_session()() as db:
                    _save_price(db, home_id, chain_id, item["id"], picked)
                done.add((item["id"], chain_id))
        j["done"] += len(done)
        j["results"] += len(done)
        j["area_found"] = len(done)
        # 2. each store still without a price, searched on its own
        _run_parallel(j, [(i, c) for i, c in pairs if (i["id"], c["id"]) not in done], handle)

    _run_in_thread(job, work)
    return dict(job)


def _save_price(db: Session, home_id: str, chain_id: str, item_id: str, picked: dict[str, Any]) -> None:
    """Record a shelf price found online. Every check is kept (for PRICE_HISTORY_DAYS) so prices can be
    shown by the date they were checked; the planner and comparisons use only the latest one."""
    db.add(WebDeal(
        home_id=home_id, store_chain_id=chain_id, common_item_id=item_id, product=picked["product"], price=picked["price"], unit=picked["unit"],
        conditions="; ".join(x for x in ("on sale" if picked.get("on_sale") else None, picked.get("note")) if x)[:500] or None,
        source_url=(picked.get("source_url") or "")[:1000],
        source_title=(picked.get("source_title") or "")[:300], kind="PRICE", method=picked.get("method"), fetched_at=datetime.utcnow()))
    db.commit()


def _save_deals(db: Session, home_id: str, chain_id: str, found: list[dict[str, Any]]) -> int:
    """Replace this chain's deals with the newly found ones (deduplicated). Returns how many were kept."""
    now = datetime.utcnow()
    seen, rows = set(), []
    for d in found:
        key = (d["item_id"], d["product"].lower(), d["price"], d["valid_to"])
        if key in seen:
            continue
        seen.add(key)
        rows.append(WebDeal(
            home_id=home_id, store_chain_id=chain_id, common_item_id=d["item_id"], product=d["product"], price=d["price"], unit=d["unit"],
            valid_from=d["valid_from"], valid_to=d["valid_to"], conditions=d["conditions"], source_url=(d["source_url"] or "")[:1000],
            source_title=(d["source_title"] or "")[:300], method=d.get("method"), fetched_at=now))
    db.query(WebDeal).filter(WebDeal.home_id == home_id, WebDeal.store_chain_id == chain_id, WebDeal.kind == "DEAL").delete(synchronize_session=False)
    db.add_all(rows[:80])
    db.commit()
    return min(len(rows), 80)


def purge_old_deals(db: Session) -> int:
    """Drop deals that have ended, deals with no end date that are old, and shelf prices older than a week."""
    today = date.today().isoformat()
    cutoff = datetime.utcnow() - timedelta(days=DEAL_MAX_AGE_DAYS)
    n = db.query(WebDeal).filter(WebDeal.kind == "DEAL", WebDeal.valid_to.isnot(None), WebDeal.valid_to < today).delete(synchronize_session=False)
    n += db.query(WebDeal).filter(WebDeal.kind == "DEAL", WebDeal.valid_to.is_(None), WebDeal.fetched_at < cutoff).delete(synchronize_session=False)
    n += db.query(WebDeal).filter(WebDeal.kind == "PRICE", WebDeal.fetched_at < datetime.utcnow() - timedelta(days=PRICE_HISTORY_DAYS)).delete(synchronize_session=False)
    db.commit()
    return n


def deal_rows(db: Session, home_id: str) -> list[dict[str, Any]]:
    """Current deals, each compared with what you usually pay at that store."""
    purge_old_deals(db)
    rows = db.query(WebDeal).filter(WebDeal.home_id == home_id).all()
    # Shelf prices: only the latest check of each item at each store counts, and only while fresh.
    trust_after = datetime.utcnow() - timedelta(days=PRICE_MAX_AGE_DAYS)
    latest_price: dict[tuple[str, str], WebDeal] = {}
    for d in rows:
        if d.kind == "PRICE" and d.fetched_at and d.fetched_at > trust_after:
            key = (d.common_item_id, d.store_chain_id)
            if key not in latest_price or d.fetched_at > latest_price[key].fetched_at:
                latest_price[key] = d
    deals = [d for d in rows if d.kind != "PRICE"] + list(latest_price.values())
    if not deals:
        return []
    item_ids = list({d.common_item_id for d in deals})
    observations = load_observations(db, home_id, item_ids=item_ids)
    prepared = analytics.prepare_price_rows(observations)
    units = {r["item_id"]: r["cunit"] for r in prepared if r.get("cunit")}
    names = {r["item_id"]: r["item_name"] for r in prepared}
    latest: dict[tuple[str, str], float] = {}
    for r in sorted((r for r in prepared if r["price"] is not None), key=lambda r: r["date"]):
        latest[(r["item_id"], r["chain_id"])] = r["price"]
    chain_names = {c.id: c.name for c in db.query(StoreChain).filter(StoreChain.id.in_({d.store_chain_id for d in deals})).all()}

    out = []
    for d in deals:
        unit = units.get(d.common_item_id)
        price = webparse.normalize_web_price(d.product, d.price, d.unit, unit)
        usual = latest.get((d.common_item_id, d.store_chain_id))
        saving = round((usual - price) / usual * 100, 1) if usual and price is not None and usual > 0 else None
        out.append({
            "id": d.id, "kind": d.kind or "DEAL", "item_id": d.common_item_id, "item_name": names.get(d.common_item_id), "chain_id": d.store_chain_id,
            "chain": chain_names.get(d.store_chain_id), "product": d.product, "price": d.price, "unit": d.unit,
            "compare_price": round(price, 4) if price is not None else None, "compare_unit": unit,
            "usual_price": round(usual, 4) if usual else None, "saving_pct": saving,
            "valid_to": d.valid_to, "conditions": d.conditions, "source_url": websearch.safe_url(d.source_url) or None, "source": urlparse(d.source_url or "").netloc,
            "method": d.method, "fetched_at": d.fetched_at.isoformat() + "Z" if d.fetched_at else None,
        })
    out.sort(key=lambda r: (-(r["saving_pct"] if r["saving_pct"] is not None else -999), r["item_name"] or ""))
    return out


def ignored_pairs(db: Session, home_id: str) -> set[tuple[str, str]]:
    """(item id, chain id) whose online prices this home chose not to use."""
    from app.db.models import OnlinePriceIgnore
    return {(r.common_item_id, r.store_chain_id) for r in db.query(OnlinePriceIgnore).filter(OnlinePriceIgnore.home_id == home_id).all()}


def set_ignored(db: Session, home_id: str, item_id: str, chain_id: str, ignored: bool) -> None:
    """Stop (or start again) using the online price of an item at a store chain."""
    from app.db.models import OnlinePriceIgnore
    row = (db.query(OnlinePriceIgnore).filter(OnlinePriceIgnore.home_id == home_id, OnlinePriceIgnore.common_item_id == item_id,
                                              OnlinePriceIgnore.store_chain_id == chain_id).first())
    if ignored and row is None:
        db.add(OnlinePriceIgnore(home_id=home_id, common_item_id=item_id, store_chain_id=chain_id, created_at=datetime.utcnow()))
    elif not ignored and row is not None:
        db.delete(row)
    db.commit()


def deal_offers(db: Session, home_id: str, item_ids: list[str]) -> list[dict[str, Any]]:
    """Deals and shelf prices for the planner: in each item's own comparison unit, only those that can be compared, and
    not those the home chose to ignore (that item at that store uses its receipt price)."""
    wanted = set(item_ids)
    ignored = ignored_pairs(db, home_id)
    return [
        {"id": r["id"], "kind": r["kind"], "item_id": r["item_id"], "item_name": r["item_name"], "chain_id": r["chain_id"], "chain": r["chain"],
         "product": r["product"], "price": r["compare_price"], "unit": r["compare_unit"], "listed_price": r["price"], "listed_unit": r["unit"],
         "valid_to": r["valid_to"], "source_url": r["source_url"], "source": r["source"], "method": r["method"],
         "fetched_at": r["fetched_at"], "date": (r["fetched_at"] or "")[:10], "conditions": r.get("conditions")}
        for r in deal_rows(db, home_id) if r["item_id"] in wanted and r["compare_price"] is not None and r["compare_unit"]
        and (r["item_id"], r["chain_id"]) not in ignored
    ]


def price_history(db: Session, home_id: str, days: int = PRICE_HISTORY_DAYS) -> list[dict[str, Any]]:
    """Every online price check in the last ``days`` days, grouped by the date it was checked (newest first).

    Returns [{date, checks: [{item_name, chain, product, price, unit, method, source, source_url, time, latest}]}].
    ``latest`` marks the check that is currently used for that item at that store.
    """
    purge_old_deals(db)
    since = datetime.utcnow() - timedelta(days=days)
    rows = db.query(WebDeal).filter(WebDeal.home_id == home_id, WebDeal.kind == "PRICE", WebDeal.fetched_at > since).all()
    if not rows:
        return []
    from app.db.models import CommonItem  # local: keeps this module's import list short
    names = {i.id: i.name for i in db.query(CommonItem).filter(CommonItem.id.in_({r.common_item_id for r in rows})).all()}
    chains = {c.id: c.name for c in db.query(StoreChain).filter(StoreChain.id.in_({r.store_chain_id for r in rows})).all()}
    newest: dict[tuple[str, str], datetime] = {}
    for r in rows:
        key = (r.common_item_id, r.store_chain_id)
        newest[key] = max(newest.get(key, r.fetched_at), r.fetched_at)
    trust_after = datetime.utcnow() - timedelta(days=PRICE_MAX_AGE_DAYS)
    by_day: dict[str, list[dict[str, Any]]] = {}
    for r in sorted(rows, key=lambda r: r.fetched_at, reverse=True):
        key = (r.common_item_id, r.store_chain_id)
        by_day.setdefault(r.fetched_at.date().isoformat(), []).append({
            "item_id": r.common_item_id, "item_name": names.get(r.common_item_id, r.product), "chain": chains.get(r.store_chain_id),
            "product": r.product, "price": r.price, "unit": r.unit, "method": r.method or "model", "conditions": r.conditions,
            "source": urlparse(r.source_url or "").netloc, "source_url": websearch.safe_url(r.source_url) or None, "time": r.fetched_at.isoformat() + "Z",
            "latest": r.fetched_at == newest[key] and r.fetched_at > trust_after,
        })
    return [{"date": day, "checks": checks} for day, checks in by_day.items()]


# --------------------------------------------------------------------------- #
# Scheduled refresh
# --------------------------------------------------------------------------- #

_last_deals_run: dict[str, datetime] = {}
# The daily job runs at the same hour each day, so "every 3 days" must count a run a few minutes short
# of exactly 3 days ago as due, or it would only happen every 4th day.
_SLACK = timedelta(hours=2)


@reqcache.scoped
def run_scheduled() -> None:
    """Called by the daily job: look for deals for homes that are due, and for stores whose hours have never been checked.

    Each step stands on its own: one with nothing to do (no stores yet, every price already fresh) must not
    stop the others. Deal lookups are due by when deals were last looked for, not by any online price
    check, or checking prices every day would keep deals from ever being looked up.
    """
    settings = get_settings()
    if not websearch.configured():
        return
    from app.db.models import Home
    with get_db_session()() as db:
        homes = [h.id for h in db.query(Home).all()]

    def newest(home_id: str, kind: str) -> datetime | None:
        with get_db_session()() as db:
            row = (db.query(WebDeal.fetched_at).filter(WebDeal.home_id == home_id, WebDeal.kind == kind)
                   .order_by(WebDeal.fetched_at.desc()).first())
        return row[0] if row else None

    def step(home_id: str, start: Callable[[], dict[str, Any]]) -> None:
        try:
            _wait(start()["id"])
        except WebInfoError:
            pass  # nothing to do for this home right now
        except Exception:  # noqa: BLE001
            logger.exception("Scheduled web lookup failed for home %s", home_id)

    for home_id in homes:
        if settings.web_deals_days > 0:
            # a lookup that found nothing saves no rows, so the time it ran is also remembered here
            last = max((t for t in (newest(home_id, "DEAL"), _last_deals_run.get(home_id)) if t), default=None)
            if last is None or datetime.utcnow() - last > timedelta(days=settings.web_deals_days) - _SLACK:
                _last_deals_run[home_id] = datetime.utcnow()
                step(home_id, lambda: start_deals_job(home_id))
        if settings.web_prices_days > 0:
            last = newest(home_id, "PRICE")
            if last is None or datetime.utcnow() - last > timedelta(days=settings.web_prices_days) - _SLACK:
                step(home_id, lambda: start_prices_job(home_id))
        step(home_id, lambda: start_hours_job(home_id))


def _wait(job_id: str, timeout: float = 3600.0) -> None:
    start = time.time()
    while time.time() - start < timeout:
        job = get_job(job_id)
        if job is None or job["finished"]:
            return
        time.sleep(2)
