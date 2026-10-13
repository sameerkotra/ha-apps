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
import threading
import time
import urllib.parse
from datetime import date, datetime, timedelta, timezone

from starlette.concurrency import run_in_threadpool

from . import config, db, ha_client, links, schedule_logic, settings, taskview
from .common import ha_notify

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


def _ticket_action(conn, task_id: str) -> list[dict]:
    """*Open ticket* for a task with exactly one PDF on it (SPEC §17): the app's page at #/ticket/<task>/<file>
    (deeplink.js), which offers Open and Download. Nothing without the app's sidebar page."""
    if not config.SIDEBAR_PAGE:
        return []
    from . import task_files
    f = task_files.the_ticket(conn, task_id)
    if not f:
        return []
    return [{"action": "URI", "title": "Open ticket", "uri": f"{config.SIDEBAR_PAGE}/ticket/{task_id}/{f['id']}"}]


def _send_one(sender, services: list[str], message: str, url: str | None, details: dict | None = None,
              extra_actions: list[dict] | None = None) -> bool:
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
    actions = _actions(details) + list(extra_actions or [])
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
    user — household or private; they're the only one who gets them — and of
    items taken in turns, on the user's turns (§5.4b). Shaped
    like a task for the line formatter: {title, dueDate, dueTime, endTime,
    place, url, schedule: True}."""
    items = [dict(r) for r in conn.execute(
        "SELECT * FROM schedule_items WHERE assigned_to = ? OR rotation IS NOT NULL", (user_id,))]
    if not items:
        return []
    schedule_logic.attach_turns(conn, items)
    excs = schedule_logic.load_exceptions(conn)
    _, places = schedule_logic.lookups(conn)
    out = []
    for item in items:
        raw = places.get(item["place_id"])
        place = schedule_logic.place_json(raw)
        if place:
            place = dict(place, phone=raw.get("phone"))
        timed = schedule_logic.is_timed(item)
        for d, turn in schedule_logic.effective_turns_between(item, excs.get(item["id"], []), start, end):
            if item["assigned_to"] != user_id and turn != user_id:
                continue                      # taken in turns (§5.4b): only the one whose turn it is
            out.append({"schedule": True, "title": item["name"] + (" (your turn)" if turn == user_id else ""),
                        "dueDate": d.isoformat(),
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

# Catching up: a digest (weekly summary, maintenance notification) whose time came while the app wasn't running
# is still sent up to CATCH_UP_MINUTES after that time, once the reminders loop is going again.
CATCH_UP_MINUTES = 180
_loop_since: datetime | None = None          # when the reminders loop started (None: not running, e.g. tests)


def _parse_hhmm(hhmm) -> tuple[int, int] | None:
    try:
        hh, mm = (int(x) for x in hhmm.split(":"))
        if 0 <= hh <= 23 and 0 <= mm <= 59:
            return hh, mm
    except (ValueError, AttributeError):
        pass
    return None


def window_day(now: datetime, hhmm) -> date | None:
    """The day whose "HH:MM" send time `now` is in the window after: today's, or — for a time late in the
    evening — yesterday's, whose window runs past midnight. The window is WINDOW_MINUTES long, or
    CATCH_UP_MINUTES when the app wasn't running yet at that time (it was restarted, or off). None: outside."""
    t = _parse_hhmm(hhmm)
    if t is None:
        return None
    for day in (now.date(), now.date() - timedelta(days=1)):
        start = now.replace(year=day.year, month=day.month, day=day.day, hour=t[0], minute=t[1], second=0,
                            microsecond=0)
        minutes = WINDOW_MINUTES
        if _loop_since is not None and _loop_since > start:
            minutes = CATCH_UP_MINUTES
        if start <= now < start + timedelta(minutes=minutes):
            return day
    return None


def _in_time_window(now: datetime, hh: int, mm: int) -> bool:
    return window_day(now, f"{hh:02d}:{mm:02d}") is not None


