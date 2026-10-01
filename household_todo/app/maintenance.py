"""Maintenance (SPEC §14): recurring house upkeep that can be marked done, one-off jobs in the
shared Maintenance list, suggestions from a catalogue filtered by the home profile, and who is told.

Everything computed rather than stored lives here (due dates, status, recipients, suggestions), so the
router, the calendar, the notifications and the sensors agree. No FastAPI imports except HTTPException
for readable 4xx messages.

Due dates
- interval: last_done + N units (months clamp to the month's last day); never done → start_date.
- calendar: the first rule occurrence after `cleared_through` (the last one marked done), from anchor_date.
- A snooze moves one due date (snooze_due → snooze_to); marking done clears it.
- Paused items have no due date.
Status: overdue (due < today), due (within lead_days), upcoming, or paused.
"""
import json
import re
import unicodedata
from calendar import monthrange
from datetime import date, timedelta

from starlette.exceptions import HTTPException

from . import config, db, links, maint_catalog as cat, recurrence, settings

MAX_ITEMS = 200
MAX_PROJECTED = 24
UPCOMING_DAYS = 90
ENTITY_PREFIX = "binary_sensor.household_todo_maintenance_"
OVERDUE_SENSOR = "sensor.household_todo_maintenance_overdue"
LIST_ROLE = "maintenance"
_EMOJI_MAX = 8


def enabled() -> bool:
    return bool(settings.get("maintenance_enabled"))


def south() -> bool:
    return cat.is_south(config.LATITUDE)


def require_enabled() -> None:
    if not enabled():
        raise HTTPException(409, "Maintenance is turned off. An admin can turn it on under Admin → Maintenance.")


# ---------------------------------------------------------------------------
# dates
# ---------------------------------------------------------------------------
def add_interval(d: date, n: int, unit: str) -> date:
    if unit == "day":
        return d + timedelta(days=n)
    if unit == "week":
        return d + timedelta(weeks=n)
    months = n * (12 if unit == "year" else 1)
    y, m = divmod(d.month - 1 + months, 12)
    y += d.year
    m += 1
    return date(y, m, min(d.day, monthrange(y, m)[1]))


def last_occurrence_on_or_before(rule: str, anchor, d: date) -> date | None:
    anchor = recurrence.parse_date(anchor)
    if anchor is None or d < anchor:
        return None
    x = d
    for _ in range(3700 * 2):
        if x < anchor:
            return None
        if recurrence.occurs_on(rule, anchor, x):
            return x
        x -= timedelta(days=1)
    return None


def _d(v) -> date | None:
    return recurrence.parse_date(v) if v else None


def raw_due(item: dict) -> date | None:
    """The due date before any snooze (None when paused)."""
    if item["paused"]:
        return None
    if item["mode"] == "interval":
        last = _d(item["last_done"])
        if last:
            return add_interval(last, item["every_n"], item["every_unit"])
        return _d(item["start_date"]) or config.to_local_date(item["created_at"]) or config.today()
    cleared = _d(item["cleared_through"])
    start = cleared + timedelta(days=1) if cleared else _d(item["anchor_date"])
    return recurrence.next_rule_occurrence(item["rule"], item["anchor_date"], start) if start else None


def due_date(item: dict) -> date | None:
    d = raw_due(item)
    if d and item.get("snooze_due") == d.isoformat() and item.get("snooze_to"):
        return _d(item["snooze_to"]) or d
    return d


def status_of(item: dict, today: date) -> tuple[str, date | None]:
    d = due_date(item)
    if d is None:
        return "paused", None
    if d < today:
        return "overdue", d
    if (d - today).days <= item["lead_days"]:
        return "due", d
    return "upcoming", d


def next_after(item: dict, d: date) -> date | None:
    """The due date that would follow `d` if it were done on `d` (for the calendar's projections)."""
    if item["mode"] == "interval":
        return add_interval(d, item["every_n"], item["every_unit"])
    return recurrence.next_rule_occurrence(item["rule"], item["anchor_date"], d + timedelta(days=1))


def projected(item: dict, start: date, end: date, today: date) -> list[tuple[date, bool]]:
    """(date, is_the_real_due_date) in [start, end]: the due date (an overdue one shows on today) and the
    ones after it, as they'd fall if each were done on time."""
    d = due_date(item)
    out = []
    if d is None:
        return out
    shown = max(d, today) if d < today else d
    if start <= shown <= end:
        out.append((shown, True))
    x = d
    for _ in range(MAX_PROJECTED):
        x = next_after(item, x)
        if x is None or x > end:
            break
        if x >= start and x > shown:
            out.append((x, False))
    return out


