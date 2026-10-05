"""Food stores near a point, from OpenStreetMap through an Overpass server.

The query and parsing here are pure functions; ``fetch_stores`` is the only one that goes on the
network. Like the address search in ``geo.py`` this follows the public servers' fair-use policy: one
query at a time, an identifying User-Agent, and the caller keeps the results for weeks rather than
asking again.

Privacy: the point searched around is the home's coordinates rounded to two decimals (about a
kilometre), with the radius widened to make up for it. Distances are then worked out here from the
exact coordinates, and stores outside the radius asked for are dropped.
"""

from __future__ import annotations

import json
import re
import threading
import urllib.error
import urllib.parse
from typing import Any

from app.common import geo

TIMEOUT = 40
MAX_RESULTS = 300
ROUNDING_KM = 1.2  # how far a point rounded to 2 decimals can be from the real one (worst case)

# OpenStreetMap shop=* values that sell groceries, and how to describe them
SHOP_KINDS = {
    "supermarket": "Supermarket",
    "wholesale": "Warehouse club",
    "greengrocer": "Greengrocer",
    "butcher": "Butcher",
    "convenience": "Convenience store",
    "department_store": "Department store",
    "variety_store": "Variety store",
    "health_food": "Health food store",
    "deli": "Deli",
    "seafood": "Fish market",
    "frozen_food": "Frozen food store",
    "farm": "Farm shop",
}

_lock = threading.Lock()


class OsmError(RuntimeError):
    """The Overpass server could not be used. The message is safe to show."""


def haversine_km(a: tuple[float, float], b: tuple[float, float]) -> float:
    from app.services.planner import haversine_km as distance  # one formula for the whole app
    return distance(a, b)


def build_query(lat: float, lng: float, radius_km: float) -> str:
    """The Overpass QL query for grocery shops within ``radius_km`` of a rounded point."""
    rlat, rlng = round(lat, 2), round(lng, 2)
    metres = int((radius_km + ROUNDING_KM) * 1000)
    kinds = "|".join(SHOP_KINDS)
    return (f'[out:json][timeout:{TIMEOUT - 5}];'
            f'nwr["shop"~"^({kinds})$"](around:{metres},{rlat:.2f},{rlng:.2f});'
            f'out center tags {MAX_RESULTS};')


def _address(tags: dict[str, str]) -> dict[str, str | None]:
    street = " ".join(x for x in (tags.get("addr:housenumber"), tags.get("addr:street")) if x) or None
    city = tags.get("addr:city") or tags.get("addr:town") or tags.get("addr:village") or tags.get("addr:suburb")
    state = tags.get("addr:state") or tags.get("addr:province")
    postal = tags.get("addr:postcode")
    tail = " ".join(x for x in (state, postal) if x)
    raw = ", ".join(x for x in (street, city, tail) if x) or None
    return {"street": street, "city": city, "state": state, "postal_code": postal, "raw": raw}


def parse_elements(data: Any, home: tuple[float, float], radius_km: float) -> list[dict[str, Any]]:
    """Stores from an Overpass answer, nearest first, only those within ``radius_km`` of ``home``.

    Each: {osm_id, name, brand, shop, kind, lat, lng, distance_km, address{...}, opening_hours_raw,
    opening_hours (app format or None), website}.
    """
    elements = data.get("elements") if isinstance(data, dict) else None
    out, seen = [], set()
    for el in elements if isinstance(elements, list) else []:
        if not isinstance(el, dict) or el.get("type") not in ("node", "way", "relation"):
            continue
        tags = el.get("tags") if isinstance(el.get("tags"), dict) else {}
        centre = el.get("center") if isinstance(el.get("center"), dict) else el
        try:
            lat, lng = float(centre["lat"]), float(centre["lon"])
        except (KeyError, TypeError, ValueError):
            continue
        name = " ".join(str(tags.get("name") or "").split())[:200]
        brand = " ".join(str(tags.get("brand") or "").split())[:200] or None
        if not (name or brand):
            continue  # an unnamed shop cannot be told apart or searched for
        shop = str(tags.get("shop") or "")
        if shop not in SHOP_KINDS:
            continue
        osm_id = f"{el['type']}/{el.get('id')}"
        if osm_id in seen:
            continue
        seen.add(osm_id)
        distance = haversine_km(home, (lat, lng))
        if distance > radius_km:
            continue
        hours_raw = " ".join(str(tags.get("opening_hours") or "").split())[:255] or None
        website = str(tags.get("website") or tags.get("contact:website") or "")
        out.append({
            "osm_id": osm_id, "name": name or brand, "brand": brand, "shop": shop, "kind": SHOP_KINDS[shop],
            "lat": lat, "lng": lng, "distance_km": round(distance, 2), "address": _address(tags),
            "opening_hours_raw": hours_raw, "opening_hours": parse_opening_hours(hours_raw),
            "website": website if re.match(r"^https?://", website, re.I) else None,
        })
    out.sort(key=lambda s: (s["distance_km"], s["name"].lower()))
    return out


# --------------------------------------------------------------------------- #
# opening_hours
# --------------------------------------------------------------------------- #