def _services_if_due(conn, u, now: datetime, hhmm: str | None) -> list[str]:
    """The person's notify services when `now` is inside the window after
    their "HH:MM" send time; [] otherwise (or if the time is unreadable)."""
    if window_day(now, hhmm) is None:
        return []
    return ha_notify.services_for(dict(u), conn)


# ---------------------------------------------------------------------------
# Home Assistant not answering
# ---------------------------------------------------------------------------

class HomeAssistantDown(Exception):
    """A send failed and Home Assistant doesn't answer at all: the rest of this tick waits for the next one."""


_down_at: float | None = None


def _after_failed_send() -> None:
    """A notification wasn't accepted. If Home Assistant answers (the phone's service is wrong, say), the pass
    carries on with everyone else; if it doesn't answer at all, stop here instead of waiting out a 10-second
    timeout for every person and every reminder in turn — they're all tried again on the next tick."""
    status, _ = ha_client.request("GET", "/", timeout=5)
    if status is None:
        raise HomeAssistantDown()


def _down(name: str) -> None:
    global _down_at
    _down_at = time.monotonic()
    logger.warning("Home Assistant isn't answering: the %s pass stopped and tries again in a minute.", name)


def run_digest_pass_blocking(now: datetime | None = None, sender=None) -> int:
    """One pass of the digest. Each person's digest fires in the 60 minutes
    after their own `digest_time` (08:00 if they never set one), mirroring
    how the weekly summary already works per person — for a late time, that
    hour runs past midnight; after a restart, a digest whose time came while
    the app was off is caught up for three hours. Returns how many digests
    were sent. `sender` defaults to ha_notify.send_notify (tests inject a
    fake)."""
    now = now or config.now()
    if not ha_client.has_token():
        return 0
    sender = sender or ha_notify.send_notify
    sent = 0
    try:
        with db.get_conn() as conn:
            users = conn.execute(
                """
                SELECT u.id, u.name, u.username, p.lead_days, p.digest_time
                FROM users u JOIN user_prefs p ON p.user_id = u.id
                WHERE u.disabled = 0 AND p.notifications_enabled = 1
                """
            ).fetchall()
            for u in users:
                day = window_day(now, u["digest_time"] or db.DEFAULT_DIGEST_TIME)
                if day is None or conn.execute(
                        "SELECT 1 FROM notification_log WHERE user_id = ? AND kind = 'digest' AND sent_on = ?",
                        (u["id"], day.isoformat())).fetchone():
                    continue
                services = ha_notify.services_for(dict(u), conn)
                if not services:
                    continue
                tasks = collect_digest_tasks(conn, u["id"], u["lead_days"], day)
                tasks += collect_schedule_occurrences(conn, u["id"], day, day + timedelta(days=u["lead_days"]))
                tasks.sort(key=lambda t: _entry_key(t, day))
                message = format_digest(tasks, day)
                if not message:
                    continue  # never send an empty digest
                if _send_text(sender, services, message):
                    conn.execute(
                        "INSERT OR IGNORE INTO notification_log (id, user_id, kind, sent_on, created_at) "
                        "VALUES (?, ?, 'digest', ?, ?)",
                        (db.new_id(), u["id"], day.isoformat(), config.now_iso()),
                    )
                    conn.commit()
                    sent += 1
                else:
                    # a failed send is not recorded: it is retried next tick, but only
                    # inside the 60-minute window (§8b)
                    _after_failed_send()
    except HomeAssistantDown:
        _down("digest")
    return sent


# ---------------------------------------------------------------------------
# The weekly-summary pass (per-user day/time)
# ---------------------------------------------------------------------------