def repeat_label(item: dict) -> str:
    if item["mode"] == "interval":
        return cat.every_label(item["every_n"], item["every_unit"]) + " after last done"
    return recurrence.describe_rule(item["rule"])


# ---------------------------------------------------------------------------
# the Maintenance list (one-off jobs)
# ---------------------------------------------------------------------------
def list_id(conn) -> str | None:
    row = conn.execute("SELECT id FROM lists WHERE role = ? ORDER BY created_at LIMIT 1", (LIST_ROLE,)).fetchone()
    return row["id"] if row else None


def ensure_list(conn) -> str:
    lid = list_id(conn)
    if lid:
        return lid
    from . import taskview
    lid = db.new_id()
    conn.execute("INSERT INTO lists (id, name, kind, owner_user_id, created_at, position, role) "
                 "VALUES (?, 'Maintenance', 'shared', NULL, ?, ?, ?)",
                 (lid, config.now_iso(), taskview.next_list_position(conn, "shared", None), LIST_ROLE))
    return lid


# ---------------------------------------------------------------------------
# recipients
# ---------------------------------------------------------------------------
def admin_ids(conn) -> list[str]:
    out = []
    for r in conn.execute("SELECT id, username FROM users WHERE disabled = 0 ORDER BY name COLLATE NOCASE"):
        if {str(x).strip().lower() for x in (r["id"], r["username"]) if x} & config.ADMIN_NAMES:
            out.append(r["id"])
    return out


def default_recipients() -> list[str]:
    return list(settings.get("maintenance_recipients") or [])


def chosen_recipients(conn, item: dict) -> list[str]:
    """Who the item is set to tell (before muting/disabled): its own list or the household default, plus
    its assignee."""
    if item["recipients_mode"] == "custom":
        ids = [r["user_id"] for r in conn.execute("SELECT user_id FROM maint_recipients WHERE item_id = ?", (item["id"],))]
    else:
        ids = default_recipients()
    if item.get("assigned_to") and item["assigned_to"] not in ids:
        ids = ids + [item["assigned_to"]]
    return ids


def eligible(conn, user_ids) -> list[dict]:
    """Of these, the enabled users who haven't muted maintenance (Settings → Reminders)."""
    ids = list(dict.fromkeys(user_ids))
    if not ids:
        return []
    rows = conn.execute(
        f"SELECT u.id, u.name, u.username, p.digest_time, COALESCE(p.maintenance_notify, 1) AS on_ "
        f"FROM users u LEFT JOIN user_prefs p ON p.user_id = u.id WHERE u.id IN ({','.join('?' * len(ids))}) "
        "AND u.disabled = 0", ids).fetchall()
    return [dict(r) for r in rows if r["on_"]]


# ---------------------------------------------------------------------------
# JSON
# ---------------------------------------------------------------------------
def lookups(conn) -> dict:
    users = {r["id"]: r["name"] for r in conn.execute("SELECT id, name FROM users")}
    places = {r["id"]: dict(r) for r in conn.execute("SELECT id, name, address, drive_minutes FROM places")}
    custom = {r["item_id"]: [] for r in conn.execute("SELECT DISTINCT item_id FROM maint_recipients")}
    for r in conn.execute("SELECT item_id, user_id FROM maint_recipients"):
        custom[r["item_id"]].append(r["user_id"])
    files: dict[str, list] = {}
    for r in conn.execute("SELECT * FROM maint_files WHERE item_id IS NOT NULL ORDER BY created_at"):
        files.setdefault(r["item_id"], []).append(file_json(r))
    last: dict[str, dict] = {}
    counts: dict[str, int] = {}
    for r in conn.execute("SELECT item_id, done_on, done_by, created_at FROM maint_done ORDER BY done_on, created_at"):
        last[r["item_id"]] = dict(r)
        counts[r["item_id"]] = counts.get(r["item_id"], 0) + 1
    return {"users": users, "places": places, "custom": custom, "files": files, "last": last, "counts": counts}


def file_json(r) -> dict:
    return {"id": r["id"], "name": r["name"], "size": r["size"], "mime": r["mime"], "thumb": bool(r["thumb"]),
            "createdAt": r["created_at"]}


def entity_id(item: dict) -> str:
    return ENTITY_PREFIX + item["entity_slug"]


