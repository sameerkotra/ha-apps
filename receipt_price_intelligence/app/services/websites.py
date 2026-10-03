"""Filling in store chains' websites (the *Fill in websites* button on the Stores page).

Two steps, for the home's chains that have no website yet:

1. **From the stores' own pages** (applied straight away): the domain of the store pages the chain's
   locations have (set by hand, or from OpenStreetMap), and of the websites OpenStreetMap lists for the
   chain's branches found by *Stores near home*.
2. **From a web search** (suggested, never applied without the person): one search per remaining chain
   for its name; the site that comes up most, and preferably carries the chain's name, is suggested.

Only the domain is ever set (e.g. ``kroger.com``). Social networks, maps, review, delivery and coupon sites
are never taken for a store's own website.
"""

from __future__ import annotations

import re
from collections import Counter
from typing import Any
from urllib.parse import urlparse

from sqlalchemy.orm import Session

from app.db import get_db_session
from app.db.models import NearbyStore, StoreChain
from app.logging_config import get_logger
from app.services import trips, webinfo, websearch

logger = get_logger("websites")

MAX_SEARCHES = 25

# Sites that list stores but are not a store's own website
NOT_A_STORE_SITE = {
    "facebook.com", "instagram.com", "twitter.com", "x.com", "tiktok.com", "youtube.com", "linkedin.com", "pinterest.com",
    "reddit.com", "nextdoor.com", "linktr.ee", "wikipedia.org", "wikidata.org", "openstreetmap.org", "google.com",
    "goo.gl", "bing.com", "duckduckgo.com", "apple.com", "waze.com", "mapquest.com", "yelp.com", "tripadvisor.com",
    "foursquare.com", "yellowpages.com", "superpages.com", "bbb.org", "chamberofcommerce.com", "manta.com",
    "hotfrog.com", "merchantcircle.com", "citysearch.com", "local.com", "allmenus.com", "opentable.com",
    "doordash.com", "instacart.com", "ubereats.com", "grubhub.com", "postmates.com", "shipt.com", "gopuff.com",
    "flipp.com", "weeklyads2.com", "ladysavings.com", "iheartpublix.com", "retailmenot.com", "coupons.com",
    "slickdeals.net", "groupon.com", "indeed.com", "glassdoor.com", "storeopeninghours.com", "hours.com",
    "storehours.org", "hoursguide.com", "restaurantji.com", "birdeye.com", "zomato.com", "trustpilot.com",
}
_TWO_LEVEL_SUFFIXES = {
    "co.uk", "org.uk", "ac.uk", "com.au", "net.au", "co.nz", "co.in", "com.br", "co.jp", "com.mx", "co.za",
    "com.sg", "com.tr", "com.cn", "co.kr", "com.ar", "com.my", "com.ph", "co.id", "com.hk",
}


def registrable_domain(url_or_host: str | None) -> str | None:
    """"stores.kroger.com" or "https://www.kroger.com/x" -> "kroger.com" (a simple, common-case rule)."""
    text = url_or_host or ""
    host = (urlparse(text).hostname if "://" in text else text.split("/")[0]) or ""
    labels = [x for x in host.lower().strip(".").split(".") if x]
    if len(labels) < 2 or all(x.isdigit() for x in labels):
        return None
    if len(labels) >= 3 and ".".join(labels[-2:]) in _TWO_LEVEL_SUFFIXES:
        return ".".join(labels[-3:])
    return ".".join(labels[-2:])


def not_a_store_site(domain: str) -> bool:
    return any(domain == d or domain.endswith("." + d) for d in NOT_A_STORE_SITE)


def _letters(text: str) -> str:
    return re.sub(r"[^a-z0-9]", "", (text or "").lower())


def carries_name(domain: str, chain_name: str) -> bool:
    """Whether a domain looks like the chain's own ("kroger.com" for Kroger, "traderjoes.com" for Trader Joe's)."""
    name, site = _letters(chain_name), _letters(domain.rsplit(".", 1)[0])
    if not name or not site:
        return False
    first = _letters(chain_name.split()[0]) if chain_name.split() else name
    return name in site or site in name or (len(first) >= 4 and first in site)


