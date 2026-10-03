"""Reminders through Home Assistant `notify.<service>` (SPEC §8b): a daily
digest at each person's own configured time (`user_prefs.digest_time`,
08:00 until they pick one), a
per-user weekly summary, per-user/per-task "N before due" pings, and immediate assignment
pings. Strictly opt-in per user, each kind by its own switch: the digest
by `notifications_enabled`, the weekly summary by its
own toggle, "N before" reminders by having offsets, assignment pings by
`notify_on_assign` (for people who have saved their reminder settings).
Only for people with a phone in Home Assistant or a notify service
(ha_notify.services_for). A person with
several services gets each notification on every one of them; it counts as
sent (and is logged, so not repeated) when at least one succeeded.

The same reminders cover schedule items assigned to someone
(household or private): their occurrences are mixed into that person's
digest and weekly summary, timed ones get the person's "N before" offsets
relative to the start time, and a new assignee gets a ping. Only the
assignee is ever told about an item, and only its name, time range,
place and link are sent (never notes).

A task or item's optional link (URL) is included too: on its
own last line of a single-item notification (which also carries it as
notify `data`, so tapping the notification opens it), and on an indented
"🔗" line under the item in the digest and weekly summary.

Place details (the "Place details in reminders" App setting, on by default):
a task or item with a place gets its address and phone — "📍 <address>" and
"📞 <phone>" lines in a single-item notification, with **Directions** (a maps
search for the address) and **Call** (tel:) buttons in the notify `data`,
and an indented "📍 <address> · 📞 <phone>" line in the digest and weekly
summary. With the setting off only the place's name is sent, as before.

One asyncio loop ticks every 60 seconds and runs all four scheduled passes
(digest, weekly summary, task reminders, schedule reminders) each tick:
- The digest and weekly summary each only act inside the 60 minutes after
  their own configured time — a per-user time for the digest
  (`user_prefs.digest_time`, 08:00 when a person never set one), a per-user day/time for the
  weekly summary; their log tables' UNIQUE constraints guarantee at most
  one send per person per day, even across restarts.
- Task reminders have no fixed time-of-day or window: each is due once
  `now` reaches `due_datetime - minutes_before`, and stays eligible right up
  until `due_datetime` itself (so a restart just catches up), after which
  it is skipped as pointless. `task_reminder_log` is keyed on the due
  date/time too, so editing a task's due date/time makes it eligible again.
"""
import asyncio
import logging
import re
import urllib.parse
from datetime import date, datetime, timedelta

from starlette.concurrency import run_in_threadpool

from . import config, db, ha_client, ha_notify, links, schedule_logic, settings, taskview

logger = logging.getLogger("reminders")

TICK_SECONDS = 60
WINDOW_MINUTES = 60
MAX_DIGEST_LINES = 5
MAX_WEEKLY_LINES = 10
WEEKLY_LOOKAHEAD_DAYS = 7
TITLE = "Household Todo"
LINK_PREFIX = "   🔗 "
PLACE_PREFIX = "   📍 "
MAPS_SEARCH = "https://www.google.com/maps/search/?api=1&query="


def maps_url(address: str) -> str:
    """A maps search for the address — the same link the app's place chips open."""
    return MAPS_SEARCH + urllib.parse.quote(address, safe="")


def tel_url(phone: str) -> str | None:
    """tel: link from a typed phone number (digits and a leading +), or None."""
    digits = re.sub(r"[^\d+]", "", phone or "")
    digits = digits[:1] + digits[1:].replace("+", "")
    return f"tel:{digits}" if re.search(r"\d{3}", digits) else None


def place_details(place: dict | None) -> dict | None:
    """{address, phone} of a place when the App setting allows them and the
    place has any; None otherwise. `place` is any dict with `address` and
    optionally `phone`."""
    if not place or not settings.notify_place_details():
        return None
    address = (place.get("address") or "").strip()
    phone = (place.get("phone") or "").strip()
    if not address and not phone:
        return None
    return {"address": address, "phone": phone}


