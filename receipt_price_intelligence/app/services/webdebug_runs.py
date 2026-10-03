"""The *Try it* tools of the Web debug page: one search, one page, or one hours, price or deals lookup run
end to end exactly as the real lookups do, but with nothing saved. Each returns what it decided at every
step, plus ``trace``: every request it made and what came back (see ``webdebug``).
"""

from __future__ import annotations

import re
import time
from typing import Any

from sqlalchemy.orm import Session

from app.config import get_settings
from app.services import priceextract, textllm, trips, webcache, webdebug, webinfo, webparse, websearch

MAX_DEAL_PAGES = 3


def setup() -> dict[str, Any]:
    """Which provider is used and its settings (no secrets)."""
    s = get_settings()
    return {
        "provider": websearch.provider(), "hosted": websearch.provider() in websearch.HOSTED, "configured": websearch.configured(),
        "server": websearch.describe(), "provider_name": websearch.PROVIDERS[websearch.provider()],
        "search_url": s.web_search_url or None, "api_key_set": bool(s.web_search_api_key), "token_set": bool(s.web_search_token),
        "query_field": s.web_query_field, "url_field": s.web_url_field, "timeout_seconds": s.web_search_timeout_seconds,
        "concurrency": s.web_search_concurrency, "model": s.model_name, "model_format": s.model_api_format,
        "pages_opened_by": "your search server (/fetch)" if websearch.provider() == "custom" else "the app itself",
        "paused": websearch.paused(),
        "cache": webcache.stats(), "min_interval_seconds": s.web_search_min_interval_seconds,
    }


def options(db: Session, home_id: str) -> dict[str, Any]:
    """Stores, locations and items to pick from on the page."""
    locations = trips.home_locations(db, home_id)
    chains = webinfo._chains(db, home_id, None)
    items = webinfo._match_items(db, home_id)
    return {
        "locations": [{"id": l["id"], "label": l["label"], "address": l["address"], "page_url": l.get("page_url"),
                       "website": l.get("website")} for l in locations],
        "chains": [{"id": c["id"], "name": c["name"], "city": c["city"], "website": c.get("website"), "search_url": c.get("search_url"),
                    "deals_url": c.get("deals_url")} for c in chains],
        "items": [{"id": i["id"], "name": i["name"], "unit": i.get("unit")} for i in items],
    }


def run_search(query: str, site: str | None, limit: int, use_cache: bool = True) -> dict[str, Any]:
    started = time.time()
    with webdebug.capture() as trace:
        try:
            hits, error = websearch.search(query, limit, site=site or None, use_cache=use_cache), None
        except websearch.WebSearchError as e:
            hits, error = [], str(e)
    sent = query if not site or websearch.provider() in websearch.HOSTED else f"{query} site:{site}"
    return {"provider": websearch.provider(), "query_sent": sent, "site": site or None, "hits": hits, "error": error,
            "ms": round((time.time() - started) * 1000), "trace": trace}


def run_fetch(url: str, use_cache: bool = True) -> dict[str, Any]:
    with webdebug.capture() as trace:
        try:
            page, error = websearch.fetch(url, use_cache=use_cache), None
        except websearch.WebSearchError as e:
            page, error = None, str(e)
    if page is None:
        return {"error": error, "trace": trace}
    full = priceextract.page_text(page["raw"]) or page["text"]
    return {
        "error": None, "raw_chars": len(page["raw"]), "text_chars": len(full), "text_preview": full[:3000],
        "structured_prices": priceextract.extract(page["raw"])[:25], "structured_hours": priceextract.extract_hours(page["raw"]),
        "has_clock_times": webparse.has_clock_times(full), "hours_excerpt": webparse.hours_excerpt(full, webinfo.PAGE_CHARS_FOR_MODEL)[:3000],
        "trace": trace,
    }