def pick_from_hits(hits: list[dict[str, str]], chain_name: str) -> tuple[str, str] | None:
    """(domain, a page on it) that search results point to as the chain's own site, or None if unclear."""
    counts: Counter[str] = Counter()
    first_url: dict[str, str] = {}
    for hit in hits:
        domain = registrable_domain(hit.get("url"))
        if not domain or not_a_store_site(domain):
            continue
        counts[domain] += 1
        first_url.setdefault(domain, hit["url"])
    if not counts:
        return None
    ranked = sorted(counts, key=lambda d: (carries_name(d, chain_name), counts[d]), reverse=True)
    best = ranked[0]
    if carries_name(best, chain_name) or counts[best] >= 2:
        return best, first_url[best]
    return None


def _home_chains(db: Session, home_id: str) -> dict[str, dict[str, Any]]:
    chains: dict[str, dict[str, Any]] = {}
    locations = trips.home_locations(db, home_id)
    grocery = webinfo.grocery_chain_ids(db, home_id, locations)
    for loc in locations:
        if loc["chain_id"] not in grocery:
            continue  # a restaurant or a car park: its website does not help any lookup
        c = chains.setdefault(loc["chain_id"], {"id": loc["chain_id"], "name": loc["chain_name"], "website": loc.get("website"),
                                                "pages": [], "city": None})
        if loc.get("page_url"):
            c["pages"].append(loc["page_url"])
        row = loc.get("_address_row")
        c["city"] = c["city"] or getattr(row, "city", None)
    return chains


def fill_from_pages(db: Session, home_id: str) -> list[dict[str, Any]]:
    """Set the website of each chain that has none from its stores' own pages. Returns what was set."""
    chains = {k: c for k, c in _home_chains(db, home_id).items() if not c["website"]}
    if not chains:
        return []
    for row in db.query(NearbyStore).filter(NearbyStore.store_chain_id.in_(list(chains)), NearbyStore.website.isnot(None),
                                            NearbyStore.status != "HIDDEN").all():
        chains[row.store_chain_id]["pages"].append(row.website)
    applied = []
    for chain_id, c in chains.items():
        domains = Counter(d for d in (registrable_domain(p) for p in c["pages"]) if d and not not_a_store_site(d))
        if not domains:
            continue
        domain = sorted(domains, key=lambda d: (carries_name(d, c["name"]), domains[d]), reverse=True)[0]
        chain = db.get(StoreChain, chain_id)
        if chain is not None and not chain.website:
            chain.website = f"https://{domain}"
            applied.append({"chain_id": chain_id, "name": c["name"], "website": chain.website, "source": "store pages"})
    db.commit()
    return applied


def start_search_job(home_id: str) -> dict[str, Any] | None:
    """Search for the website of each chain still without one, in the background. The finished job carries
    ``suggestions``: [{chain_id, name, website, source_url, matches_name}]. None when there is nothing to search."""
    if not websearch.configured():
        return None
    existing = webinfo.running_job("websites", home_id)
    if existing:
        return existing
    with get_db_session()() as db:
        todo = [c for c in _home_chains(db, home_id).values() if not c["website"]][:MAX_SEARCHES]
    if not todo:
        return None
    job = webinfo._new_job("websites", home_id, len(todo))
    job["suggestions"] = []

    def work(j: dict[str, Any]) -> None:
        j["message"] = f"Looking up {len(todo)} website{'s' if len(todo) != 1 else ''}…"

        def handle(c: dict[str, Any]) -> bool:
            query = " ".join(x for x in (c["name"], "grocery store official site", c["city"]) if x)
            picked = pick_from_hits(websearch.search(query, 8), c["name"])
            if not picked:
                return False
            domain, url = picked
            j["suggestions"].append({"chain_id": c["id"], "name": c["name"], "website": f"https://{domain}", "domain": domain,
                                     "source_url": websearch.safe_url(url) or None, "matches_name": carries_name(domain, c["name"])})
            return True

        webinfo._run_parallel(j, todo, handle)
        j["suggestions"].sort(key=lambda s: s["name"].lower())

    webinfo._run_in_thread(job, work)
    return dict(job)


def fill(db: Session, home_id: str, search: bool = True) -> dict[str, Any]:
    """Step 1 now, and step 2 started in the background (when asked for and a web search is set up)."""
    applied = fill_from_pages(db, home_id)
    job = start_search_job(home_id) if search else None
    missing = sum(1 for c in _home_chains(db, home_id).values() if not c["website"])
    return {"applied": applied, "job": job, "missing": missing, "search_available": websearch.configured()}