def _detail_lines(details: dict | None) -> list[str]:
    """Single-item notification lines: "📍 <address>", "📞 <phone>"."""
    if not details:
        return []
    out = []
    if details["address"]:
        out.append(f"📍 {details['address']}")
    if details["phone"]:
        out.append(f"📞 {details['phone']}")
    return out


def _actions(details: dict | None) -> list[dict]:
    """Companion-app buttons: Directions (maps) and Call (tel:)."""
    if not details:
        return []
    out = []
    if details["address"]:
        out.append({"action": "URI", "title": "Directions", "uri": maps_url(details["address"])})
    tel = tel_url(details["phone"])
    if tel:
        out.append({"action": "URI", "title": "Call", "uri": tel})
    return out


def _send_one(sender, services: list[str], message: str, url: str | None, details: dict | None = None) -> bool:
    """Send a single-item notification to each of the person's services;
    True if at least one accepted it. Place details (address, phone) go on
    their own lines, with Directions / Call buttons as notify `data`
    `actions`. With a link, the URL goes on its own last line and also as
    notify `data` (`url` for the iOS Companion app, `clickAction` for
    Android), so tapping the notification opens it. With neither, the sender
    is called exactly as before (no `data`)."""
    lines = [message] + _detail_lines(details)
    data = {}
    if links.is_web_url(url):
        lines.append(url)
        data.update(url=url, clickAction=url)
    actions = _actions(details)
    if actions:
        data["actions"] = actions
    text = "\n".join(lines)
    ok = False
    for service in services:
        if data:
            ok = bool(sender(service, TITLE, text, data=data)) or ok
        else:
            ok = bool(sender(service, TITLE, text)) or ok
    return ok


def _send_text(sender, services: list[str], message: str) -> bool:
    """Digest / weekly summary: the same text to every service."""
    ok = False
    for service in services:
        ok = bool(sender(service, TITLE, message)) or ok
    return ok


def _with_links(entries: list[dict], today: date) -> list[str]:
    """Digest / weekly lines: each entry's line, then an indented link line
    when it has one."""
    lines = []
    for t in entries:
        lines.append(_line(t, today))
        details = place_details(t.get("place"))
        if details:
            parts = ([f"{details['address']}"] if details["address"] else []) + \
                    ([f"📞 {details['phone']}"] if details["phone"] else [])
            lines.append(PLACE_PREFIX + " · ".join(parts))
        if links.is_web_url(t.get("url")):
            lines.append(f"{LINK_PREFIX}{t['url']}")
    return lines


# ---------------------------------------------------------------------------
# Building the digest
# ---------------------------------------------------------------------------

# Open, dated tasks that are "theirs" (§8e) on lists they may see; binds user_id three times.
_OPEN_AND_THEIRS = """t.completed = 0 AND t.due_date IS NOT NULL
          AND (t.assigned_to = ? OR (l.kind = 'personal' AND l.owner_user_id = ?))
          AND (l.kind = 'shared' OR l.owner_user_id = ?)"""


def collect_digest_tasks(conn, user_id: str, lead_days: int, today: date) -> list[dict]:
    """The user's open tasks that are due today or within `lead_days`, plus
    overdue ones — but only completion-required tasks can be overdue (§8l).
    "Theirs" is §8e's definition, limited to lists they may see."""
    sql = (
        taskview.TASK_SELECT
        + """
        WHERE """ + _OPEN_AND_THEIRS + """
          AND ((t.due_date >= ? AND t.due_date <= ?) OR (t.due_date < ? AND t.completion_required = 1))
        """
    )
    rows = conn.execute(
        sql,
        (user_id, user_id, user_id, today.isoformat(), (today + timedelta(days=lead_days)).isoformat(),
         today.isoformat()),
    ).fetchall()
    tasks = taskview.serialize_tasks(conn, rows, today)
    tasks.sort(key=lambda t: (t["dueDate"] >= today.isoformat(), t["dueDate"], t["dueTime"] or "", t["position"]))
    return tasks


def _when(t: dict, today: date) -> str:
    due = date.fromisoformat(t["dueDate"])
    delta = (due - today).days
    if delta < 0:
        n = -delta
        return f"{n} day{'s' if n != 1 else ''} overdue"
    if delta == 0:
        label = "today"
    elif delta == 1:
        label = "tomorrow"
    else:
        label = f"in {delta} days"
    if t["dueTime"]:
        label += f" {t['dueTime']}"
    return label


