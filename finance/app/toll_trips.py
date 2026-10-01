"""Trips, regular-trip suggestions and tags for the E-470 feature (SPEC.md section 18.8).

A **trip** is what one journey costs: one vehicle's passes, in time order, where each pass is within
TRIP_GAP_MIN minutes of the one before it and travels the same direction. Its **signature** is the
ordered `PLAZA|DIRECTION` list. A **tag** (toll_patterns row) is a signature plus a start-time
window; it carries no car, so it matches the same route driven by any of the user's cars.

A **vehicle** is the car a pass's device is assigned to (`c<car id>`), or, while the device is not
assigned to any car, the device itself (`d<device row id>`). Assigning a device to a car therefore
regroups its passes, which is why every assignment rebuilds the trips.

The trips table is a derived cache: rebuild_trips() recomputes it from the passes and re-applies the
tags and the person's manual overrides (keyed by trip_key, so they survive rebuilds). It runs after
anything that changes the passes or the tags: a statement completing, being confirmed, deleted or
restored, a tag saved or deleted, an override set.

All tuning constants live here, at the top, because they are expected to be adjusted against real
statements (SPEC.md 18.13).
"""
import hashlib
import sqlite3
from collections import defaultdict
from datetime import datetime

TRIP_GAP_MIN = 45          # a pass joins the current trip when within this many minutes of the previous pass
CLUSTER_GAP_MIN = 60       # trips of one route start a new time-of-day group after a gap of this many minutes
WINDOW_PAD_MIN = 15        # a suggested window is earliest start - this to latest start + this
MIN_TRIPS = 31             # a group needs MORE THAN 30 trips (so at least 31) to be suggested as regular
MIN_DAYS_IN_WEEK = 2       # ... and in some Monday-Sunday week they must fall on at least this many days

_FMT = "%Y-%m-%d %H:%M:%S"
_SEP = " > "


# ------------------------------------------------------------------ signatures and times

def make_signature(items: list[tuple[str, str]]) -> str:
    """[(plaza, direction), ...] -> 'SMOKY HILL RD|South > PLAZA A|South'."""
    return _SEP.join(f"{(plaza or '').strip().upper()}|{(direction or '').strip().title()}" for plaza, direction in items)


def signature_items(signature: str) -> list[str]:
    return signature.split(_SEP) if signature else []


def pretty_signature(signature: str) -> str:
    parts = []
    for item in signature_items(signature):
        plaza, _, direction = item.rpartition("|")
        parts.append(f"{plaza.title()} ({direction})" if plaza else item)
    return " → ".join(parts)


def signature_matches(tag_signature: str, trip_signature: str) -> bool:
    """A trip matches a tag's route when it is the same list, or a contiguous part of it (a partial
    trip with a pass missing at either end still matches)."""
    tag, trip = signature_items(tag_signature), signature_items(trip_signature)
    if not trip or len(trip) > len(tag):
        return False
    return any(tag[i:i + len(trip)] == trip for i in range(len(tag) - len(trip) + 1))


def minute_of_day(timestamp: str) -> int:
    return int(timestamp[11:13]) * 60 + int(timestamp[14:16])


def format_minutes(minutes: int) -> str:
    """615 -> '10:15 AM'."""
    hour, minute = divmod(int(minutes), 60)
    return f"{(hour % 12) or 12}:{minute:02d} {'AM' if hour < 12 else 'PM'}"


def clock_value(minutes: int) -> str:
    """615 -> '10:15', for an <input type=time>."""
    return f"{int(minutes) // 60:02d}:{int(minutes) % 60:02d}"


def parse_clock(text: str) -> int | None:
    """'10:15' -> 615 (None if it is not a valid time of day)."""
    try:
        hour, minute = (int(x) for x in text.strip().split(":")[:2])
    except (ValueError, AttributeError):
        return None
    return hour * 60 + minute if 0 <= hour <= 23 and 0 <= minute <= 59 else None


def trip_matches(tag: dict, trip: dict) -> bool:
    start = minute_of_day(trip["started_at"])
    return tag["window_start_min"] <= start <= tag["window_end_min"] and signature_matches(tag["signature"], trip["signature"])


# ------------------------------------------------------------------ building trips

def build_trips(passes: list[dict]) -> list[dict]:
    """passes: dicts with id, vehicle, occurred_at, plaza, direction, amount, dedupe_key.
    Returns one dict per trip: vehicle, trip_key, first_dedupe_key, started_at, ended_at, signature,
    pass_ids, pass_count, amount."""
    by_vehicle: dict[str, list[dict]] = defaultdict(list)
    for p in passes:
        by_vehicle[p["vehicle"]].append(p)

    trips = []
    for vehicle, items in by_vehicle.items():
        items.sort(key=lambda p: (p["occurred_at"], p["id"]))
        current: list[dict] = []
        for p in items:
            if current:
                gap = (datetime.strptime(p["occurred_at"], _FMT) - datetime.strptime(current[-1]["occurred_at"], _FMT)).total_seconds()
                if p["direction"].title() == current[-1]["direction"].title() and gap <= TRIP_GAP_MIN * 60:
                    current.append(p)
                    continue
                trips.append(_trip(vehicle, current))
            current = [p]
        if current:
            trips.append(_trip(vehicle, current))
    trips.sort(key=lambda t: (t["started_at"], t["vehicle"]))
    return trips