def run_weekly_pass_blocking(now: datetime | None = None, sender=None) -> int:
    """Once per person, on their own configured day-of-week (ISO 1=Mon..
    7=Sun) and time, inside the same window as the daily digest (past
    midnight for a late time, caught up after a restart). Gated by the
    per-user weekly-summary toggle alone."""
    now = now or config.now()
    if not ha_client.has_token():
        return 0
    sender = sender or ha_notify.send_notify
    sent = 0
    try:
        with db.get_conn() as conn:
            users = conn.execute(
                """
                SELECT u.id, u.name, u.username, w.time, w.day_of_week
                FROM users u
                JOIN user_weekly_prefs w ON w.user_id = u.id
                WHERE u.disabled = 0 AND w.enabled = 1
                """
            ).fetchall()
            for u in users:
                today = window_day(now, u["time"])
                if today is None or today.isoweekday() != u["day_of_week"] or conn.execute(
                        "SELECT 1 FROM weekly_summary_log WHERE user_id = ? AND sent_on = ?",
                        (u["id"], today.isoformat())).fetchone():
                    continue
                services = ha_notify.services_for(dict(u), conn)
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
                else:
                    _after_failed_send()
    except HomeAssistantDown:
        _down("weekly summary")
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


ON_TIME = timedelta(minutes=5)       # a reminder sent this late still says its own "in 1 hour"


def _utc(dt: datetime) -> datetime:
    return dt.astimezone(timezone.utc)


def _remaining_label(minutes: float) -> str:
    """"in 40 minutes" / "in 3 hours" / "in 2 days", from the time really left (rounded)."""
    m = max(1, round(minutes))
    if m < 90:
        return f"in {m} minute{'s' if m != 1 else ''}"
    if m < 36 * 60:
        h = round(m / 60)
        return f"in {h} hour{'s' if h != 1 else ''}"
    d = round(m / 1440)
    return f"in {d} day{'s' if d != 1 else ''}"


def _due_offsets(at: datetime, now: datetime, offsets: list[int], logged) -> tuple[list[int], str] | None:
    """The person's "N before" offsets that are due for something happening at `at` and not sent yet, and the
    "in …" words for the one notification that covers them: the smallest due offset's own words when it is
    on time, else the time really left (the app was off, or the offset was just added). Times are compared
    in UTC, so an offset across a clock change is exact. None: nothing to send."""
    at_utc, now_utc = _utc(at), _utc(now)
    if at_utc <= now_utc:
        return None                                  # already started / due: a "before" ping is moot
    due = sorted(m for m in set(offsets) if at_utc - timedelta(minutes=m) <= now_utc and not logged(m))
    if not due:
        return None
    smallest = due[0]
    if now_utc - (at_utc - timedelta(minutes=smallest)) <= ON_TIME:
        words = _in_label(smallest)
    else:
        words = _remaining_label((at_utc - now_utc).total_seconds() / 60)
    return due, words


def run_task_reminder_pass_blocking(now: datetime | None = None, sender=None) -> int:
    """Each user's own configured minute-offsets, applied to their own open
    tasks that have a due *time* — an offset needs a clock time, so a
    date-only task is only ever covered by the daily digest. A reminder
    fires once `now` reaches `due_datetime - minutes_before`, and stays
    eligible (for a restart to catch up on) until `due_datetime` itself.
    Several offsets due at once (after a restart, or a just-added task) make
    ONE notification, worded from the time really left; the larger ones are
    logged as sent with it."""
    now = now or config.now()
    if not ha_client.has_token():
        return 0
    sender = sender or ha_notify.send_notify
    sent = 0
    try:
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
                offsets = [r["minutes_before"] for r in rows]
                for t in tasks:
                    try:
                        due_dt = datetime.combine(
                            date.fromisoformat(t["due_date"]),
                            datetime.strptime(t["due_time"], "%H:%M").time(),
                            tzinfo=now.tzinfo,
                        )
                    except (ValueError, TypeError):
                        continue
                    logged = {r[0] for r in conn.execute(
                        "SELECT minutes_before FROM task_reminder_log WHERE task_id = ? AND user_id = ? "
                        "AND due_date = ? AND due_time = ?", (t["id"], user_id, t["due_date"], t["due_time"]))}
                    todo = _due_offsets(due_dt, now, offsets, logged.__contains__)
                    if todo is None:
                        continue
                    due, words = todo
                    message = f"{t['title']} — due {words} ({t['due_time']})"
                    if t["place_name"]:
                        message += f" @ {t['place_name']}"
                        message += _drive_note(t["due_date"], t["due_time"], t["place_drive_minutes"])
                    details = place_details({"address": t["place_address"], "phone": t["place_phone"]}) \
                        if t["place_name"] else None
                    if _send_one(sender, services, message, t["url"], details, _ticket_action(conn, t["id"])):
                        for minutes_before in due:
                            conn.execute(
                                "INSERT OR IGNORE INTO task_reminder_log "
                                "(id, task_id, user_id, minutes_before, due_date, due_time, created_at) "
                                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                                (db.new_id(), t["id"], user_id, minutes_before, t["due_date"], t["due_time"],
                                 config.now_iso()),
                            )
                        conn.commit()
                        sent += 1
                    else:
                        # a failed send is not recorded and is retried next tick,
                        # right up until due_dt makes it moot (mirrors the digest)
                        _after_failed_send()
    except HomeAssistantDown:
        _down("task reminder")
    return sent


