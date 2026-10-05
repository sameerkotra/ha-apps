"""Per-user reminder preferences and the test notification (SPEC §8b).
Always the real caller — an admin acting as someone else gets a 409."""
import re
import time

from fastapi import APIRouter, Body, Depends, HTTPException

from .. import config, db, ha_client
from ..common import ha_notify
from ..auth import get_real_user_for_prefs

router = APIRouter(prefix="/api", tags=["prefs"])

_last_test: dict[str, float] = {}
TEST_INTERVAL_SECONDS = 10

MAX_OFFSETS = 5
MIN_OFFSET_MINUTES = 1
MAX_OFFSET_MINUTES = 10080  # 7 days
_TIME_RE = re.compile(r"^([01][0-9]|2[0-3]):[0-5][0-9]$")


def _can_notify(user: dict) -> tuple[bool, str | None]:
    if not ha_notify.services_for(user):
        return False, ("No phone is linked to you yet. In Home Assistant, go to Settings → People → you → "
                       "Track device and pick your phone (the Home Assistant Companion app); it's picked up here "
                       "within 5 minutes. An admin can also add another notify service under Admin → Users.")
    if not ha_client.has_token():
        return False, "This app can't reach Home Assistant right now (no Supervisor token)."
    return True, None


def _maint_recipient(conn, uid: str) -> bool:
    """Is this person told about any maintenance (household default, an item's own list, or assignee)?"""
    from .. import maintenance as mt, settings
    if not settings.get("maintenance_enabled"):
        return False
    if uid in mt.default_recipients():
        return True
    return bool(conn.execute("SELECT 1 FROM maint_recipients WHERE user_id = ? UNION SELECT 1 FROM maint_items "
                             "WHERE assigned_to = ? LIMIT 1", (uid, uid)).fetchone())


def _prefs_json(conn, user: dict) -> dict:
    row = conn.execute("SELECT * FROM user_prefs WHERE user_id = ?", (user["id"],)).fetchone()
    can, reason = _can_notify(user)
    offsets = [
        r["minutes_before"]
        for r in conn.execute(
            "SELECT minutes_before FROM user_reminder_offsets WHERE user_id = ? ORDER BY minutes_before DESC",
            (user["id"],),
        ).fetchall()
    ]
    w = conn.execute("SELECT * FROM user_weekly_prefs WHERE user_id = ?", (user["id"],)).fetchone()
    return {
        "notificationsEnabled": bool(row["notifications_enabled"]) if row else False,
        "leadDays": row["lead_days"] if row else 0,
        "notifyOnAssign": bool(row["notify_on_assign"]) if row else True,
        "digestTime": (row["digest_time"] if row else None) or db.DEFAULT_DIGEST_TIME,
        "reminderOffsets": offsets,
        "weeklySummary": {
            "enabled": bool(w["enabled"]) if w else False,
            "dayOfWeek": w["day_of_week"] if w else 7,
            "time": w["time"] if w else "18:00",
        },
        "maintenanceNotify": bool(row["maintenance_notify"]) if row else True,
        "maintenanceRecipient": _maint_recipient(conn, user["id"]),
        "canNotify": can,
        "canNotifyReason": reason,
    }


@router.get("/prefs")
def get_prefs(user: dict = Depends(get_real_user_for_prefs)):
    with db.get_conn() as conn:
        return _prefs_json(conn, user)