def _drive_note(due_date: str | None, due_time: str | None, drive_minutes: int | None) -> str:
    """" · ~18 min drive, leave by 13:42" when a task has both a due time and
    a place with a cached estimated drive time from home (spec §7.3); "" whenever
    any of those is missing or the "Drive times" App setting is off, so this is
    always safe to append unconditionally. 24-hour clock, matching this file's
    existing due-time formatting."""
    if not due_date or not due_time or drive_minutes is None or not settings.drive_times_enabled():
        return ""
    try:
        due_dt = datetime.combine(date.fromisoformat(due_date), datetime.strptime(due_time, "%H:%M").time())
    except (ValueError, TypeError):
        return ""
    leave_by = (due_dt - timedelta(minutes=drive_minutes)).strftime("%H:%M")
    return f" · ~{drive_minutes} min drive, leave by {leave_by}"


def collect_schedule_occurrences(conn, user_id: str, start: date, end: date) -> list[dict]:
    """Effective dates in [start, end] of the schedule items assigned to the
    user — household or private; they're the only one who gets them. Shaped
    like a task for the line formatter: {title, dueDate, dueTime, endTime,
    place, url, schedule: True}."""
    items = [dict(r) for r in conn.execute("SELECT * FROM schedule_items WHERE assigned_to = ?", (user_id,))]
    if not items:
        return []
    excs = schedule_logic.load_exceptions(conn)
    _, places = schedule_logic.lookups(conn)
    out = []
    for item in items:
        raw = places.get(item["place_id"])
        place = schedule_logic.place_json(raw)
        if place:
            place = dict(place, phone=raw.get("phone"))
        timed = schedule_logic.is_timed(item)
        for d in schedule_logic.effective_dates_between(item, excs.get(item["id"], []), start, end):
            out.append({"schedule": True, "title": item["name"], "dueDate": d.isoformat(),
                        "dueTime": item["start_time"] if timed else None,
                        "endTime": item["end_time"] if timed else None, "place": place,
                        "url": item["url"], "position": 0})
    return out


def _line(t: dict, today: date) -> str:
    """"• Title — when[ @ Place][ drive note]". A schedule occurrence shows
    its time range ("today 18:00–19:00"); an all-day one no time."""
    when = _when(t, today)
    if t.get("endTime"):
        when += f"–{t['endTime']}"
    line = f"• {t['title']} — {when}"
    if t["place"]:
        line += f" @ {t['place']['name']}"
        line += _drive_note(t["dueDate"], t["dueTime"], t["place"]["driveMinutes"])
    return line


def _entry_key(t: dict, today: date):
    """Overdue tasks first, then by date and time (all-day first), tasks
    before schedule items at the same time, then manual position."""
    return (t["dueDate"] >= today.isoformat(), t["dueDate"], t["dueTime"] or "", bool(t.get("schedule")), t["position"])


def format_digest(tasks: list[dict], today: date) -> str | None:
    """Only titles (and schedule item names), times, places (name, and with
    the place-details setting on, address and phone) and links are ever sent
    — never notes. `tasks` may include schedule occurrences from
    collect_schedule_occurrences."""
    if not tasks:
        return None
    lines = _with_links(tasks[:MAX_DIGEST_LINES], today)
    if len(tasks) > MAX_DIGEST_LINES:
        lines.append(f"+{len(tasks) - MAX_DIGEST_LINES} more")
    return "\n".join(lines)


def collect_weekly_tasks(conn, user_id: str, today: date) -> list[dict]:
    """The user's open tasks due from tomorrow through
    WEEKLY_LOOKAHEAD_DAYS days out — a preview of the week ahead. Overdue
    and due-today tasks are what the daily digest is for, so they aren't
    repeated here."""
    start = today + timedelta(days=1)
    end = today + timedelta(days=WEEKLY_LOOKAHEAD_DAYS)
    sql = (
        taskview.TASK_SELECT
        + """
        WHERE """ + _OPEN_AND_THEIRS + """
          AND t.due_date >= ? AND t.due_date <= ?
        """
    )
    rows = conn.execute(
        sql, (user_id, user_id, user_id, start.isoformat(), end.isoformat())
    ).fetchall()
    tasks = taskview.serialize_tasks(conn, rows, today)
    tasks.sort(key=lambda t: (t["dueDate"], t["dueTime"] or "", t["position"]))
    return tasks