def serialize(item: dict, today: date, lk: dict) -> dict:
    st, due = status_of(item, today)
    place = lk["places"].get(item.get("place_id"))
    last = lk["last"].get(item["id"])
    recips = lk["custom"].get(item["id"], []) if item["recipients_mode"] == "custom" else default_recipients()
    return {
        "id": item["id"], "name": item["name"], "icon": item["icon"], "category": item["category"],
        "categoryLabel": cat.CATEGORIES.get(item["category"], "Other"),
        "notes": item["notes"], "howto": item["howto"], "url": item["url"],
        "mode": item["mode"], "everyN": item["every_n"], "everyUnit": item["every_unit"],
        "rule": item["rule"], "anchorDate": item["anchor_date"], "repeatLabel": repeat_label(item),
        "lastDone": item["last_done"] if item["mode"] == "interval" else (last["done_on"] if last else None),
        "startDate": item["start_date"], "clearedThrough": item["cleared_through"],
        "leadDays": item["lead_days"], "overdueEvery": item["overdue_every"],
        "placeId": item["place_id"] if place else None,
        "place": {"id": place["id"], "name": place["name"], "address": place["address"]} if place else None,
        "assignedTo": item["assigned_to"], "assigneeName": lk["users"].get(item["assigned_to"]),
        "recipientsMode": item["recipients_mode"], "recipients": recips,
        "recipientNames": [lk["users"][u] for u in recips if u in lk["users"]],
        "paused": bool(item["paused"]),
        "snoozedTo": item["snooze_to"] if item.get("snooze_to") and item.get("snooze_due") == (raw_due(item).isoformat() if raw_due(item) else None) else None,
        "status": st, "dueDate": due.isoformat() if due else None,
        "daysUntil": (due - today).days if due else None,
        "exposeSensor": bool(item["expose_sensor"]), "entityId": entity_id(item),
        "suggestionKey": item["suggestion_key"],
        "files": lk["files"].get(item["id"], []),
        "lastRecord": {"date": last["done_on"], "by": lk["users"].get(last["done_by"])} if last else None,
        "doneCount": lk["counts"].get(item["id"], 0),
        "createdAt": item["created_at"],
    }


def load_item(conn, item_id: str) -> dict:
    row = conn.execute("SELECT * FROM maint_items WHERE id = ?", (item_id,)).fetchone()
    if not row:
        raise HTTPException(404, "That maintenance item doesn't exist (any more).")
    return dict(row)


def all_items(conn) -> list[dict]:
    return [dict(r) for r in conn.execute("SELECT * FROM maint_items ORDER BY name COLLATE NOCASE")]


# ---------------------------------------------------------------------------
# validation
# ---------------------------------------------------------------------------
_ICON_RE = re.compile(r"^[^\sA-Za-z0-9<>&\"']{1,8}$")


def _text(body, key, limit, required=False, label=None):
    v = body.get(key)
    if v is None or (isinstance(v, str) and not v.strip()):
        if required:
            raise HTTPException(422, f"{label or key} is required.")
        return None
    if not isinstance(v, str) or len(v.strip()) > limit:
        raise HTTPException(422, f"{label or key} must be text of at most {limit} characters.")
    return v.strip()


def _int(v, lo, hi, label):
    if isinstance(v, bool) or not isinstance(v, int) or not lo <= v <= hi:
        raise HTTPException(422, f"{label} must be a whole number from {lo} to {hi}.")
    return v


def clean_icon(v):
    if v in (None, ""):
        return None
    if not isinstance(v, str) or not _ICON_RE.match(v.strip()):
        raise HTTPException(422, "The icon must be an emoji.")
    return v.strip()


def clean_every(n, unit):
    if unit not in cat.UNITS:
        raise HTTPException(422, "every_unit must be day, week, month or year.")
    lo, hi = cat.UNITS[unit]
    return _int(n, lo, hi, f"Every … {unit}s"), unit


def make_slug(conn, name: str, exclude: str | None = None) -> str:
    base = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode()
    base = re.sub(r"[^a-z0-9]+", "_", base.lower()).strip("_")[:40].strip("_") or "item"
    slug, n = base, 1
    while conn.execute("SELECT 1 FROM maint_items WHERE entity_slug = ? AND id IS NOT ?", (slug, exclude)).fetchone():
        n += 1
        slug = f"{base}_{n}"
    return slug