@router.put("/prefs")
def put_prefs(body: dict = Body(...), user: dict = Depends(get_real_user_for_prefs)):
    with db.get_conn() as conn:
        cur = conn.execute("SELECT * FROM user_prefs WHERE user_id = ?", (user["id"],)).fetchone()
        enabled = bool(cur["notifications_enabled"]) if cur else False
        lead = cur["lead_days"] if cur else 0
        on_assign = bool(cur["notify_on_assign"]) if cur else True
        digest_time = (cur["digest_time"] if cur else None) or db.DEFAULT_DIGEST_TIME

        if "notificationsEnabled" in body:
            if not isinstance(body["notificationsEnabled"], bool):
                raise HTTPException(422, "notificationsEnabled must be true or false.")
            enabled = body["notificationsEnabled"]
        if "leadDays" in body:
            v = body["leadDays"]
            if isinstance(v, bool) or not isinstance(v, int) or not (0 <= v <= 7):
                raise HTTPException(422, "leadDays must be a whole number from 0 to 7.")
            lead = v
        if "notifyOnAssign" in body:
            if not isinstance(body["notifyOnAssign"], bool):
                raise HTTPException(422, "notifyOnAssign must be true or false.")
            on_assign = body["notifyOnAssign"]
        if "digestTime" in body:
            t = body["digestTime"]
            if t is None:
                digest_time = db.DEFAULT_DIGEST_TIME   # null resets to 08:00 (there is no household default)
            elif not isinstance(t, str) or not _TIME_RE.match(t):
                raise HTTPException(422, "digestTime must be a time like 08:00, or null to reset it to 08:00.")
            else:
                digest_time = t

        maint_on = bool(cur["maintenance_notify"]) if cur else True
        if "maintenanceNotify" in body:
            if not isinstance(body["maintenanceNotify"], bool):
                raise HTTPException(422, "maintenanceNotify must be true or false.")
            maint_on = body["maintenanceNotify"]

        if enabled and not (cur and cur["notifications_enabled"]):
            can, reason = _can_notify(user)
            if not can:
                raise HTTPException(400, reason)

        conn.execute(
            "INSERT INTO user_prefs (user_id, notifications_enabled, lead_days, notify_on_assign, digest_time, maintenance_notify) "
            "VALUES (?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(user_id) DO UPDATE SET notifications_enabled = excluded.notifications_enabled, "
            "lead_days = excluded.lead_days, notify_on_assign = excluded.notify_on_assign, digest_time = excluded.digest_time, "
            "maintenance_notify = excluded.maintenance_notify",
            (user["id"], int(enabled), lead, int(on_assign), digest_time, int(maint_on)),
        )

        if "reminderOffsets" in body:
            v = body["reminderOffsets"]
            if not isinstance(v, list) or len(v) > MAX_OFFSETS:
                raise HTTPException(422, f"reminderOffsets must be a list of at most {MAX_OFFSETS} numbers.")
            minutes = []
            for x in v:
                if isinstance(x, bool) or not isinstance(x, int) or not (MIN_OFFSET_MINUTES <= x <= MAX_OFFSET_MINUTES):
                    raise HTTPException(
                        422,
                        f"Each reminder offset must be a whole number of minutes from "
                        f"{MIN_OFFSET_MINUTES} to {MAX_OFFSET_MINUTES}.",
                    )
                minutes.append(x)
            minutes = sorted(set(minutes), reverse=True)
            conn.execute("DELETE FROM user_reminder_offsets WHERE user_id = ?", (user["id"],))
            for m in minutes:
                conn.execute(
                    "INSERT INTO user_reminder_offsets (id, user_id, minutes_before, created_at) VALUES (?, ?, ?, ?)",
                    (db.new_id(), user["id"], m, config.now_iso()),
                )

        if "weeklySummary" in body:
            ws = body["weeklySummary"]
            if not isinstance(ws, dict):
                raise HTTPException(422, "weeklySummary must be an object.")
            wcur = conn.execute("SELECT * FROM user_weekly_prefs WHERE user_id = ?", (user["id"],)).fetchone()
            w_enabled = bool(wcur["enabled"]) if wcur else False
            w_dow = wcur["day_of_week"] if wcur else 7
            w_time = wcur["time"] if wcur else "18:00"
            if "enabled" in ws:
                if not isinstance(ws["enabled"], bool):
                    raise HTTPException(422, "weeklySummary.enabled must be true or false.")
                w_enabled = ws["enabled"]
            if "dayOfWeek" in ws:
                d = ws["dayOfWeek"]
                if isinstance(d, bool) or not isinstance(d, int) or not (1 <= d <= 7):
                    raise HTTPException(422, "weeklySummary.dayOfWeek must be a whole number from 1 (Monday) to 7 (Sunday).")
                w_dow = d
            if "time" in ws:
                t = ws["time"]
                if not isinstance(t, str) or not _TIME_RE.match(t):
                    raise HTTPException(422, "weeklySummary.time must be a time like 18:00.")
                w_time = t
            conn.execute(
                "INSERT INTO user_weekly_prefs (user_id, enabled, day_of_week, time) VALUES (?, ?, ?, ?) "
                "ON CONFLICT(user_id) DO UPDATE SET enabled = excluded.enabled, "
                "day_of_week = excluded.day_of_week, time = excluded.time",
                (user["id"], int(w_enabled), w_dow, w_time),
            )

        return _prefs_json(conn, user)


@router.post("/prefs/test-notification")
def test_notification(user: dict = Depends(get_real_user_for_prefs)):
    """A test push to the caller's own assigned services only; one per 10 s.
    Sent to every assigned service; 502 only if none accepted it."""
    can, reason = _can_notify(user)
    if not can:
        raise HTTPException(400, reason)
    now = time.monotonic()
    last = _last_test.get(user["id"])
    if last is not None and now - last < TEST_INTERVAL_SECONDS:
        raise HTTPException(429, "Please wait a few seconds before sending another test.")
    _last_test[user["id"]] = now
    services = ha_notify.services_for(user)
    results = ha_notify.send_to_services(services, "Household Todo", "This is a test notification from Household Todo.")
    failed = [s for s, ok in results.items() if not ok]
    if len(failed) == len(results):
        hint = ha_notify.explain_failure(failed[0])
        raise HTTPException(502, "Home Assistant didn't accept the notification — "
                                 + (hint or "ask an admin to check your notify service under Admin → Users."))
    return {"status": "sent", "failed": [f"notify.{s}" for s in failed]}