def run_hours(db: Session, home_id: str, location_id: str) -> dict[str, Any]:
    loc = next((l for l in trips.home_locations(db, home_id) if l["id"] == location_id), None)
    if loc is None:
        return {"error": "Location not found"}
    from app.services import nearby  # imported here: nearby -> trips -> webinfo

    osm = nearby.hours_for_location(db, location_id)
    with webdebug.capture() as trace:
        try:
            result, error = (osm, None) if osm else (webinfo.find_hours(loc, webinfo._Budget(webinfo.MAX_LLM_CALLS_PER_JOB)), None)
        except (websearch.WebSearchError, textllm.TextLLMError, webinfo.WebInfoError) as e:
            result, error = None, str(e)
    return {"location": {"label": loc["label"], "address": loc["address"], "page_url": loc.get("page_url"), "website": loc.get("website")},
            "from_openstreetmap": bool(osm), "result": result, "error": error, "trace": trace}


def run_price(db: Session, home_id: str, item_id: str, chain_id: str) -> dict[str, Any]:
    item = next((i for i in webinfo._match_items(db, home_id) if i["id"] == item_id), None)
    chain = next((c for c in webinfo._chains(db, home_id, None, fuel_stations=True) if c["id"] == chain_id), None)
    if item is None or chain is None:
        return {"error": "Item or store not found"}
    pages: list[dict[str, Any]] = []
    picked, error = None, None
    if item.get("fuel"):
        with webdebug.capture() as trace:
            try:
                picked = webinfo.find_fuel_price(item, chain, webinfo._Budget(2))
            except (websearch.WebSearchError, textllm.TextLLMError, webinfo.WebInfoError) as e:
                error = str(e)
        pages = [{"url": picked["source_url"] if picked else None, "title": f"{item['fuel']} gas price",
                  "listings_on_page": [], "listing_count": 1 if picked else 0, "model_used": bool(picked and picked.get("method") == "model"), "picked": picked}]
        return {"item": item, "chain": chain, "pages": pages, "picked": picked, "error": error, "saved": False, "trace": trace}
    with webdebug.capture() as trace:
        try:
            for raw, text, url, title in webinfo._price_pages(item, chain):
                listings = priceextract.extract(raw)
                page = {"url": url, "title": title, "listings_on_page": listings[:15], "listing_count": len(listings), "model_used": False}
                picked = webparse.pick_price(listings, item.get("plain_name") or item["name"], [item["name"], *(item.get("aliases") or [])], item.get("unit"), webinfo.MIN_MATCH_SCORE)
                if not picked:
                    words = re.findall(r"[A-Za-z]{3,}", " ".join([item.get("plain_name") or item["name"], item["name"], *(item.get("aliases") or [])]))
                    excerpt = webparse.text_around(priceextract.page_text(raw) or text, webinfo.PAGE_CHARS_FOR_MODEL, words)
                    answer = textllm.complete_json(webparse.PRICES_SYSTEM, webparse.prices_prompt(chain["name"], chain["city"], item.get("plain_name") or item["name"], excerpt), 1500)
                    model_listings = [{**x, "method": "model"} for x in webparse.clean_prices(answer)]
                    page.update(model_used=True, model_listings=model_listings[:15])
                    picked = webparse.pick_price(model_listings, item.get("plain_name") or item["name"], [item["name"], *(item.get("aliases") or [])], item.get("unit"), webinfo.MIN_MATCH_SCORE)
                page["picked"] = picked
                pages.append(page)
                if picked:
                    picked = {**picked, "source_url": url}
                    break
        except (websearch.WebSearchError, textllm.TextLLMError, webinfo.WebInfoError) as e:
            error = str(e)
    return {"item": item, "chain": chain, "pages": pages, "picked": picked, "error": error, "saved": False, "trace": trace}


