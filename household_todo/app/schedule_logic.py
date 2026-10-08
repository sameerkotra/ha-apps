"""Everything about a schedule item that is computed rather than stored:
its effective occurrences (rule + exceptions, §8k), sensor state (§8f), the
"next pickups" list and calendar entries. No FastAPI here, so the schedule
router, the calendar, the dashboard, the reminders and the sensor loop all
share one implementation and it can be tested directly.

An item may belong to one person (`assigned_to`), be private
to them (`visibility='private'`), and have a start/end time. A timed item's
sensor is on only inside that window on each effective date (SPEC §7.2).

A household item can also be taken in turns (`rotation`, SPEC §5.4b): the
rule's occurrences go to the people in order, counted from the anchor date
(the first occurrence is the first person's), so skipping a week doesn't
change whose week the next one is. A moved occurrence stays the turn of
whoever had its original date; an extra date is the turn of whoever has the
next rule occurrence. One turn can be handed to someone else
(`schedule_turns`, attached to the item as `turns` by attach_turns()).
"""
import json
import re
from functools import lru_cache
import unicodedata
from datetime import date, datetime, time, timedelta

from . import config, recurrence, settings

ENTITY_PREFIX = "binary_sensor.household_todo_"
DEFAULT_ICON = "mdi:calendar-clock"
ICON_RE = re.compile(r"^mdi:[a-z0-9-]+$")
MAX_ITEMS = 100
MAX_EXCEPTIONS_PER_ITEM = 50
MAX_DAYS_AHEAD = 366


def entity_id(slug: str) -> str:
    return f"{ENTITY_PREFIX}{slug}"


# ---------------------------------------------------------------------------
# Visibility (§4): a private item exists only for its assignee — an admin
# sees it only while acting as them. Household items are visible to all.
# ---------------------------------------------------------------------------

# For queries on schedule_items (unaliased); bind the acting user's id.
VISIBLE_SQL = "(visibility = 'household' OR assigned_to = ?)"


def is_visible(item: dict, user_id: str) -> bool:
    return item.get("visibility") != "private" or item.get("assigned_to") == user_id


def can_edit(item: dict, user_id: str) -> bool:
    """Household items (assigned or not) are editable by anyone; a private
    one only by its assignee."""
    return item.get("visibility") != "private" or item.get("assigned_to") == user_id


def is_timed(item: dict) -> bool:
    return bool(item.get("start_time") and item.get("end_time"))


def published(item: dict) -> bool:
    """Mirrored to Home Assistant: the global App setting AND the item's switch."""
    return bool(settings.get("expose_schedule_sensors") and item.get("expose_sensor", 1))


def time_range(item: dict) -> str | None:
    """"18:00–19:00", or None for an all-day item."""
    return f"{item['start_time']}–{item['end_time']}" if is_timed(item) else None


def lookups(conn) -> tuple[dict, dict]:
    """(user id -> display name, place id -> place row as dict) for the JSON
    and sensor attributes."""
    users = {r["id"]: r["name"] for r in conn.execute("SELECT id, name FROM users")}
    places = {r["id"]: dict(r) for r in conn.execute("SELECT id, name, address, phone, drive_minutes FROM places")}
    return users, places


def make_slug(conn, name: str) -> str:
    """Lowercase ASCII, runs of other characters -> '_', trimmed, max 40
    characters, 'item' if nothing survives, _2/_3... on a collision (§8f)."""
    ascii_name = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode()
    base = re.sub(r"[^a-z0-9]+", "_", ascii_name.lower()).strip("_")[:40].strip("_") or "item"
    slug, n = base, 1
    while conn.execute("SELECT 1 FROM schedule_items WHERE entity_slug = ?", (slug,)).fetchone():
        n += 1
        slug = f"{base}_{n}"
    return slug


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------

def load_exceptions(conn, item_id: str | None = None) -> dict[str, list[dict]]:
    """item_id -> that item's exception rows (as dicts). One item or all."""
    if item_id is None:
        rows = conn.execute("SELECT * FROM schedule_exceptions ORDER BY date").fetchall()
    else:
        rows = conn.execute("SELECT * FROM schedule_exceptions WHERE item_id = ? ORDER BY date", (item_id,)).fetchall()
    out: dict[str, list[dict]] = {}
    for r in rows:
        out.setdefault(r["item_id"], []).append(dict(r))
    return out