def format_weekly_summary(tasks: list[dict], today: date) -> str | None:
    """Same one-title-per-line shape as the daily digest, just a longer
    look-ahead and a higher line cap."""
    if not tasks:
        return None
    lines = _with_links(tasks[:MAX_WEEKLY_LINES], today)
    if len(tasks) > MAX_WEEKLY_LINES:
        lines.append(f"+{len(tasks) - MAX_WEEKLY_LINES} more")
    return "\n".join(lines)


def _in_label(minutes: int) -> str:
    """"in 5 hours" / "in 1 day" / "in 30 minutes"."""
    if minutes % 1440 == 0:
        n = minutes // 1440
        return f"in {n} day{'s' if n != 1 else ''}"
    if minutes % 60 == 0:
        n = minutes // 60
        return f"in {n} hour{'s' if n != 1 else ''}"
    return f"in {minutes} minute{'s' if minutes != 1 else ''}"


def _offset_label(minutes: int) -> str:
    """"due in 5 hours" style label used in a task reminder's message."""
    return "due " + _in_label(minutes)


# ---------------------------------------------------------------------------
# The daily pass
# ---------------------------------------------------------------------------

def _in_time_window(now: datetime, hh: int, mm: int) -> bool:
    start = now.replace(hour=hh, minute=mm, second=0, microsecond=0)
    return start <= now < start + timedelta(minutes=WINDOW_MINUTES)


def _services_if_due(conn, u, now: datetime, hhmm: str | None) -> list[str]:
    """The person's notify services when `now` is inside the window after
    their "HH:MM" send time; [] otherwise (or if the time is unreadable)."""
    try:
        hh, mm = (int(x) for x in hhmm.split(":"))
    except (ValueError, AttributeError):
        return []
    if not _in_time_window(now, hh, mm):
        return []
    return ha_notify.services_for(dict(u), conn)


def run_digest_pass_blocking(now: datetime | None = None, sender=None) -> int:
    """One pass of the digest. Each person's digest fires in the 60 minutes
    after their own `digest_time` (08:00 if they never set one), mirroring
    how the weekly summary already works per person. Returns how many
    digests were sent. `sender` defaults to ha_notify.send_notify (tests
    inject a fake)."""
    now = now or config.now()
    if not ha_client.has_token():
        return 0
    sender = sender or ha_notify.send_notify
    today = now.date()
    sent = 0
    with db.get_conn() as conn:
        users = conn.execute(
            """
            SELECT u.id, u.name, u.username, p.lead_days, p.digest_time
            FROM users u JOIN user_prefs p ON p.user_id = u.id
            WHERE u.disabled = 0 AND p.notifications_enabled = 1
              AND NOT EXISTS (SELECT 1 FROM notification_log n
                              WHERE n.user_id = u.id AND n.kind = 'digest' AND n.sent_on = ?)
            """,
            (today.isoformat(),),
        ).fetchall()
        for u in users:
            services = _services_if_due(conn, u, now, u["digest_time"] or db.DEFAULT_DIGEST_TIME)
            if not services:
                continue
            tasks = collect_digest_tasks(conn, u["id"], u["lead_days"], today)
            tasks += collect_schedule_occurrences(conn, u["id"], today, today + timedelta(days=u["lead_days"]))
            tasks.sort(key=lambda t: _entry_key(t, today))
            message = format_digest(tasks, today)
            if not message:
                continue  # never send an empty digest
            if _send_text(sender, services, message):
                conn.execute(
                    "INSERT OR IGNORE INTO notification_log (id, user_id, kind, sent_on, created_at) "
                    "VALUES (?, ?, 'digest', ?, ?)",
                    (db.new_id(), u["id"], today.isoformat(), config.now_iso()),
                )
                conn.commit()
                sent += 1
            # a failed send is not recorded: it is retried next tick, but only
            # inside the 60-minute window (§8b)
    return sent