def run_deals(db: Session, home_id: str, chain_id: str) -> dict[str, Any]:
    chain = next((c for c in webinfo._chains(db, home_id, None) if c["id"] == chain_id), None)
    if chain is None:
        return {"error": "Store not found"}
    items = webinfo._match_items(db, home_id)
    today = time.strftime("%Y-%m-%d")
    pages: list[dict[str, Any]] = []
    error = None
    with webdebug.capture() as trace:
        try:
            for raw, text, url, title in webinfo._deal_sources(chain, []):
                on_sale = [x for x in priceextract.extract(raw) if x["on_sale"] and not (x["valid_to"] and x["valid_to"] < today)]
                matches = webparse.match_products([d["product"] for d in on_sale], items, webinfo.MIN_MATCH_SCORE)
                page = {"url": url, "title": title, "on_sale_on_page": on_sale[:20],
                        "matched": [{"product": d["product"], "price": d["price"], "item": m["name"]} for d, m in zip(on_sale, matches) if m],
                        "model_used": False}
                if not page["matched"] and len(pages) < 2:
                    from datetime import date
                    answer = textllm.complete_json(webparse.DEALS_SYSTEM, webparse.deals_prompt(chain["name"], chain["city"], date.today(),
                                                                                                text[:webinfo.PAGE_CHARS_FOR_MODEL]), 3000)
                    deals = webparse.clean_deals(answer, date.today())
                    ms = webparse.match_products([d["product"] for d in deals], items, webinfo.MIN_MATCH_SCORE)
                    page.update(model_used=True, model_deals=deals[:20],
                                matched=[{"product": d["product"], "price": d["price"], "item": m["name"]} for d, m in zip(deals, ms) if m])
                pages.append(page)
                if len(pages) >= MAX_DEAL_PAGES:
                    break
        except (websearch.WebSearchError, textllm.TextLLMError, webinfo.WebInfoError) as e:
            error = str(e)
    return {"chain": chain, "pages": pages, "error": error, "saved": False, "trace": trace}


# --------------------------------------------------------------------------- #
# Presets: one kind of lookup for all stores or items, as a dry run
# --------------------------------------------------------------------------- #

PRESETS = ("queries", "hours", "prices", "deals")
MAX_PRESET_TARGETS = 25
_preset_results: dict[str, dict[str, Any]] = {}


def _price_pairs(db: Session, home_id: str, limit: int) -> list[tuple[dict[str, Any], dict[str, Any]]]:
    """(item, store) pairs a price check would try for the ``limit`` items bought most: the stores each was bought at
    first, then stores added from the nearby search (as ``start_prices_job`` does)."""
    from app.services.analysis_data import load_observations

    items = [i for i in webinfo._match_items(db, home_id) if not i.get("skip")][:limit]
    chains = {c["id"]: c for c in webinfo._chains(db, home_id, None, fuel_stations=True)}
    bought: dict[str, set[str]] = {}
    for r in load_observations(db, home_id, item_ids=[i["id"] for i in items]) if items else []:
        bought.setdefault(r["item_id"], set()).add(r["chain_id"])
    nearby_ids = {l["chain_id"] for l in trips.home_locations(db, home_id) if l.get("nearby")} - {c for cs in bought.values() for c in cs}
    pairs = [(i, chains[c]) for i in items for c in sorted(bought.get(i["id"], ())) if c in chains]
    pairs += [(i, chains[c]) for i in items for c in sorted(nearby_ids) if c in chains and not i.get("fuel")]
    return pairs[:MAX_PRESET_TARGETS]


def _area_queries(pairs: list[tuple[dict[str, Any], dict[str, Any]]]) -> list[dict[str, Any]]:
    """The one-search-per-item area searches a price check makes first (in your ZIP code), and which stores they cover."""
    by_item: dict[str, tuple[dict[str, Any], list[dict[str, Any]]]] = {}
    for item, chain in pairs:
        by_item.setdefault(item["id"], (item, []))[1].append(chain)
    out = []
    for item, chains in by_item.values():
        if len(chains) < (2 if item.get("fuel") else 3):
            continue
        area = next((c.get("zip") or c.get("city") for c in chains if c.get("zip") or c.get("city")), None)
        if area and not (item.get("fuel") and item["fuel"] != "regular"):
            query = f"gas prices {area}" if item.get("fuel") else f"{item.get('plain_name') or item['name']} price {area}"
            out.append({"item": item["name"], "query": query, "stores": [c["name"] for c in chains]})
    return out