# ---------------------------------------------------------------------------
# Taking turns (SPEC §5.4b)
# ---------------------------------------------------------------------------

MAX_ROTATION = 12


def rotation_of(item: dict) -> list[str]:
    """The people taking turns, in order ([] when the item isn't taken in turns)."""
    try:
        r = json.loads(item.get("rotation") or "null")
    except (TypeError, ValueError):
        return []
    return [u for u in r if isinstance(u, str)] if isinstance(r, list) else []


def attach_turns(conn, items: list[dict]) -> list[dict]:
    """Give each rotating item its handed-over turns: item["turns"] = {iso date: user id}."""
    ids = [i["id"] for i in items if i.get("rotation")]
    if ids:
        by: dict[str, dict] = {}
        q = f"SELECT item_id, date, user_id FROM schedule_turns WHERE item_id IN ({','.join('?' * len(ids))})"
        for r in conn.execute(q, ids):
            by.setdefault(r["item_id"], {})[r["date"]] = r["user_id"]
        for i in items:
            if i.get("rotation"):
                i["turns"] = by.get(i["id"], {})
    return items


@lru_cache(maxsize=4096)
def _occurrences_before(rule: str, anchor: str, day: date) -> int:
    a = recurrence.parse_date(anchor)
    if a is None or day <= a:
        return 0
    return len(recurrence.rule_occurrences_between(rule, a, a, day - timedelta(days=1)))


def turn_on(item: dict, day) -> str | None:
    """Whose turn the occurrence whose rule date is `day` is (None when the item isn't taken in turns)."""
    rot = rotation_of(item)
    d = recurrence.parse_date(day)
    if not rot or d is None:
        return None
    handed = (item.get("turns") or {}).get(d.isoformat())
    if handed:
        return handed
    return rot[_occurrences_before(item["rule"], item["anchor_date"], d) % len(rot)]


def effective_turns_between(item: dict, excs: list[dict], start: date, end: date) -> list[tuple[date, str | None]]:
    """(effective date, whose turn) in [start, end] — a moved occurrence keeps its original date's turn."""
    moved_from = {e["to_date"]: e["date"] for e in excs if e["kind"] == "move"}
    return [(d, turn_on(item, moved_from.get(d.isoformat(), d))) for d in effective_dates_between(item, excs, start, end)]


def date_sets(excs: list[dict]) -> tuple[set[date], set[date]]:
    """(removed, added): removed = the date of every skip and move; added =
    the to_date of every move and the date of every add (§8k)."""
    removed: set[date] = set()
    added: set[date] = set()
    for e in excs:
        d = recurrence.parse_date(e["date"])
        if e["kind"] in ("skip", "move") and d:
            removed.add(d)
        if e["kind"] == "add" and d:
            added.add(d)
        if e["kind"] == "move":
            t = recurrence.parse_date(e["to_date"])
            if t:
                added.add(t)
    return removed, added


def effective_dates_between(item: dict, excs: list[dict], start: date, end: date) -> list[date]:
    """Every effective date in [start, end]: rule occurrences minus skips and
    moves, plus move targets and extra dates (§5.4). Sorted."""
    removed, added = date_sets(excs)
    out = {d for d in recurrence.rule_occurrences_between(item["rule"], item["anchor_date"], start, end) if d not in removed}
    out |= {d for d in added if start <= d <= end}
    return sorted(out)


# ---------------------------------------------------------------------------
# Sensor state and derived values
# ---------------------------------------------------------------------------

def _clock(hhmm: str) -> time:
    return datetime.strptime(hhmm, "%H:%M").time()


def _window(item: dict, d: date, tz) -> tuple[datetime, datetime]:
    """A timed item's window on date `d`, in Home Assistant's zone."""
    return (datetime.combine(d, _clock(item["start_time"]), tzinfo=tz),
            datetime.combine(d, _clock(item["end_time"]), tzinfo=tz))