# ---------------------------------------------------------------------------
# The weekly-summary pass (per-user day/time)
# ---------------------------------------------------------------------------

def run_weekly_pass_blocking(now: datetime | None = None, sender=None) -> int:
    """Once per person, on their own configured day-of-week (ISO 1=Mon..
    7=Sun) and time, inside the same 60-minute catch-up window as the daily
    digest. Gated by the per-user weekly-summary toggle alone."""
    now = now or config.now()
    if not ha_client.has_token():
        return 0
    sender = sender or ha_notify.send_notify
    today = now.date()
    iso_dow = today.isoweekday()
    sent = 0
    with db.get_conn() as conn:
        users = conn.execute(
            """
            SELECT u.id, u.name, u.username, w.time
            FROM users u
            JOIN user_weekly_prefs w ON w.user_id = u.id
            WHERE u.disabled = 0 AND w.enabled = 1
              AND w.day_of_week = ?
              AND NOT EXISTS (SELECT 1 FROM weekly_summary_log l
                              WHERE l.user_id = u.id AND l.sent_on = ?)
            """,
            (iso_dow, today.isoformat()),
        ).fetchall()
        for u in users:
            services = _services_if_due(conn, u, now, u["time"])
            if not services:
                continue
            tasks = collect_weekly_tasks(conn, u["id"], today)
            tasks += collect_schedule_occurrences(conn, u["id"], today + timedelta(days=1),
                                                  today + timedelta(days=WEEKLY_LOOKAHEAD_DAYS))
            tasks.sort(key=lambda t: _entry_key(t, today))
            message = format_weekly_summary(tasks, today)
            from . import maint_notify   # their maintenance this week, in its own section
            maint = maint_notify.weekly_lines(conn, u["id"], today)
            if maint:
                message = ((message + "\n\n") if message else "") + "Maintenance:\n" + "\n".join(maint[:MAX_WEEKLY_LINES])
            if not message:
                continue  # never send an empty summary
            if _send_text(sender, services, message):
                conn.execute(
                    "INSERT OR IGNORE INTO weekly_summary_log (id, user_id, sent_on, created_at) "
                    "VALUES (?, ?, ?, ?)",
                    (db.new_id(), u["id"], today.isoformat(), config.now_iso()),
                )
                conn.commit()
                sent += 1
    return sent


# ---------------------------------------------------------------------------
# The task-reminder pass ("N before due", per user, per task)
# ---------------------------------------------------------------------------

def _reminder_users(conn) -> dict[str, list]:
    """user id -> their offset rows, for enabled users. Having offsets is the switch (the daily
    digest doesn't need to be on)."""
    rows = conn.execute(
        """
        SELECT o.user_id, o.minutes_before, u.name, u.username
        FROM user_reminder_offsets o
        JOIN users u ON u.id = o.user_id
        WHERE u.disabled = 0
        """
    ).fetchall()
    by_user: dict[str, list] = {}
    for r in rows:
        by_user.setdefault(r["user_id"], []).append(r)
    return by_user


