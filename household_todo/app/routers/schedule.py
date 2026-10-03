"""Schedule items — recurring things that are never completed, each mirrored
to Home Assistant as a binary_sensor (SPEC §5.4, §7.2). Household items
belong to everyone; an item can also belong to one person
(`assigned_to`), have a start/end time and a place, and be private to that
person (SPEC §4, §13)."""
from fastapi import APIRouter, BackgroundTasks, Body, Depends, HTTPException, Query, Response

from .. import config, db, ha_sensors, recurrence, reminders, schedule_logic
from ..auth import get_acting_user, require_admin
from .tasks import TIME_RE, validated_place_id, validated_url

router = APIRouter(prefix="/api", tags=["schedule"])

VISIBILITIES = ("household", "private")


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def _load_item(conn, item_id: str, acting: dict) -> dict:
    """The item, or 404 — also when it's someone else's private item, so
    its existence never leaks."""
    row = conn.execute("SELECT * FROM schedule_items WHERE id = ?", (item_id,)).fetchone()
    if not row or not schedule_logic.is_visible(dict(row), acting["id"]):
        raise HTTPException(404, "Schedule item not found.")
    return dict(row)


def _load_for_edit(conn, item_id: str, acting: dict) -> dict:
    item = _load_item(conn, item_id, acting)
    if not schedule_logic.can_edit(item, acting["id"]):   # defence in depth: visibility already implies it
        raise HTTPException(403, "Only the person this item belongs to can change it.")
    return item


def _item_json(conn, item: dict) -> dict:
    excs = schedule_logic.load_exceptions(conn, item["id"]).get(item["id"], [])
    users, places = schedule_logic.lookups(conn)
    now = config.now()
    return schedule_logic.serialize_item(item, excs, now.date(), users, places, now)


def _name(value) -> str:
    if not isinstance(value, str) or not (1 <= len(value.strip()) <= 60):
        raise HTTPException(422, "A name must be 1–60 characters.")
    return value.strip()


def _notes(value):
    if value is None:
        return None
    if not isinstance(value, str) or len(value) > 500:
        raise HTTPException(422, "Notes must be text of at most 500 characters.")
    return value.strip() or None


def _lead_days(value) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not (0 <= value <= 7):
        raise HTTPException(422, "lead_days must be a whole number from 0 to 7.")
    return value


def _icon(value):
    if value is None or value == "":
        return None
    if not isinstance(value, str) or not schedule_logic.ICON_RE.match(value):
        raise HTTPException(422, "icon must look like mdi:trash-can-outline.")
    return value


def _time(value, field: str):
    if value is None or value == "":
        return None
    if not isinstance(value, str) or not TIME_RE.match(value):
        raise HTTPException(422, f"{field} must be HH:MM in 24-hour form.")
    return value


def _person_fields(conn, body: dict, existing: dict | None, acting: dict) -> dict:
    """Validate the person fields (assignee, times, place, privacy, sensor) present in `body` against the resulting
    item (existing values fill in whatever a PATCH leaves out) and return
    {column: value} for the ones sent."""
    updates: dict = {}
    old = existing or {}

    if "assigned_to" in body:
        uid = body["assigned_to"]
        if uid is not None:
            user = conn.execute("SELECT id, disabled FROM users WHERE id = ?", (uid,)).fetchone() if isinstance(uid, str) else None
            if not user:
                raise HTTPException(422, "That person isn't known to this app.")
            if uid != old.get("assigned_to") and user["disabled"]:
                raise HTTPException(400, "That person is disabled and can't be assigned schedule items.")
        updates["assigned_to"] = uid

    if "start_time" in body:
        updates["start_time"] = _time(body["start_time"], "start_time")
    if "end_time" in body:
        updates["end_time"] = _time(body["end_time"], "end_time")

    if "place_id" in body:
        updates["place_id"] = validated_place_id(conn, body["place_id"])

    if "visibility" in body:
        if body["visibility"] not in VISIBILITIES:
            raise HTTPException(422, "visibility must be 'household' or 'private'.")
        updates["visibility"] = body["visibility"]

    if "url" in body:
        updates["url"] = validated_url(body["url"])

    if "expose_sensor" in body:
        if not isinstance(body["expose_sensor"], bool):
            raise HTTPException(422, "expose_sensor must be true or false.")
        updates["expose_sensor"] = 1 if body["expose_sensor"] else 0

    def eff(col, default=None):
        return updates[col] if col in updates else old.get(col, default)

    start, end = eff("start_time"), eff("end_time")
    if (start is None) != (end is None):
        raise HTTPException(422, "Give both a start and an end time, or neither.")
    if start is not None and end <= start:
        raise HTTPException(422, "The end time must be later than the start time (on the same day).")
    if eff("visibility", "household") == "private":
        if eff("assigned_to") is None:
            raise HTTPException(422, "A private item must belong to someone — pick a person, or make it a household item.")
        # Only the person it's for can hide it (an admin can while acting as them):
        # otherwise anyone could make a household item vanish for everyone, themselves included.
        if eff("assigned_to") != acting["id"]:
            raise HTTPException(422, "Only the person an item is for can make it private — they can do it themselves (or an admin can while acting as them).")
    return updates