def clean_item(conn, body: dict, current: dict | None, acting: dict, today: date) -> tuple[dict, list | None]:
    """Validate a create (current None) or a partial update → (column values to write, custom recipients
    or None when unchanged)."""
    if not isinstance(body, dict):
        raise HTTPException(422, "Send an object.")
    known = {"name", "icon", "category", "notes", "howto", "url", "mode", "every_n", "every_unit", "rule",
             "anchor_date", "last_done", "start_date", "lead_days", "overdue_every", "place_id", "assigned_to",
             "recipients_mode", "recipients", "paused", "expose_sensor", "suggestion_key"}
    unknown = sorted(set(body) - known)
    if unknown:
        raise HTTPException(422, "Unknown field(s): " + ", ".join(unknown))
    out: dict = {}
    creating = current is None
    if creating or "name" in body:
        name = _text(body, "name", 60, required=True, label="Name")
        out["name"] = name
    if "icon" in body:
        out["icon"] = clean_icon(body["icon"])
    if creating or "category" in body:
        c = body.get("category") or "other"
        if c not in cat.CATEGORIES:
            raise HTTPException(422, "Unknown category.")
        out["category"] = c
    for key, limit, label in (("notes", 1000, "Notes"), ("howto", 2000, "How to")):
        if key in body:
            out[key] = _text(body, key, limit, label=label)
    if "url" in body:
        try:
            out["url"] = links.clean_url(body["url"])
        except ValueError as e:
            raise HTTPException(422, str(e))
    mode = body.get("mode", current["mode"] if current else "interval")
    if mode not in ("interval", "calendar"):
        raise HTTPException(422, "mode must be interval or calendar.")
    changing_mode = creating or ("mode" in body and mode != current["mode"])
    if mode == "interval":
        if changing_mode or "every_n" in body or "every_unit" in body:
            n, unit = clean_every(body.get("every_n", current and current["every_n"]),
                                  body.get("every_unit", current and current["every_unit"]))
            out.update(every_n=n, every_unit=unit)
        for key, label in (("last_done", "Last done"), ("start_date", "First due")):
            if key in body and body[key] is not None:
                d = recurrence.parse_date(body[key])
                if d is None:
                    raise HTTPException(422, f"{label} must be a date (YYYY-MM-DD).")
                if key == "last_done" and d > today:
                    raise HTTPException(422, "Last done can't be in the future.")
                if abs((d - today).days) > 3660:
                    raise HTTPException(422, f"{label} must be within 10 years of today.")
                out[key] = d.isoformat()
            elif key in body:
                out[key] = None
        if changing_mode:
            out.update(rule=None, anchor_date=None, cleared_through=None)
            if not out.get("last_done") and not out.get("start_date"):
                out["start_date"] = today.isoformat()       # never / not sure: due now
    else:
        if changing_mode or "rule" in body or "anchor_date" in body:
            rule = body.get("rule", current and current["rule"])
            anchor = body.get("anchor_date", current and current["anchor_date"])
            err = recurrence.validate_rule(rule, anchor)
            if err:
                raise HTTPException(422, err)
            out.update(rule=rule, anchor_date=anchor)
            if not changing_mode and (rule != current["rule"] or anchor != current["anchor_date"]):
                out["cleared_through"] = None
        if changing_mode:
            out.update(every_n=None, every_unit=None, last_done=None, start_date=None, cleared_through=None)
    out["mode"] = mode
    if "lead_days" in body or creating:
        out["lead_days"] = _int(body.get("lead_days", 7), 0, 60, "Remind ahead (days)")
    if "overdue_every" in body or creating:
        v = body.get("overdue_every", 7)
        if v not in (0, 3, 7, 14) or isinstance(v, bool):
            raise HTTPException(422, "overdue_every must be 0 (never), 3, 7 or 14.")
        out["overdue_every"] = v
    if "place_id" in body:
        pid = body["place_id"]
        if pid is not None and not conn.execute("SELECT 1 FROM places WHERE id = ?", (pid,)).fetchone():
            raise HTTPException(404, "Place not found.")
        out["place_id"] = pid
    if "assigned_to" in body:
        uid = body["assigned_to"]
        if uid is not None:
            u = conn.execute("SELECT disabled FROM users WHERE id = ?", (uid,)).fetchone()
            if not u:
                raise HTTPException(422, "Unknown person.")
            if u["disabled"] and (creating or uid != current["assigned_to"]):
                raise HTTPException(400, "That person is disabled.")
        out["assigned_to"] = uid
    recips = None
    if "recipients_mode" in body or creating:
        rm = body.get("recipients_mode", "default")
        if rm not in ("default", "custom"):
            raise HTTPException(422, "recipients_mode must be default or custom.")
        out["recipients_mode"] = rm
    if "recipients" in body:
        ids = body["recipients"]
        if not isinstance(ids, list) or not all(isinstance(x, str) for x in ids) or len(ids) > 100:
            raise HTTPException(422, "recipients must be a list of user ids.")
        ids = list(dict.fromkeys(ids))
        known_ids = {r["id"] for r in conn.execute("SELECT id FROM users")}
        if any(x not in known_ids for x in ids):
            raise HTTPException(422, "recipients contains an unknown person.")
        recips = ids
    for key in ("paused", "expose_sensor"):
        if key in body:
            if not isinstance(body[key], bool):
                raise HTTPException(422, f"{key} must be true or false.")
            out[key] = int(body[key])
    if "suggestion_key" in body and creating:
        k = body["suggestion_key"]
        if k is not None and (not isinstance(k, str) or not re.match(r"^[bc]:[A-Za-z0-9_]{1,64}$", k)):
            raise HTTPException(422, "Unknown suggestion.")
        out["suggestion_key"] = k
    return out, recips