def run_task_reminder_pass_blocking(now: datetime | None = None, sender=None) -> int:
    """Each user's own configured minute-offsets, applied to their own open
    tasks that have a due *time* — an offset needs a clock time, so a
    date-only task is only ever covered by the daily digest. A reminder
    fires once `now` reaches `due_datetime - minutes_before`, and stays
    eligible (for a restart to catch up on) until `due_datetime` itself."""
    now = now or config.now()
    if not ha_client.has_token():
        return 0
    sender = sender or ha_notify.send_notify
    sent = 0
    with db.get_conn() as conn:
        by_user = _reminder_users(conn)
        if not by_user:
            return 0
        # The widest legal offset is 7 days, so a task due further out than
        # that can't have a reminder due yet; bound the scan accordingly.
        window_start = (now.date() - timedelta(days=1)).isoformat()
        window_end = (now.date() + timedelta(days=10)).isoformat()
        for user_id, rows in by_user.items():
            services = ha_notify.services_for({"id": user_id, "username": rows[0]["username"]}, conn)
            if not services:
                continue
            tasks = conn.execute(
                taskview.TASK_SELECT
                + """
                WHERE t.completed = 0 AND t.due_date IS NOT NULL AND t.due_time IS NOT NULL
                  AND (t.assigned_to = ? OR (l.kind = 'personal' AND l.owner_user_id = ?))
                  AND (l.kind = 'shared' OR l.owner_user_id = ?)
                  AND t.due_date >= ? AND t.due_date <= ?
                """,
                (user_id, user_id, user_id, window_start, window_end),
            ).fetchall()
            for t in tasks:
                try:
                    due_dt = datetime.combine(
                        date.fromisoformat(t["due_date"]),
                        datetime.strptime(t["due_time"], "%H:%M").time(),
                        tzinfo=now.tzinfo,
                    )
                except (ValueError, TypeError):
                    continue
                if due_dt <= now:
                    continue  # already due — a "before it's due" ping is moot
                for r in rows:
                    minutes_before = r["minutes_before"]
                    reminder_at = due_dt - timedelta(minutes=minutes_before)
                    if reminder_at > now:
                        continue  # not time yet
                    already = conn.execute(
                        "SELECT 1 FROM task_reminder_log WHERE task_id = ? AND user_id = ? "
                        "AND minutes_before = ? AND due_date = ? AND due_time = ?",
                        (t["id"], user_id, minutes_before, t["due_date"], t["due_time"]),
                    ).fetchone()
                    if already:
                        continue
                    message = f"{t['title']} — {_offset_label(minutes_before)} ({t['due_time']})"
                    if t["place_name"]:
                        message += f" @ {t['place_name']}"
                        message += _drive_note(t["due_date"], t["due_time"], t["place_drive_minutes"])
                    details = place_details({"address": t["place_address"], "phone": t["place_phone"]}) \
                        if t["place_name"] else None
                    if _send_one(sender, services, message, t["url"], details):
                        conn.execute(
                            "INSERT OR IGNORE INTO task_reminder_log "
                            "(id, task_id, user_id, minutes_before, due_date, due_time, created_at) "
                            "VALUES (?, ?, ?, ?, ?, ?, ?)",
                            (db.new_id(), t["id"], user_id, minutes_before, t["due_date"], t["due_time"],
                             config.now_iso()),
                        )
                        conn.commit()
                        sent += 1
                    # a failed send is not recorded and is retried next tick,
                    # right up until due_dt makes it moot (mirrors the digest)
    return sent


# ---------------------------------------------------------------------------
# The schedule-reminder pass ("N before" a timed schedule item starts)
# ---------------------------------------------------------------------------