def _sync_sensor(background: BackgroundTasks, before: dict | None, after: dict) -> None:
    """Push the item, or — when its switch was just turned off — remove its
    entity. Both run after the response."""
    if after["expose_sensor"]:
        background.add_task(ha_sensors.push_item_blocking, after["id"])
    elif before is not None and before["expose_sensor"]:
        background.add_task(ha_sensors.delete_entity_blocking, after["entity_slug"])


def _queue_ping(background: BackgroundTasks, acting: dict, old_assignee, item: dict) -> None:
    """Assignment ping, same rule as tasks: only when the assignee changed to
    someone other than the person acting."""
    new = item.get("assigned_to")
    if new and new != old_assignee and new != acting["id"]:
        detail = recurrence.describe_rule(item["rule"])
        if schedule_logic.is_timed(item):
            detail += ", " + schedule_logic.time_range(item)
        background.add_task(reminders.send_assignment_ping_blocking, new, acting["name"], item["name"], None,
                            detail=detail, url=item.get("url"), place_id=item.get("place_id"))


# ---------------------------------------------------------------------------
# items
# ---------------------------------------------------------------------------

@router.get("/schedule")
def list_schedule(assignee: str | None = Query(default=None), acting: dict = Depends(get_acting_user)):
    """Visible items only; `assignee` = me | unassigned | anyone (default) | <user id>."""
    now = config.now()
    with db.get_conn() as conn:
        items = [dict(r) for r in conn.execute(
            f"SELECT * FROM schedule_items WHERE {schedule_logic.VISIBLE_SQL}", (acting["id"],))]
        excs = schedule_logic.load_exceptions(conn)
        users, places = schedule_logic.lookups(conn)

    def keep(i: dict) -> bool:
        if assignee in (None, "", "anyone"):
            return True
        if assignee == "me":
            return i["assigned_to"] == acting["id"]
        if assignee == "unassigned":
            return i["assigned_to"] is None
        return i["assigned_to"] == assignee

    out = [schedule_logic.serialize_item(i, excs.get(i["id"], []), now.date(), users, places, now) for i in items if keep(i)]
    out.sort(key=lambda i: (i["nextDate"] or "9999", i["startTime"] is not None, i["startTime"] or "", i["name"].lower()))
    return out


@router.post("/schedule", status_code=201)
def create_item(background: BackgroundTasks, body: dict = Body(...), acting: dict = Depends(get_acting_user)):
    name = _name(body.get("name"))
    rule, anchor = body.get("rule"), body.get("anchor_date")
    error = recurrence.validate_rule(rule, anchor)
    if error:
        raise HTTPException(422, error)
    lead = _lead_days(body["lead_days"]) if "lead_days" in body and body["lead_days"] is not None else 1
    notes, icon = _notes(body.get("notes")), _icon(body.get("icon"))
    with db.get_conn() as conn:
        extra = _person_fields(conn, body, None, acting)
        visibility = extra.get("visibility", "household")
        # any HA user can read sensors, so a private item isn't published unless asked
        expose = extra.get("expose_sensor", 0 if visibility == "private" else 1)
        if conn.execute("SELECT COUNT(*) FROM schedule_items").fetchone()[0] >= schedule_logic.MAX_ITEMS:
            raise HTTPException(422, f"There can be at most {schedule_logic.MAX_ITEMS} schedule items.")
        item_id = db.new_id()
        conn.execute(
            "INSERT INTO schedule_items (id, name, notes, rule, anchor_date, lead_days, icon, entity_slug, created_by, created_at, "
            "assigned_to, start_time, end_time, place_id, visibility, expose_sensor, url) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (item_id, name, notes, rule, anchor, lead, icon, schedule_logic.make_slug(conn, name), acting["id"], config.now_iso(),
             extra.get("assigned_to"), extra.get("start_time"), extra.get("end_time"), extra.get("place_id"), visibility, expose,
             extra.get("url")),
        )
        # read back directly: the creator may have made it someone else's private item
        item = dict(conn.execute("SELECT * FROM schedule_items WHERE id = ?", (item_id,)).fetchone())
        result = _item_json(conn, item)
    _sync_sensor(background, None, item)
    _queue_ping(background, acting, None, item)
    return result