def item_state(item: dict, excs: list[dict], today: date, now: datetime | None = None) -> dict:
    """{nextDate, daysUntil, sensorOn, occursToday, nextStart, nextEnd}.

    All-day items: on from lead_days before an effective date to the end of
    it. Timed items: on only while start <= now < end on an effective date;
    lead_days is ignored. nextDate/daysUntil/occursToday are date-based for
    both (today counts all day). nextStart/nextEnd are the next window that
    hasn't ended yet (null for all-day items). `now` defaults to config.now()
    and should be on `today`."""
    removed, added = date_sets(excs)
    nxt = recurrence.next_on_or_after(item["rule"], item["anchor_date"], today, removed, added)
    if nxt is None:  # unreachable for legal rules; be safe
        return {"nextDate": None, "daysUntil": None, "sensorOn": False, "occursToday": False,
                "nextStart": None, "nextEnd": None}
    days_until = (nxt - today).days
    state = {
        "nextDate": nxt.isoformat(),
        "daysUntil": days_until,
        "sensorOn": days_until <= item["lead_days"],
        "occursToday": days_until == 0,
        "nextStart": None,
        "nextEnd": None,
    }
    if is_timed(item):
        now = now or config.now()
        start, end = _window(item, nxt, now.tzinfo)
        state["sensorOn"] = days_until == 0 and start <= now < end
        if end <= now:   # today's window is over: the next one is on a later date
            later = recurrence.next_on_or_after(item["rule"], item["anchor_date"], nxt + timedelta(days=1), removed, added)
            start, end = _window(item, later, now.tzinfo) if later else (None, None)
        state["nextStart"] = start.isoformat(timespec="seconds") if start else None
        state["nextEnd"] = end.isoformat(timespec="seconds") if end else None
    return state


def _upcoming_dates(excs: list[dict], today: date):
    """Skipped/moved-away original dates and extra dates (added or move
    targets), from today on, soonest first."""
    skipped, extra = [], []
    for e in excs:
        d = recurrence.parse_date(e["date"])
        if e["kind"] in ("skip", "move") and d and d >= today:
            skipped.append(d)
        if e["kind"] == "add" and d and d >= today:
            extra.append(d)
        if e["kind"] == "move":
            t = recurrence.parse_date(e["to_date"])
            if t and t >= today:
                extra.append(t)
    return sorted(skipped), sorted(extra)


def sensor_payload(item: dict, excs: list[dict], today: date, now: datetime | None = None,
                   users_by_id: dict | None = None) -> tuple[str, str, dict]:
    """(entity_id, 'on'|'off', attributes) for Home Assistant (§7.2)."""
    st = item_state(item, excs, today, now)
    skipped, extra = _upcoming_dates(excs, today)
    attrs = {
        "friendly_name": item["name"],
        "icon": item["icon"] or DEFAULT_ICON,
        "next_date": st["nextDate"],
        "days_until": st["daysUntil"],
        "occurs_today": st["occursToday"],
        "lead_days": item["lead_days"],
        "rule": recurrence.describe_rule(item["rule"]),
        "skipped_dates": [d.isoformat() for d in skipped[:10]],
        "extra_dates": [d.isoformat() for d in extra[:10]],
        "assigned_to": (users_by_id or {}).get(item.get("assigned_to")),
        "turn": (users_by_id or {}).get(_next_turn(item, excs, st["nextDate"])),
        "start_time": item.get("start_time"),
        "end_time": item.get("end_time"),
        "next_start": st["nextStart"],
        "next_end": st["nextEnd"],
    }
    return entity_id(item["entity_slug"]), ("on" if st["sensorOn"] else "off"), attrs


def _next_turn(item: dict, excs: list[dict], next_date: str | None) -> str | None:
    """Whose turn the next effective date is (a moved one: its original date's)."""
    if not rotation_of(item) or not next_date:
        return None
    moved_from = {e["to_date"]: e["date"] for e in excs if e["kind"] == "move"}
    return turn_on(item, moved_from.get(next_date, next_date))


