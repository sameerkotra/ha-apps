"""Where the family's places are (§13.3), for the places map.

Only while the places map is switched on (Admin → App settings → Features). Unique place
texts from events are looked up in the background with the same approach as
Household Todo's geocoder: OpenStreetMap Nominatim (`nominatim_url`), at most
one request per second, one place per tick, an identifying User-Agent, and a
retry with a unit/house part stripped — then with the first part of the place
dropped ("12 Main St, Guntur, India" → "Guntur, India"). Results are cached in
`place_geo`; a failed place is tried again after a week (at most 3 times).
Anyone can drag a pin to fix it: that's a `manual` row, never overwritten.

Only the place text is sent — no names, no dates.
"""
import asyncio
import json
import logging
import re
import threading
import time
import urllib.parse
import urllib.request
from datetime import timedelta

from starlette.concurrency import run_in_threadpool

from . import config, db, features, settings

logger = logging.getLogger("geocode")

USER_AGENT = "FamilyTree-HomeAssistantAddon/2.0 (self-hosted Home Assistant add-on)"
MIN_INTERVAL = 1.05
RETRY_DAYS = 7
MAX_TRIES = 3
IDLE_SECONDS = 60

_throttle_lock = threading.Lock()
_last_call = 0.0


def key(place: str) -> str:
    """"  Guntur ,  Andhra Pradesh, INDIA " → "guntur, andhra pradesh, india"."""
    parts = [" ".join(p.split()) for p in (place or "").lower().split(",")]
    return ", ".join(p for p in parts if p)


def _throttle() -> None:
    global _last_call
    with _throttle_lock:
        wait = MIN_INTERVAL - (time.monotonic() - _last_call)
        if wait > 0:
            time.sleep(wait)
        _last_call = time.monotonic()


_UNIT_RE = re.compile(r"(?:^|[,\s])(?:#\s*[\w-]+|(?:suite|ste|unit|apt|apartment|flat|door no|d\.? ?no|h\.? ?no|"
                      r"plot|bldg|building|fl|floor|rm|room)\.?\s*[\w/-]+)", re.IGNORECASE)


def variants(place: str) -> list:
    """The place as written, without a unit/house number, then with leading parts dropped."""
    place = " ".join((place or "").split()).strip(" ,")
    out = [place]
    stripped = re.sub(r"\s*,\s*,+", ",", re.sub(r"\s+", " ", _UNIT_RE.sub(" ", place, count=1))).strip(" ,")
    if stripped and stripped not in out:
        out.append(stripped)
    parts = [p.strip() for p in stripped.split(",") if p.strip()]
    while len(parts) > 2:
        parts = parts[1:]
        v = ", ".join(parts)
        if v not in out:
            out.append(v)
    return out[:4]


def _lookup(q: str):
    _throttle()
    url = settings.get("nominatim_url") + "/search?" + urllib.parse.urlencode({"q": q, "format": "json", "limit": 1})
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            results = json.loads(resp.read())
        return float(results[0]["lat"]), float(results[0]["lon"])
    except Exception as e:                                   # network, no result, odd answer
        logger.debug("Geocoding %r: %s", q, e)
        return None


def geocode_blocking(place: str):
    for q in variants(place):
        hit = _lookup(q)
        if hit:
            return hit
    return None


def places_in_use(conn) -> dict:
    """{place_key: place text as first written} for every event of someone not in the trash."""
    out = {}
    for r in conn.execute(
            "SELECT e.place FROM events e LEFT JOIN people p ON p.id = e.person_id "
            "LEFT JOIN families f ON f.id = e.family_id WHERE e.place IS NOT NULL AND e.place != '' "
            "AND (p.id IS NULL OR p.deleted_at IS NULL) AND (f.id IS NULL OR f.deleted_at IS NULL) "
            "ORDER BY e.created_at"):
        k = key(r["place"])
        if k and k not in out:
            out[k] = r["place"]
    return out


def next_place(conn):
    """(key, place) still to look up, or None."""
    known = {r["place_key"]: r for r in conn.execute("SELECT * FROM place_geo")}
    retry_before = (config.utcnow() - timedelta(days=RETRY_DAYS)).strftime("%Y-%m-%dT%H:%M:%SZ")
    for k, place in places_in_use(conn).items():
        row = known.get(k)
        if row is None:
            return k, place
        if row["source"] == "failed" and row["tries"] < MAX_TRIES and row["checked_at"] < retry_before:
            return k, place
    return None


def step_blocking() -> bool:
    """Look up one place. True if there was one to do."""
    if not features.on("map"):
        return False
    with db.get_conn() as conn:
        todo = next_place(conn)
    if not todo:
        return False
    k, place = todo
    hit = geocode_blocking(place)                          # no DB connection is open here
    with db.get_conn() as conn:
        row = conn.execute("SELECT source, tries FROM place_geo WHERE place_key = ?", (k,)).fetchone()
        if row and row["source"] == "manual":
            return True                                    # someone pinned it meanwhile
        tries = (row["tries"] if row else 0) + 1
        conn.execute(
            "INSERT INTO place_geo (place_key, place, lat, lon, source, tries, checked_at) VALUES (?, ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(place_key) DO UPDATE SET place = excluded.place, lat = excluded.lat, lon = excluded.lon, "
            "source = excluded.source, tries = excluded.tries, checked_at = excluded.checked_at",
            (k, place, hit[0] if hit else None, hit[1] if hit else None, "geocoder" if hit else "failed", tries,
             config.now_iso()))
    if not hit:
        logger.info("Couldn't find %r on the map (try %s); drag a pin to place it.", place, tries)
    return True


def status(conn) -> dict:
    in_use = places_in_use(conn)
    rows = {r["place_key"]: r["source"] for r in conn.execute("SELECT place_key, source FROM place_geo")}
    located = sum(1 for k in in_use if rows.get(k) in ("geocoder", "manual"))
    failed = sum(1 for k in in_use if rows.get(k) == "failed")
    return {"places": len(in_use), "located": located, "failed": failed, "pending": len(in_use) - located - failed}


async def loop():
    """One place per second while there's work; otherwise check again in a minute."""
    while True:
        busy = False
        try:
            busy = await run_in_threadpool(step_blocking)
        except Exception:
            logger.exception("Geocoding step failed")
        await asyncio.sleep(0 if busy else IDLE_SECONDS)