def state_snapshot(item: dict) -> str:
    return json.dumps({k: item.get(k) for k in ("last_done", "start_date", "cleared_through", "snooze_due", "snooze_to")})


# ---------------------------------------------------------------------------
# suggestions
# ---------------------------------------------------------------------------
def custom_suggestions(conn) -> list[dict]:
    out = []
    for r in conn.execute("SELECT * FROM maint_suggestions ORDER BY name COLLATE NOCASE"):
        out.append({"key": "c:" + r["id"], "id": r["id"], "name": r["name"], "icon": r["icon"],
                    "category": r["category"], "needs": r["needs"],
                    "every": (r["every_n"], r["every_unit"]) if r["every_n"] else None,
                    "seasons": json.loads(r["seasons"]) if r["seasons"] else None,
                    "why": r["why"], "steps": [s for s in (r["howto"] or "").split("\n") if s.strip()],
                    "who": r["who"], "minutes": r["minutes"], "custom": True})
    return out


def all_suggestions(conn) -> list[dict]:
    return [dict(s, key="b:" + s["key"], custom=False) for s in cat.BUILTIN] + custom_suggestions(conn)


def suggestion_json(s: dict, today: date, sth: bool) -> dict:
    out = {"key": s["key"], "name": s["name"], "icon": s["icon"], "category": s["category"],
           "categoryLabel": cat.CATEGORIES.get(s["category"], "Other"), "needs": s["needs"],
           "needsLabel": cat.FEATURES.get(s["needs"]) if s["needs"] else None,
           "why": s["why"], "steps": s["steps"], "who": s["who"], "minutes": s["minutes"],
           "custom": s["custom"], "repeatLabel": cat.repeat_label(s),
           "seasons": s["seasons"], "inSeason": cat.in_season_soon(s["seasons"], today, sth)}
    # what the Add form is pre-filled with
    if s["seasons"]:
        rule, anchor = cat.calendar_rule_for(s["seasons"], today, sth)
        out["defaults"] = {"mode": "calendar", "rule": rule, "anchor_date": anchor}
    else:
        n, unit = s["every"]
        out["defaults"] = {"mode": "interval", "every_n": n, "every_unit": unit}
    if s.get("id"):
        out["id"] = s["id"]
        out["everyN"], out["everyUnit"] = (s["every"] or (None, None))
    return out


def suggestions(conn, today: date) -> dict:
    """Those that fit the home profile, aren't set up and aren't hidden (current season first), plus the
    hidden ones for "Show hidden"."""
    profile = set(settings.get("maintenance_profile") or [])
    hidden = {r["key"] for r in conn.execute("SELECT key FROM maint_hidden")}
    added = set()
    names = set()
    for r in conn.execute("SELECT suggestion_key, name FROM maint_items"):
        added.add(r["suggestion_key"])
        names.add(r["name"].strip().lower())
    sth = south()
    shown, hid = [], []
    for s in all_suggestions(conn):
        if s["needs"] and s["needs"] not in profile:
            continue
        if s["key"] in added or s["name"].strip().lower() in names:     # already set up (from here, or by hand)
            continue
        (hid if s["key"] in hidden else shown).append(suggestion_json(s, today, sth))
    shown.sort(key=lambda x: (not x["inSeason"], x["categoryLabel"], x["name"].lower()))
    hid.sort(key=lambda x: x["name"].lower())
    season = cat.season_of(today, sth)
    return {"season": season, "seasonLabel": cat.SEASON_LABEL[season], "south": sth, "items": shown, "hidden": hid}


def season_suggestions(conn, today: date) -> list[dict]:
    """Suggestions for the season that is starting (for the season's notification)."""
    sth = south()
    season = cat.season_of(today, sth)
    return [s for s in suggestions(conn, today)["items"] if s["seasons"] and season in s["seasons"]]