def upcoming(item: dict, excs: list[dict], today: date, users_by_id: dict, count: int = 6) -> list[dict]:
    """The next `count` *displayed* dates on or after today: rule occurrences
    (including skipped and moved-away ones) merged with added / moved-here
    dates (§8k)."""
    skips = {e["date"]: e for e in excs if e["kind"] == "skip"}
    moves = {e["date"]: e for e in excs if e["kind"] == "move"}
    adds = {e["date"]: e for e in excs if e["kind"] == "add"}
    targets = {e["to_date"]: e for e in excs if e["kind"] == "move"}

    def by(e):
        return users_by_id.get(e.get("created_by"))

    out = []
    for d in recurrence.next_rule_occurrences(item["rule"], item["anchor_date"], today, count):
        iso = d.isoformat()
        if iso in skips:
            e = skips[iso]
            out.append({"date": iso, "status": "skipped", "exceptionId": e["id"], "reason": e["reason"], "by": by(e)})
        elif iso in moves:
            e = moves[iso]
            out.append({"date": iso, "status": "moved_away", "exceptionId": e["id"], "reason": e["reason"],
                        "by": by(e), "movedTo": e["to_date"]})
        else:
            out.append({"date": iso, "status": "normal"})
    for iso, e in adds.items():
        if iso >= today.isoformat():
            out.append({"date": iso, "status": "added", "exceptionId": e["id"], "reason": e["reason"], "by": by(e)})
    for iso, e in targets.items():
        if iso >= today.isoformat():
            out.append({"date": iso, "status": "moved_here", "exceptionId": e["id"], "reason": e["reason"],
                        "by": by(e), "movedFrom": e["date"]})
    out.sort(key=lambda x: x["date"])
    out = out[:count]
    if rotation_of(item):
        for x in out:
            uid = turn_on(item, x.get("movedFrom") or x["date"])
            x["turn"], x["turnName"] = uid, users_by_id.get(uid)
            x["handedOver"] = bool((item.get("turns") or {}).get(x.get("movedFrom") or x["date"]))
    return out


def place_json(place: dict | None) -> dict | None:
    if not place:
        return None
    drive = place["drive_minutes"] if settings.drive_times_enabled() else None   # "Drive times" off: none shown
    return {"id": place["id"], "name": place["name"], "address": place["address"], "driveMinutes": drive}


def serialize_item(item: dict, excs: list[dict], today: date, users_by_id: dict,
                   places_by_id: dict | None = None, now: datetime | None = None) -> dict:
    st = item_state(item, excs, today, now)
    place = (places_by_id or {}).get(item.get("place_id"))
    ups = upcoming(item, excs, today, users_by_id)
    rot = rotation_of(item)
    return {
        "id": item["id"],
        "name": item["name"],
        "notes": item["notes"],
        "url": item.get("url"),
        "rule": item["rule"],
        "ruleLabel": recurrence.describe_rule(item["rule"]),
        "anchorDate": item["anchor_date"],
        "leadDays": item["lead_days"],
        "icon": item["icon"],
        "entitySlug": item["entity_slug"],
        "entityId": entity_id(item["entity_slug"]),
        "minGapDays": recurrence.min_gap_days(item["rule"]),
        "createdBy": users_by_id.get(item["created_by"]),
        "createdAt": item["created_at"],
        "assignedTo": item.get("assigned_to"),
        "assigneeName": users_by_id.get(item.get("assigned_to")),
        "startTime": item.get("start_time"),
        "endTime": item.get("end_time"),
        "placeId": item.get("place_id") if place else None,
        "place": place_json(place),
        "visibility": item.get("visibility", "household"),
        "exposeSensor": bool(item.get("expose_sensor", 1)),
        "published": published(item),
        **st,
        "upcoming": ups,
        "rotation": rot,
        "rotationNames": [users_by_id.get(u) for u in rot],
        "nextTurn": _next_turn(item, excs, st["nextDate"]),
        "nextTurnName": users_by_id.get(_next_turn(item, excs, st["nextDate"])),
    }