# ---------------------------------------------------------------------------
# The schedule-reminder pass ("N before" a timed schedule item starts)
# ---------------------------------------------------------------------------

def run_schedule_reminder_pass_blocking(now: datetime | None = None, sender=None) -> int:
    """The assignee's own "N before" offsets, applied to the start time of
    each effective occurrence of their timed schedule items (household or
    private). Same timing rule as task reminders: due once
    `now >= start - offset`, pointless once `now >= start`, and several due
    at once make one notification. Deduplicated in schedule_reminder_log on
    (item, user, offset, date, start_time), so moving an occurrence or
    changing the item's times re-arms it."""
    now = now or config.now()
    if not ha_client.has_token():
        return 0
    sender = sender or ha_notify.send_notify
    sent = 0
    today = now.date()
    try:
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
                offsets = [r["minutes_before"] for r in rows]
                items = [dict(r) for r in conn.execute(
                    "SELECT * FROM schedule_items WHERE (assigned_to = ? OR rotation IS NOT NULL) "
                    "AND start_time IS NOT NULL AND end_time IS NOT NULL", (user_id,))]
                schedule_logic.attach_turns(conn, items)
                for item in items:
                    place = places.get(item["place_id"])
                    # the widest legal offset is 7 days, so nothing further out can be due yet
                    for d, turn in schedule_logic.effective_turns_between(item, excs.get(item["id"], []), today, today + timedelta(days=8)):
                        if item["assigned_to"] != user_id and turn != user_id:
                            continue              # taken in turns: only the one whose turn it is
                        start_dt = datetime.combine(d, datetime.strptime(item["start_time"], "%H:%M").time(), tzinfo=now.tzinfo)
                        logged = {r[0] for r in conn.execute(
                            "SELECT minutes_before FROM schedule_reminder_log WHERE item_id = ? AND user_id = ? "
                            "AND date = ? AND start_time = ?", (item["id"], user_id, d.isoformat(), item["start_time"]))}
                        todo = _due_offsets(start_dt, now, offsets, logged.__contains__)
                        if todo is None:
                            continue
                        due, words = todo
                        message = f"{item['name']} — starts {words} ({schedule_logic.time_range(item)})"
                        if place:
                            message += f" @ {place['name']}"
                            message += _drive_note(d.isoformat(), item["start_time"], place["drive_minutes"])
                        if _send_one(sender, services, message, item["url"], place_details(place)):
                            for minutes_before in due:
                                conn.execute(
                                    "INSERT OR IGNORE INTO schedule_reminder_log "
                                    "(id, item_id, user_id, minutes_before, date, start_time, created_at) "
                                    "VALUES (?, ?, ?, ?, ?, ?, ?)",
                                    (db.new_id(), item["id"], user_id, minutes_before, d.isoformat(),
                                     item["start_time"], config.now_iso()),
                                )
                            conn.commit()
                            sent += 1
                        else:
                            _after_failed_send()      # retried next tick, until the start time
    except HomeAssistantDown:
        _down("schedule reminder")
    return sent


# ---------------------------------------------------------------------------
# Assignment pings
# ---------------------------------------------------------------------------

