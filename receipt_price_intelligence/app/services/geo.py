"""Map services: turning addresses into coordinates, and driving distances between them.

Both are free OpenStreetMap-based services, and both addresses can be replaced with your own server
in Admin → App settings:

* **Nominatim** (``geocoder_url``) finds the coordinates of a street address.
* **OSRM** (``routing_url``) gives driving distances and times, and the route to draw.

The public servers are run on donated resources with a fair-use policy (about one search per
second, a clear User-Agent, no bulk use). This module follows it: a delay between address
searches, an identifying User-Agent (with your contact email if you set one), and results are
cached by the caller so each address is looked up once.

Only addresses are sent to these servers (your home address, and the addresses of stores you
shop at), never your receipts or prices.
"""

import json
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

from app.config import get_settings
from app.logging_config import get_logger

logger = get_logger("geo")

TIMEOUT = 20
MIN_SEARCH_INTERVAL = 1.1  # seconds between address searches (Nominatim usage policy)
MAX_ROUTE_POINTS = 60

_search_lock = threading.Lock()
_last_search = 0.0


class GeoError(RuntimeError):
    """A map service could not be reached or gave no usable answer."""


def _user_agent() -> str:
    from app.config import APP_VERSION as version
    email = get_settings().map_contact_email.strip()
    return f"ReceiptPriceIntelligence/{version} (Home Assistant app{'; ' + email if email else ''})"


def _get_json(url: str) -> Any:
    request = urllib.request.Request(url, headers={"User-Agent": _user_agent(), "Accept": "application/json"})
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        raise GeoError(f"The map service answered with an error ({e.code})") from e
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        raise GeoError("The map service could not be reached") from e
    except json.JSONDecodeError as e:
        raise GeoError("The map service sent an unreadable answer") from e


def geocode(address: str) -> dict[str, Any] | None:
    """Coordinates of an address, or None if it cannot be found. Raises GeoError if unreachable."""
    global _last_search
    query = " ".join((address or "").split())
    if not query:
        return None

    params = {"q": query, "format": "jsonv2", "limit": "1", "addressdetails": "0"}
    email = get_settings().map_contact_email.strip()
    if email:
        params["email"] = email
    url = get_settings().geocoder_url.rstrip("/") + "/search?" + urllib.parse.urlencode(params)

    with _search_lock:  # one search at a time, spaced out
        wait = MIN_SEARCH_INTERVAL - (time.monotonic() - _last_search)
        if wait > 0:
            time.sleep(wait)
        try:
            results = _get_json(url)
        finally:
            _last_search = time.monotonic()

    if not results:
        return None
    top = results[0]
    try:
        return {
            "lat": float(top["lat"]), "lng": float(top["lon"]),
            "confidence": float(top.get("importance") or 0.5) or 0.1,
            "label": top.get("display_name"),
        }
    except (KeyError, TypeError, ValueError) as e:
        raise GeoError("The map service sent an unexpected answer") from e


def _coords(points: list[tuple[float, float]]) -> str:
    return ";".join(f"{lng:.6f},{lat:.6f}" for lat, lng in points)


def route_matrix(points: list[tuple[float, float]]) -> tuple[list[list[float]], list[list[float]]]:
    """Driving distance (km) and time (minutes) between every pair of points, from OSRM.

    Raises GeoError if the service cannot be used; the caller then falls back to an estimate.
    """
    if len(points) > MAX_ROUTE_POINTS:
        raise GeoError("Too many places for the routing service")
    url = f"{get_settings().routing_url.rstrip('/')}/table/v1/driving/{_coords(points)}?annotations=distance,duration"
    data = _get_json(url)
    if data.get("code") != "Ok" or not data.get("distances") or not data.get("durations"):
        raise GeoError("The routing service could not calculate distances")

    n = len(points)
    km = [[0.0] * n for _ in range(n)]
    minutes = [[0.0] * n for _ in range(n)]
    for i in range(n):
        for j in range(n):
            d, t = data["distances"][i][j], data["durations"][i][j]
            if d is None or t is None:
                raise GeoError("Some places cannot be reached by road")
            km[i][j], minutes[i][j] = d / 1000.0, t / 60.0
    return km, minutes


def route_geometry(points: list[tuple[float, float]]) -> dict[str, Any]:
    """The driving route through the points in order: total km, minutes and the line to draw."""
    url = (f"{get_settings().routing_url.rstrip('/')}/route/v1/driving/{_coords(points)}"
           "?overview=simplified&geometries=geojson&steps=false")
    data = _get_json(url)
    routes = data.get("routes") or []
    if data.get("code") != "Ok" or not routes:
        raise GeoError("The routing service could not calculate the route")
    route = routes[0]
    return {
        "km": route["distance"] / 1000.0,
        "minutes": route["duration"] / 60.0,
        "line": [[lat, lng] for lng, lat in route["geometry"]["coordinates"]],
    }