def calendar_entries(item: dict, excs: list[dict], start: date, end: date, users_by_id: dict | None = None) -> list[dict]:
    """Every schedule occurrence in [start, end] including the exceptions
    that touch it, so they can be drawn and undone (§8j). Moves keep the
    item's times."""
    skips = {e["date"]: e for e in excs if e["kind"] == "skip"}
    moves = {e["date"]: e for e in excs if e["kind"] == "move"}
    adds = {e["date"]: e for e in excs if e["kind"] == "add"}
    targets = {e["to_date"]: e for e in excs if e["kind"] == "move"}
    base = {"itemId": item["id"], "name": item["name"], "icon": item["icon"] or DEFAULT_ICON,
            "startTime": item.get("start_time"), "endTime": item.get("end_time"),
            "assignedTo": item.get("assigned_to"), "assigneeName": (users_by_id or {}).get(item.get("assigned_to")),
            "private": item.get("visibility") == "private", "url": item.get("url")}
    out = []
    for d in recurrence.rule_occurrences_between(item["rule"], item["anchor_date"], start, end):
        iso = d.isoformat()
        if iso in skips:
            out.append({**base, "date": iso, "status": "skipped", "exceptionId": skips[iso]["id"]})
        elif iso in moves:
            out.append({**base, "date": iso, "status": "moved_away", "exceptionId": moves[iso]["id"],
                        "movedTo": moves[iso]["to_date"]})
        else:
            out.append({**base, "date": iso, "status": "normal"})
    for iso, e in adds.items():
        if start.isoformat() <= iso <= end.isoformat():
            out.append({**base, "date": iso, "status": "added", "exceptionId": e["id"]})
    for iso, e in targets.items():
        if start.isoformat() <= iso <= end.isoformat():
            out.append({**base, "date": iso, "status": "moved_here", "exceptionId": e["id"], "movedFrom": e["date"]})
    if rotation_of(item):
        for e in out:
            uid = turn_on(item, e.get("movedFrom") or e["date"])
            e.update(assignedTo=uid, assigneeName=(users_by_id or {}).get(uid), turn=True)
    out.sort(key=entry_sort_key)
    return out


def entry_sort_key(e: dict):
    """Within a day: all-day entries first, then timed ones by start time,
    then by name — the same order as tasks (§5.1)."""
    return (e["date"], e.get("startTime") is not None, e.get("startTime") or "", e["name"].lower())


# ---------------------------------------------------------------------------
# Exception validation (§8k) — returns an error message or None
# ---------------------------------------------------------------------------

def validate_exception(item: dict, excs: list[dict], kind, date_s, to_date_s, reason, today: date) -> str | None:
    """`excs` is the item's current exceptions. The exception being created
    replaces any existing skip/move for the same original date, so that one
    is left out of the checks."""
    if kind not in ("skip", "move", "add"):
        return "kind must be skip, move or add."
    d = recurrence.parse_date(date_s)
    if d is None:
        return "date must be a valid date (YYYY-MM-DD)."
    if reason is not None and (not isinstance(reason, str) or len(reason.strip()) > 60):
        return "reason must be at most 60 characters."
    limit = today + timedelta(days=MAX_DAYS_AHEAD)
    dates = [d]
    t = None
    if kind == "move":
        t = recurrence.parse_date(to_date_s)
        if t is None:
            return "A move needs a to_date (YYYY-MM-DD)."
        if t == d:
            return "to_date must differ from date."
        dates.append(t)
    elif to_date_s not in (None, ""):
        return "to_date is only used with a move."
    for x in dates:
        if x < today:
            return "Dates must be today or later."
        if x > limit:
            return f"Dates can be at most {MAX_DAYS_AHEAD} days ahead."

    replaced = None
    if kind in ("skip", "move"):
        replaced = next((e for e in excs if e["kind"] in ("skip", "move") and e["date"] == d.isoformat()), None)
    others = [e for e in excs if e is not replaced]

    if kind in ("skip", "move") and not recurrence.occurs_on(item["rule"], item["anchor_date"], d):
        return "That date is not an occurrence of this item."

    removed, added = date_sets(others)
    rule, anchor = item["rule"], item["anchor_date"]

    def effective(x: date) -> bool:
        return recurrence.is_effective(rule, anchor, x, removed, added)

    def is_original_of_exception(x: date) -> bool:
        return any(e["kind"] in ("skip", "move") and e["date"] == x.isoformat() for e in others)

    if kind == "move":
        if effective(t):
            return "That date is already an occurrence — there can't be two on one day."
        if is_original_of_exception(t) or recurrence.occurs_on(rule, anchor, t):
            return "That date is a skipped or moved occurrence — undo that first."
    if kind == "add":
        if effective(d):
            return "That date is already an occurrence — there can't be two on one day."
        if is_original_of_exception(d):
            return "That date is a skipped or moved occurrence — undo that first."

    # cap: 50 exceptions per item, counting the replacement
    if len(others) + 1 > MAX_EXCEPTIONS_PER_ITEM:
        return f"An item can have at most {MAX_EXCEPTIONS_PER_ITEM} exceptions."
    return None