@router.patch("/schedule/{item_id}")
def update_item(item_id: str, background: BackgroundTasks, body: dict = Body(...), acting: dict = Depends(get_acting_user)):
    with db.get_conn() as conn:
        item = _load_for_edit(conn, item_id, acting)
        updates: dict = {}
        if "name" in body:
            updates["name"] = _name(body["name"])
        if "notes" in body:
            updates["notes"] = _notes(body["notes"])
        if "lead_days" in body:
            updates["lead_days"] = _lead_days(body["lead_days"])
        if "icon" in body:
            updates["icon"] = _icon(body["icon"])
        updates.update(_person_fields(conn, body, item, acting))
        rule = body.get("rule", item["rule"])
        anchor = body.get("anchor_date", item["anchor_date"])
        if "rule" in body or "anchor_date" in body:
            error = recurrence.validate_rule(rule, anchor)
            if error:
                raise HTTPException(422, error)
            if rule != item["rule"] or anchor != item["anchor_date"]:
                updates["rule"], updates["anchor_date"] = rule, anchor
                # skips and moves were tied to specific rule occurrences; `add` dates survive (§8k)
                conn.execute("DELETE FROM schedule_exceptions WHERE item_id = ? AND kind IN ('skip', 'move')", (item_id,))
        if updates:
            conn.execute(f"UPDATE schedule_items SET {', '.join(k + ' = ?' for k in updates)} WHERE id = ?", [*updates.values(), item_id])
        # read back directly: the caller may just have made it someone else's private item
        after = dict(conn.execute("SELECT * FROM schedule_items WHERE id = ?", (item_id,)).fetchone())
        result = _item_json(conn, after)
    _sync_sensor(background, item, after)
    if "assigned_to" in updates:
        _queue_ping(background, acting, item["assigned_to"], after)
    return result


@router.delete("/schedule/{item_id}", status_code=204)
def delete_item(item_id: str, background: BackgroundTasks, acting: dict = Depends(get_acting_user)):
    with db.get_conn() as conn:
        item = _load_for_edit(conn, item_id, acting)
        conn.execute("DELETE FROM schedule_items WHERE id = ?", (item_id,))   # cascades its exceptions and reminder log
    if item["expose_sensor"]:
        background.add_task(ha_sensors.delete_entity_blocking, item["entity_slug"])
    return Response(status_code=204)


@router.post("/schedule/sync")
def force_sync(admin: dict = Depends(require_admin)):
    """Troubleshooting aid: push every published sensor now."""
    return ha_sensors.full_sync_blocking()


# ---------------------------------------------------------------------------
# exceptions
# ---------------------------------------------------------------------------

@router.post("/schedule/{item_id}/exceptions", status_code=201)
def add_exception(item_id: str, background: BackgroundTasks, body: dict = Body(...), acting: dict = Depends(get_acting_user)):
    kind, date_s, to_date_s = body.get("kind"), body.get("date"), body.get("to_date")
    reason = body.get("reason")
    with db.get_conn() as conn:
        item = _load_for_edit(conn, item_id, acting)
        excs = schedule_logic.load_exceptions(conn, item_id).get(item_id, [])

        # repeating an identical exception is idempotent
        same = next((e for e in excs if e["kind"] == kind and e["date"] == date_s and e["to_date"] == (to_date_s or None)), None)
        if same is None:
            error = schedule_logic.validate_exception(item, excs, kind, date_s, to_date_s, reason, config.today())
            if error:
                raise HTTPException(422, error)
            if kind in ("skip", "move"):
                # one exception per original occurrence: this replaces an existing skip/move
                conn.execute(
                    "DELETE FROM schedule_exceptions WHERE item_id = ? AND date = ? AND kind IN ('skip', 'move')",
                    (item_id, date_s),
                )
            conn.execute(
                "INSERT INTO schedule_exceptions (id, item_id, kind, date, to_date, reason, created_by, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (db.new_id(), item_id, kind, date_s, to_date_s if kind == "move" else None,
                 (reason.strip() or None) if isinstance(reason, str) else None, acting["id"], config.now_iso()),
            )
        result = _item_json(conn, item)
    _sync_sensor(background, item, item)
    return result


@router.delete("/schedule/{item_id}/exceptions/{exception_id}")
def undo_exception(item_id: str, exception_id: str, background: BackgroundTasks, acting: dict = Depends(get_acting_user)):
    with db.get_conn() as conn:
        item = _load_for_edit(conn, item_id, acting)
        cur = conn.execute("DELETE FROM schedule_exceptions WHERE id = ? AND item_id = ?", (exception_id, item_id))
        if cur.rowcount == 0:
            raise HTTPException(404, "Exception not found.")
        result = _item_json(conn, item)
    _sync_sensor(background, item, item)
    return result