def preset_queries(db: Session, home_id: str, limit: int) -> dict[str, Any]:
    """What every lookup would open and search for, without sending anything."""
    every = trips.home_locations(db, home_id)
    grocery = webinfo.grocery_chain_ids(db, home_id, every)
    locations = [l for l in every if l["chain_id"] in grocery][:MAX_PRESET_TARGETS]
    chains = webinfo._chains(db, home_id, None)[:MAX_PRESET_TARGETS]
    items = webinfo._match_items(db, home_id)
    return {
        "provider": websearch.provider(),
        "names": [{"item": i["name"], "search": i.get("search_name") or i["name"], "match": i.get("plain_name") or i["name"]}
                  for i in items if not i.get("skip") and (i.get("search_name") or i["name"]) != i["name"]],
        "skipped_items": [{"item": i["name"], "reason": i["skip"]} for i in items if i.get("skip")],
        "skipped_stores": sorted({l["chain_name"] for l in every if l["chain_id"] not in grocery}),
        "hours": [{"label": l["label"], "store_page": l.get("page_url"), "site": websearch.site_domain(l.get("website")),
                   "query": webinfo.hours_query(l)} for l in locations if l["address"]],
        "prices": [{"item": i["name"], "searched_as": i.get("search_name"), "store": c["name"], **webinfo.price_queries(i, c)}
                   for i, c in _price_pairs(db, home_id, limit)],
        "area": _area_queries(_price_pairs(db, home_id, limit)),
        "deals": [{"store": c["name"], "weekly_ad_page": c.get("deals_url"),
                   "queries": [{"query": q, "site": s} for q, s in webinfo.deal_queries(c, [])]} for c in chains],
    }


def start_preset(home_id: str, preset: str, limit: int) -> dict[str, Any]:
    """Run hours, prices or deals for every location, item-at-store pair or store, one after another, in the
    background; nothing is saved. The results come from ``preset_results`` once the job has finished."""
    from app.db import get_db_session

    with get_db_session()() as db:
        if preset == "hours":
            targets = [(l["id"], l["label"]) for l in trips.home_locations(db, home_id) if l["address"]][:min(limit, MAX_PRESET_TARGETS)]
        elif preset == "prices":
            targets = [((i["id"], c["id"]), f"{i['name']} at {c['name']}") for i, c in _price_pairs(db, home_id, limit)]
        else:
            targets = [(c["id"], c["name"]) for c in webinfo._chains(db, home_id, None)][:min(limit, MAX_PRESET_TARGETS)]
    if not targets:
        raise webinfo.WebInfoError("There is nothing to try: add stores (and, for prices, approve receipts) first.")
    job = webinfo._new_job(f"debug-{preset}", home_id, len(targets))
    for old in list(_preset_results)[:-4]:  # keep the last few runs only
        _preset_results.pop(old, None)

    def work(j: dict[str, Any]) -> None:
        results = []
        for key, label in targets:
            j["message"] = f"{label}…"
            with get_db_session()() as db:
                if preset == "hours":
                    r = run_hours(db, home_id, key)
                    summary = (f"{r['result']['hours']} ({r['result'].get('note') or 'confidence ' + str(r['result']['confidence'])})"
                               if r.get("result") else "no hours found")
                    ok = bool(r.get("result"))
                elif preset == "prices":
                    r = run_price(db, home_id, key[0], key[1])
                    p = r.get("picked")
                    summary = f"{p['product']}: {p['price']} ({p.get('method')})" if p else "no price found"
                    ok = bool(p)
                else:
                    r = run_deals(db, home_id, key)
                    matched = [m for page in r.get("pages", []) for m in page["matched"]]
                    summary = "; ".join(f"{m['item']}: {m['price']}" for m in matched) or "no deals on your items"
                    ok = bool(matched)
            results.append({"label": label, "ok": ok, "summary": summary, "error": r.get("error"),
                            "requests": len(r.get("trace", [])), "detail": r})
            j["done"] += 1
            j["results"] += 1 if ok else 0
            _preset_results[j["id"]] = {"preset": preset, "finished": False, "results": results}
        _preset_results[j["id"]] = {"preset": preset, "finished": True, "results": results}

    webinfo._run_in_thread(job, work)
    return dict(job)


def preset_results(job_id: str) -> dict[str, Any] | None:
    return _preset_results.get(job_id)