def run_schedule_reminder_pass_blocking(now: datetime | None = None, sender=None) -> int:
    """The assignee's own "N before" offsets, applied to the start time of
    each effective occurrence of their timed schedule items (household or
    private). Same timing rule as task reminders: due once
    `now >= start - offset`, pointless once `now >= start`. Deduplicated in
    schedule_reminder_log on (item, user, offset, date, start_time), so
    moving an occurrence or changing the item's times re-arms it."""
    now = now or config.now()
    if not ha_client.has_token():
        return 0
    sender = sender or ha_notify.send_notify
    sent = 0
    today = now.date()
    with db.get_conn() as conn:
        by_user = _reminder_users(conn)
        if not by_user:
            return 0
        excs = schedule_logic.load_exceptions(conn)
        _, places = schedule_logic.lookups(conn)
        for user_id, rows in by_user.items():
            services = ha_notify.services_for({"id": user_id, "username": rows[0]["username"]}, conn)
            if not services:
                continue
            items = [dict(r) for r in conn.execute(
                "SELECT * FROM schedule_items WHERE assigned_to = ? AND start_time IS NOT NULL AND end_time IS NOT NULL",
                (user_id,))]
            for item in items:
                place = places.get(item["place_id"])
                # the widest legal offset is 7 days, so nothing further out can be due yet
                for d in schedule_logic.effective_dates_between(item, excs.get(item["id"], []), today, today + timedelta(days=8)):
                    start_dt = datetime.combine(d, datetime.strptime(item["start_time"], "%H:%M").time(), tzinfo=now.tzinfo)
                    if start_dt <= now:
                        continue   # already started
                    for r in rows:
                        minutes_before = r["minutes_before"]
                        if start_dt - timedelta(minutes=minutes_before) > now:
                            continue   # not time yet
                        key = (item["id"], user_id, minutes_before, d.isoformat(), item["start_time"])
                        if conn.execute(
                            "SELECT 1 FROM schedule_reminder_log WHERE item_id = ? AND user_id = ? "
                            "AND minutes_before = ? AND date = ? AND start_time = ?", key,
                        ).fetchone():
                            continue
                        message = f"{item['name']} — starts {_in_label(minutes_before)} ({schedule_logic.time_range(item)})"
                        if place:
                            message += f" @ {place['name']}"
                            message += _drive_note(d.isoformat(), item["start_time"], place["drive_minutes"])
                        if _send_one(sender, services, message, item["url"], place_details(place)):
                            conn.execute(
                                "INSERT OR IGNORE INTO schedule_reminder_log "
                                "(id, item_id, user_id, minutes_before, date, start_time, created_at) "
                                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                                (db.new_id(), *key, config.now_iso()),
                            )
                            conn.commit()
                            sent += 1
                        # a failed send is retried next tick, until the start time
    return sent


# ---------------------------------------------------------------------------
# Assignment pings
# ---------------------------------------------------------------------------

def send_assignment_ping_blocking(assignee_id: str, actor_name: str, title: str, due_date: str | None, sender=None,
                                  detail: str | None = None, url: str | None = None,
                                  place_id: str | None = None) -> bool:
    """Best effort; the caller has already decided that a ping is warranted
    (assignee changed, and isn't the person who did it). Checks the
    assignee's own switches and mapping. Tasks pass `due_date`; schedule
    items pass `detail` ("Every Tue, Thu & Fri, 18:00–19:00"). Either may
    pass its `url` and `place_id` (the place's name, and its address and
    phone when the place-details setting is on)."""
    if not ha_client.has_token():
        return False
    sender = sender or ha_notify.send_notify
    with db.get_conn() as conn:
        u = conn.execute(
            """
            SELECT u.id, u.name, u.username, u.disabled, p.notifications_enabled, p.notify_on_assign
            FROM users u LEFT JOIN user_prefs p ON p.user_id = u.id WHERE u.id = ?
            """,
            (assignee_id,),
        ).fetchone()
        place = conn.execute("SELECT name, address, phone FROM places WHERE id = ?", (place_id,)).fetchone() \
            if place_id else None
    # its own switch; someone who never saved their reminder settings (no prefs row) isn't pinged
    if not u or u["disabled"] or u["notify_on_assign"] is None or not u["notify_on_assign"]:
        return False
    services = ha_notify.services_for(dict(u))
    if not services:
        return False
    message = f"{actor_name} assigned you: {title}"
    if due_date:
        message += f" (due {due_date})"
    elif detail:
        message += f" ({detail})"
    if place:
        message += f" @ {place['name']}"
    return _send_one(sender, services, message, url, place_details(dict(place)) if place else None)


# ---------------------------------------------------------------------------
# Loop
# ---------------------------------------------------------------------------

def _maintenance_pass() -> int:
    from . import maint_notify
    return maint_notify.run_pass_blocking()


async def loop() -> None:
    """Started from main.py's lifespan; cancelled on shutdown. Each pass is
    isolated so one kind failing doesn't block the others."""
    passes = (
        (run_digest_pass_blocking, "digest"),
        (run_weekly_pass_blocking, "weekly summary"),
        (run_task_reminder_pass_blocking, "task reminder"),
        (run_schedule_reminder_pass_blocking, "schedule reminder"),
        (_maintenance_pass, "maintenance"),
    )
    while True:
        for fn, name in passes:
            try:
                await run_in_threadpool(fn)
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("%s pass failed", name)
        await asyncio.sleep(TICK_SECONDS)
