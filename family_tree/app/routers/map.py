"""Places map (§13.3): located events for the map, pins you can fix, and
geocoding progress. Everything here needs the Places map switch (Features):
while it's off every route answers 404 "Places map is turned off"."""
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import Field

from .. import features, config, dates, db, geocode, graph as graph_mod, settings
from ..auth import require_admin, require_user
from ..models import Strict

router = APIRouter(prefix="/api", tags=["map"], dependencies=[Depends(features.required("map"))])

KINDS = {"birth": "birth", "baptism": "birth", "marriage": "marriage", "engagement": "marriage", "divorce": "marriage",
         "residence": "residence", "immigration": "residence", "emigration": "residence",
         "death": "death", "burial": "death"}
ATTRIBUTION = "© OpenStreetMap contributors"


def _descendants(g, pid) -> set:
    out, stack = set(), [pid]
    while stack:
        cur = stack.pop()
        for c, _f, _r in g.children(cur):
            if c not in out:
                out.add(c)
                stack.append(c)
    return out


@router.get("/map/status")
def map_status(user: dict = Depends(require_user)):
    out = {"enabled": True, "tilesUrl": settings.get("map_tiles_url"), "attribution": ATTRIBUTION}
    with db.get_conn() as conn:
        out.update(geocode.status(conn))
    return out


@router.get("/map/points")
def points(filter: str = Query("all", pattern="^(all|ancestors|descendants|person)$"), personId: str | None = None,
           user: dict = Depends(require_user)):
    """Every event with a place in the chosen people's lives: located ones
    with lat/lon, plus the places not found yet (`unlocated`)."""
    with db.get_conn() as conn:
        g = graph_mod.get(conn)
        allowed = None
        if filter != "all":
            pid = personId or user["me_person_id"]
            if not pid or pid not in g.people:
                raise HTTPException(422, "Choose whose places to show.")
            if filter == "person":
                allowed = {pid}
            elif filter == "ancestors":
                allowed = set(g.ancestors(pid, max_gen=30))
            else:
                allowed = _descendants(g, pid) | {pid}
        geo = {r["place_key"]: r for r in conn.execute("SELECT * FROM place_geo")}
        rows = conn.execute("SELECT * FROM events WHERE place IS NOT NULL AND place != ''").fetchall()
        if not features.on("ceremonies"):
            rows = [r for r in rows if r["type"] not in features.CEREMONY_TYPES]
    out, unlocated = [], {}
    for r in rows:
        if r["person_id"]:
            who = [r["person_id"]] if r["person_id"] in g.people else []
        else:
            fam = g.families.get(r["family_id"])
            who = fam.partners() if fam else []
        if not who or (allowed is not None and not (set(who) & allowed)):
            continue
        k = geocode.key(r["place"])
        gr = geo.get(k)
        if not gr or gr["lat"] is None:
            u = unlocated.setdefault(k, {"place": r["place"], "placeKey": k, "events": 0,
                                         "status": gr["source"] if gr else "pending"})
            u["events"] += 1
            continue
        out.append({"eventId": r["id"], "type": r["type"], "kind": KINDS.get(r["type"], "other"),
                    "title": r["title"], "year": r["date_y"], "sortKey": r["sort_key"],
                    "dateDisplay": dates.display(dict(r)) if r["date_text"] else None,
                    "place": r["place"], "placeKey": k, "lat": gr["lat"], "lon": gr["lon"], "source": gr["source"],
                    "familyId": r["family_id"],
                    "people": [{"id": p, "name": g.people[p].name, "gender": g.people[p].gender} for p in who]})
    out.sort(key=lambda e: (e["sortKey"] is None, e["sortKey"] or ""))
    return {"points": out, "unlocated": sorted(unlocated.values(), key=lambda u: -u["events"])}


class PinIn(Strict):
    place: str = Field(min_length=1, max_length=200)
    lat: float = Field(ge=-90, le=90)
    lon: float = Field(ge=-180, le=180)


@router.put("/map/places")
def pin(body: PinIn, user: dict = Depends(require_user)):
    """Put a place's pin where it belongs (for every event with that place text)."""
    k = geocode.key(body.place)
    if not k:
        raise HTTPException(422, "Which place?")
    with db.get_conn() as conn:
        conn.execute(
            "INSERT INTO place_geo (place_key, place, lat, lon, source, tries, checked_at, updated_by) "
            "VALUES (?, ?, ?, ?, 'manual', 0, ?, ?) ON CONFLICT(place_key) DO UPDATE SET lat = excluded.lat, "
            "lon = excluded.lon, source = 'manual', checked_at = excluded.checked_at, updated_by = excluded.updated_by",
            (k, body.place.strip(), round(body.lat, 6), round(body.lon, 6), config.now_iso(), user["id"]))
    return {"placeKey": k, "lat": round(body.lat, 6), "lon": round(body.lon, 6), "source": "manual"}


@router.delete("/map/places")
def unpin(place: str = Query(..., min_length=1, max_length=200), user: dict = Depends(require_user)):
    """Forget a place's pin: it's looked up again."""
    with db.get_conn() as conn:
        n = conn.execute("DELETE FROM place_geo WHERE place_key = ?", (geocode.key(place),)).rowcount
    if not n:
        raise HTTPException(404, "That place has no pin yet.")
    return {"ok": True}


@router.post("/map/retry")
def retry_failed(admin: dict = Depends(require_admin)):
    """Look up every place that wasn't found again, now."""
    with db.get_conn() as conn:
        n = conn.execute("DELETE FROM place_geo WHERE source = 'failed'").rowcount
    return {"retrying": n}