_DAYS = ("mo", "tu", "we", "th", "fr", "sa", "su")
_APP_DAYS = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")
_DAY = r"(?:Mo|Tu|We|Th|Fr|Sa|Su)"
_DAY_SPEC = re.compile(rf"^{_DAY}(?:\s*-\s*{_DAY})?(?:\s*,\s*{_DAY}(?:\s*-\s*{_DAY})?)*$", re.I)
_TIME_SPAN = re.compile(r"^(\d{1,2}):(\d{2})\s*-\s*(\d{1,2}):(\d{2})$")
_UNSUPPORTED = re.compile(r"\b(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec|week|easter|sunrise|sunset|dawn|dusk)\b|\[|\+|\"", re.I)


def _days_of(spec: str) -> list[int]:
    days: list[int] = []
    for part in re.split(r"\s*,\s*", spec.strip()):
        ends = [_DAYS.index(x.strip().lower()[:2]) for x in part.split("-")]
        if len(ends) == 1:
            days.append(ends[0])
        else:
            i = ends[0]
            while True:  # "Fr-Mo" wraps round the week
                days.append(i)
                if i == ends[1]:
                    break
                i = (i + 1) % 7
    return days


def _time(h: str, m: str) -> str | None:
    hour, minute = int(h), int(m)
    if hour == 24 and minute == 0:
        return "00:00"  # midnight closing: the planner reads a close at or before the open as next day
    if 0 <= hour <= 23 and 0 <= minute <= 59:
        return f"{hour:02d}:{minute:02d}"
    return None


def parse_opening_hours(value: str | None) -> dict[str, Any] | None:
    """OpenStreetMap ``opening_hours`` in the app's format, or None when it cannot be read with certainty.

    Handles the common forms: "24/7", "Mo-Su 07:00-22:00", "Mo-Fr 08:00-20:00; Sa 09:00-18:00; Su off",
    "Mo-Sa 07:00-12:00,13:00-19:00", "Mo-Fr 08:00-20:00, Sa 09:00-14:00". Later rules override earlier
    ones for the days they name, as in OpenStreetMap. Public-holiday rules (PH, SH) are left out; anything
    with months, weeks, sunrise or comments is not guessed at (None). Days no rule mentions are left out
    (unknown), unless a rule gives times without days (then it is every day).
    """
    text = " ".join((value or "").split())
    if not text:
        return None
    if text.lower() in ("24/7", "mo-su 00:00-24:00"):
        return {"all": True}
    if _UNSUPPORTED.search(text):
        return None
    week: dict[int, Any] = {}
    rules = [r.strip() for r in re.split(r";|\|\|", text) if r.strip()]
    # "Mo-Fr 08:00-20:00, Sa 09:00-14:00": a comma after a time (or "off") and before a day starts a new
    # rule; "Mo,We,Fr 09:00-17:00" is one rule
    rules = [p.strip() for r in rules for p in re.split(rf"(?<=[0-9fd]),\s*(?={_DAY}\b|PH\b|SH\b)", r) if p.strip()]
    for rule in rules:
        if re.match(r"^(PH|SH)\b", rule, re.I):
            continue  # holidays: not part of the regular week
        m = re.match(rf"^({_DAY}(?:[\s,\-]*{_DAY})*)?\s*(.*)$", rule, re.I)
        day_text, rest = (m.group(1) or "").strip(), m.group(2).strip().lower()
        if day_text and not _DAY_SPEC.match(day_text):
            return None
        days = _days_of(day_text) if day_text else list(range(7))
        if rest in ("off", "closed"):
            spans: Any = []
        elif rest in ("24/7", "00:00-24:00"):
            spans = True
        else:
            spans = []
            for part in re.split(r"\s*,\s*", rest):
                t = _TIME_SPAN.match(part)
                if not t:
                    return None
                start, end = _time(t.group(1), t.group(2)), _time(t.group(3), t.group(4))
                if not start or not end or start == end:
                    return None
                spans.append([start, end])
            if not spans:
                return None
        for d in days:
            week[d] = spans
    if not week:
        return None
    result = {_APP_DAYS[d]: week[d] for d in sorted(week)}
    if len(result) == 7 and all(v == result["mon"] for v in result.values()):
        return {"all": result["mon"]}
    return result


# --------------------------------------------------------------------------- #
# Network
# --------------------------------------------------------------------------- #

def fetch_stores(url: str, home: tuple[float, float], radius_km: float, contact_email: str = "") -> list[dict[str, Any]]:
    """Grocery stores within ``radius_km`` of ``home``, nearest first. Raises OsmError if the server fails."""
    from app.services.geo import user_agent
    body = urllib.parse.urlencode({"data": build_query(home[0], home[1], radius_km)}).encode("utf-8")
    headers = {"User-Agent": user_agent(contact_email), "Accept": "application/json",
               "Content-Type": "application/x-www-form-urlencoded"}
    with _lock:  # one query at a time
        try:
            data = json.loads(geo.fetch(url, data=body, method="POST", headers=headers,
                                        timeout=TIMEOUT).decode("utf-8", errors="replace"))
        except urllib.error.HTTPError as e:
            if e.code in (429, 504):
                raise OsmError("The OpenStreetMap store search is busy right now. Try again in a few minutes.") from e
            raise OsmError(f"The OpenStreetMap store search answered with an error ({e.code})") from e
        except (urllib.error.URLError, TimeoutError, OSError) as e:
            raise OsmError("The OpenStreetMap store search could not be reached") from e
        except ValueError as e:
            raise OsmError("The OpenStreetMap store search sent an unreadable answer") from e
    if isinstance(data, dict) and data.get("remark") and not data.get("elements"):
        raise OsmError("The OpenStreetMap store search could not finish (it may be busy). Try a smaller distance or again later.")
    return parse_elements(data, home, radius_km)