# "X assigned you …" pings: at most PING_BURST from one person to another in PING_WINDOW_S seconds (the
# assignment itself always goes through; only the extra pings are dropped), so nobody can flood a phone by
# re-assigning things.
PING_BURST = 5
PING_WINDOW_S = 600
_ping_lock = threading.Lock()
_pings: dict[tuple[str, str], list[float]] = {}


def ping_allowed(actor_id: str, assignee_id: str, now: float | None = None) -> bool:
    """Count one assignment ping from `actor_id` to `assignee_id`; False when they've had PING_BURST already."""
    now = time.monotonic() if now is None else now
    with _ping_lock:
        if len(_pings) > 1000:                     # forget pairs that have been quiet for a window
            for k in [k for k, v in _pings.items() if not v or now - v[-1] > PING_WINDOW_S]:
                del _pings[k]
        recent = [t for t in _pings.get((actor_id, assignee_id), []) if now - t < PING_WINDOW_S]
        if len(recent) >= PING_BURST:
            _pings[(actor_id, assignee_id)] = recent
            logger.info("Not sending another “assigned you” notification to %s for now (%d in %d minutes).",
                        assignee_id, PING_BURST, PING_WINDOW_S // 60)
            return False
        recent.append(now)
        _pings[(actor_id, assignee_id)] = recent
        return True


def reset_ping_limits() -> None:
    """Tests."""
    with _ping_lock:
        _pings.clear()


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

ALARM_LATE = timedelta(hours=1)          # an alarm missed by more than this (the app was off) is dropped


def run_alarm_pass_blocking(now: datetime | None = None, sender=None) -> int:
    """"Remind me at 5 pm" alarms (`task_alarms`): sent once `now` reaches their time, to the person's phones, while
    the task is still open; up to an hour late (a restart catches up), then dropped. Needs no reminder settings."""
    now = now or config.now()
    if not ha_client.has_token():
        return 0
    sender = sender or ha_notify.send_notify
    stamp = now.strftime("%Y-%m-%dT%H:%M")
    sent = 0
    with db.get_conn() as conn:
        rows = conn.execute(
            "SELECT a.id, a.user_id, a.at, a.task_id, t.title, t.completed, t.url, u.username, u.disabled FROM task_alarms a "
            "JOIN tasks t ON t.id = a.task_id JOIN users u ON u.id = a.user_id "
            "WHERE a.sent_at IS NULL AND a.at <= ? ORDER BY a.at", (stamp,)).fetchall()
        for r in rows:
            try:
                at = datetime.strptime(r["at"], "%Y-%m-%dT%H:%M").replace(tzinfo=now.tzinfo)
            except ValueError:
                at = None
            if at is None or r["completed"] or r["disabled"] or now - at > ALARM_LATE:
                conn.execute("UPDATE task_alarms SET sent_at = ? WHERE id = ?", (config.now_iso(), r["id"]))
                conn.commit()
                continue
            services = ha_notify.services_for({"id": r["user_id"], "username": r["username"]}, conn)
            if not services:
                continue                 # nothing to send to: tried again next tick, up to an hour late
            if _send_one(sender, services, f"⏰ {r['title']} ({r['at'][11:]})", r["url"],
                         extra_actions=_ticket_action(conn, r["task_id"])):
                conn.execute("UPDATE task_alarms SET sent_at = ? WHERE id = ?", (config.now_iso(), r["id"]))
                conn.commit()
                sent += 1
            else:
                try:                     # every phone refused: tried again next tick, up to an hour late
                    _after_failed_send()
                except HomeAssistantDown:
                    _down("alarm")
                    break
    return sent


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
        (run_alarm_pass_blocking, "alarm"),
        (run_schedule_reminder_pass_blocking, "schedule reminder"),
        (_maintenance_pass, "maintenance"),
    )
    global _loop_since
    _loop_since = config.now()            # digests whose time came before this are caught up (window_day)
    while True:
        tick = time.monotonic()
        for fn, name in passes:
            if _down_at is not None and _down_at >= tick:
                break                     # Home Assistant isn't answering: the rest waits for the next tick
            try:
                await run_in_threadpool(fn)
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("%s pass failed", name)
        await asyncio.sleep(TICK_SECONDS)