def _trip(vehicle: str, items: list[dict]) -> dict:
    first = items[0]
    return {
        "vehicle": vehicle,
        # Independent of the vehicle: assigning a tag to a car must not orphan a manual choice about this trip.
        "trip_key": hashlib.sha1(f"trip|{first['dedupe_key']}".encode()).hexdigest(),
        "first_dedupe_key": first["dedupe_key"],
        "started_at": first["occurred_at"], "ended_at": items[-1]["occurred_at"],
        "signature": make_signature([(p["plaza"], p["direction"]) for p in items]),
        "pass_ids": [p["id"] for p in items], "pass_count": len(items),
        "amount": round(sum(p["amount"] for p in items), 2),
    }


def rebuild_trips(conn: sqlite3.Connection, user_id: str) -> int:
    """Recompute the user's trips from their live, confirmed passes and re-apply tags and overrides.
    Commits. Returns the number of trips."""
    rows = conn.execute(
        "SELECT t.id, t.device_ref, d.car_id, t.occurred_at, t.plaza, t.direction, t.amount, t.dedupe_key "
        "FROM toll_transactions t JOIN toll_statements s ON s.id = t.statement_id JOIN toll_devices d ON d.id = t.device_ref "
        "WHERE t.user_id = ? AND t.deleted_at IS NULL AND t.review_status = 'clean' AND s.deleted_at IS NULL",
        (user_id,),
    ).fetchall()
    passes = []
    for r in rows:
        p = dict(r)
        p["vehicle"] = f"c{p['car_id']}" if p["car_id"] is not None else f"d{p['device_ref']}"
        passes.append(p)
    trips = build_trips(passes)

    tags = [dict(r) for r in conn.execute(
        "SELECT id, signature, window_start_min, window_end_min FROM toll_patterns "
        "WHERE user_id = ? AND ignored = 0 ORDER BY created_at, id", (user_id,))]
    overrides = {r["trip_key"]: r["pattern_id"] for r in conn.execute(
        "SELECT trip_key, pattern_id FROM toll_trip_overrides WHERE user_id = ?", (user_id,))}

    conn.execute("DELETE FROM toll_trips WHERE user_id = ?", (user_id,))
    pass_trip: list[tuple[int, int]] = []
    for trip in trips:
        if trip["trip_key"] in overrides:
            pattern_id = overrides[trip["trip_key"]]     # a manual choice always wins (None = "not regular")
        else:
            pattern_id = next((t["id"] for t in tags if trip_matches(t, trip)), None)   # earliest-created tag wins
        cur = conn.execute(
            "INSERT INTO toll_trips (user_id, vehicle, trip_key, started_at, ended_at, signature, pass_count, amount, pattern_id) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (user_id, trip["vehicle"], trip["trip_key"], trip["started_at"], trip["ended_at"], trip["signature"],
             trip["pass_count"], trip["amount"], pattern_id),
        )
        pass_trip.extend((cur.lastrowid, pid) for pid in trip["pass_ids"])
    conn.executemany("UPDATE toll_transactions SET trip_id = ? WHERE id = ?", pass_trip)
    conn.commit()
    return len(trips)


# ------------------------------------------------------------------ suggestions

def suggest_regular(trips: list[dict], ignored: list[dict], min_trips: int | None = None) -> list[dict]:
    """Groups of trips that look like a regular journey. `trips` are the candidates (the caller passes
    only trips with no tag and no manual choice); `ignored` are dismissed suggestions (signature +
    window) that must not come back. A group must have at least `min_trips` trips (default MIN_TRIPS,
    read at call time: more than 30)."""
    min_trips = MIN_TRIPS if min_trips is None else min_trips
    groups: dict[str, list[dict]] = defaultdict(list)
    for t in trips:
        groups[t["signature"]].append(t)

    suggestions = []
    for signature, items in groups.items():
        items = sorted(items, key=lambda t: minute_of_day(t["started_at"]))
        clusters: list[list[dict]] = [[items[0]]]
        for t in items[1:]:
            if minute_of_day(t["started_at"]) - minute_of_day(clusters[-1][-1]["started_at"]) > CLUSTER_GAP_MIN:
                clusters.append([t])
            else:
                clusters[-1].append(t)

        for cluster in clusters:
            if len(cluster) < min_trips:
                continue
            days_by_week: dict[tuple, set] = defaultdict(set)
            for t in cluster:
                day = datetime.strptime(t["started_at"], _FMT).date()
                days_by_week[day.isocalendar()[:2]].add(day)
            best_week = max(len(days) for days in days_by_week.values())
            if best_week < MIN_DAYS_IN_WEEK:
                continue

            minutes = [minute_of_day(t["started_at"]) for t in cluster]
            start, end = max(0, min(minutes) - WINDOW_PAD_MIN), min(1439, max(minutes) + WINDOW_PAD_MIN)
            if any(i["signature"] == signature and i["window_start_min"] <= end and start <= i["window_end_min"] for i in ignored):
                continue

            direction = signature_items(signature)[0].rpartition("|")[2].lower()
            total = round(sum(t["amount"] for t in cluster), 2)
            suggestions.append({
                "signature": signature, "route": pretty_signature(signature),
                "window_start_min": start, "window_end_min": end,
                "trip_count": len(cluster), "days_per_week": best_week,
                "total": total, "average": round(total / len(cluster), 2),
                "vehicles": sorted({t["vehicle"] for t in cluster}),
                "name": f"{'Morning' if min(minutes) < 720 else 'Evening'} {direction}".strip(),
            })
    suggestions.sort(key=lambda s: (-s["trip_count"], s["window_start_min"], s["signature"]))
    return suggestions
