"""Expected statement dates and overdue reminders (SPEC.md section 27).

Each account can have an expected statement date ("monthly, on the 12th"). Its expected dates E1, E2, … are worked
out here, never stored. Cycle Ek runs from the day after Ek-1 to Ek and counts as received when the account gets an
upload on or after Ek: a statement (PDF or QFX) in any status but `error`, or a CSV import that put at least one
row in it. Only the latest cycle (the newest Ek on or before today) is ever looked at: once Ek+1 arrives, Ek is
dropped, received or not. A cycle is overdue from the day after Ek, unless received or dismissed; cycles before the
day the setting was set or changed (or the account restored / unarchived: `expected_set_at`) never are.

Reminders go to the account's owner only, on the schedule they chose (reminder_prefs; no row = No notifications),
through Home Assistant (the shared app/common/ha_notify.py and ha_people.py). The background loop (`loop`, every
15 minutes) sends what `due_reminders` returns: per person and account, only the newest reminder whose day and time
have passed, once (statement_reminders), retried for six hours when sending failed. Several accounts due in one
check go as one notification. "Now" and "today" are the process's local time, which bootstrap.py sets to Home
Assistant's time zone; tests replace `now`.

    expected_dates(account, start, end)   the expected dates in [start, end]
    cycle_status(conn, account, today)    the latest cycle: received, dismissed, due (today), overdue, upcoming
    due_reminders(conn, now)              what the next check should send
    run_check()                           one check (blocking): plan, send, record
"""
from __future__ import annotations

import asyncio
import calendar
import json
import logging
import re
from datetime import date, datetime, time, timedelta, timezone

from . import settings
from .db import get_db

logger = logging.getLogger(__name__)

EVERY_CHOICES = ((1, "Monthly"), (2, "Every 2 months"), (3, "Every 3 months"), (6, "Every 6 months"), (12, "Yearly"))
OFFSET_CHOICES = ((0, "On the day"), (1, "1 day"), (2, "2 days"), (3, "3 days"), (5, "5 days"), (7, "1 week"),
                  (10, "10 days"), (14, "2 weeks"), (21, "3 weeks"), (28, "4 weeks"))
REPEAT_CHOICES = ((0, "Never"), (7, "Every week"), (14, "Every 2 weeks"))
MAX_OFFSETS = 6
DEFAULT_AT = "09:00"
CHECK_SECONDS = 15 * 60
RETRY_SECONDS = 6 * 60 * 60
LOOK_BACK_DAYS = 400            # more than a year: the previous expected date of a yearly series
_MONTH_RE = re.compile(r"^(\d{4})-(0[1-9]|1[0-2])$")
_TIME_RE = re.compile(r"^([01]\d|2[0-3]):([0-5]\d)$")


def now() -> datetime:
    """Local time (Home Assistant's zone, see bootstrap.py), naive. Tests replace it."""
    return datetime.now()


def today() -> date:
    return now().date()


def enabled() -> bool:
    """Admin → App settings → Statement reminders (on by default)."""
    return settings.get_bool("statement_reminders")


# ------------------------------------------------------------------ dates

def _month_index(y: int, m: int) -> int:
    return y * 12 + (m - 1)


def _on_day(y: int, m: int, day: int) -> date:
    """Day 29–31 in a shorter month is its last day; 0 is always the last day."""
    last = calendar.monthrange(y, m)[1]
    return date(y, m, last if day == 0 or day > last else day)


def parse_month(value) -> tuple[int, int] | None:
    m = _MONTH_RE.match(str(value or "").strip())
    return (int(m.group(1)), int(m.group(2))) if m else None


def expected_dates(account, start: date, end: date) -> list[date]:
    """Every expected date from `start` to `end` (both included), oldest first; [] when Off."""
    every, day = account["expected_every_months"], account["expected_day"]
    if not every or day is None or end < start:
        return []
    anchor = None
    if every != 1:
        ym = parse_month(account["expected_from"])
        if ym is None:
            return []
        anchor = _month_index(*ym)
    out = []
    for idx in range(_month_index(start.year, start.month), _month_index(end.year, end.month) + 1):
        if anchor is not None and (idx < anchor or (idx - anchor) % every):
            continue
        d = _on_day(idx // 12, idx % 12 + 1, day)
        if start <= d <= end:
            out.append(d)
    return out


def _utc_text(local: datetime) -> str:
    """A local (naive) time as the UTC text the app stores."""
    return local.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


def stamp() -> str:
    """Now, as stored (UTC text): expected_set_at, from the same clock as everything here."""
    return _utc_text(now())


def local_from_utc(text) -> datetime | None:
    """Stored UTC text ('YYYY-MM-DD HH:MM:SS') → local naive time."""
    if not text:
        return None
    try:
        dt = datetime.strptime(str(text)[:19], "%Y-%m-%d %H:%M:%S")
    except ValueError:
        try:
            dt = datetime.strptime(str(text)[:10], "%Y-%m-%d")
        except ValueError:
            return None
    return dt.replace(tzinfo=timezone.utc).astimezone().replace(tzinfo=None)


def label(d: date | None) -> str:
    """"Oct 12" (with the year when it isn't this year's)."""
    if d is None:
        return ""
    text = f"{calendar.month_abbr[d.month]} {d.day}"
    return text if d.year == today().year else f"{text}, {d.year}"


# ------------------------------------------------------------------ received, dismissed, overdue

def last_upload(conn, account_id: int) -> datetime | None:
    """The newest upload that counts as a statement arriving (local time), or None."""
    s = conn.execute(
        "SELECT MAX(uploaded_at) FROM statements WHERE account_id = ? AND deleted_at IS NULL AND status != 'error'",
        (account_id,)).fetchone()[0]
    c = conn.execute(
        "SELECT MAX(c.imported_at) FROM csv_imports c WHERE c.deleted_at IS NULL AND EXISTS "
        "(SELECT 1 FROM transactions t WHERE t.csv_import_id = c.id AND t.account_id = ? AND t.deleted_at IS NULL)",
        (account_id,)).fetchone()[0]
    times = [t for t in (local_from_utc(s), local_from_utc(c)) if t is not None]
    return max(times) if times else None


def is_set(account) -> bool:
    return bool(account["expected_every_months"]) and account["expected_day"] is not None


def cycle_status(conn, account, on: date | None = None) -> dict | None:
    """The account's latest cycle, or None when it has no expected date or is archived / deleted.

    status: "received" (an upload on or after the expected date), "dismissed", "due" (today is the expected date),
    "overdue" (from the day after), or "upcoming" (no cycle counts yet: none so far, or only ones before the setting
    was made). next_date is the first expected date after today."""
    if not is_set(account) or account["is_archived"] or account["deleted_at"] is not None:
        return None
    on = on or today()
    past = expected_dates(account, on - timedelta(days=LOOK_BACK_DAYS), on)
    upcoming = expected_dates(account, on + timedelta(days=1), on + timedelta(days=LOOK_BACK_DAYS))
    out = {"account_id": account["id"], "name": account["name"], "cycle_date": past[-1] if past else None,
           "next_date": upcoming[0] if upcoming else None, "status": "upcoming", "days_overdue": 0,
           "last_upload": last_upload(conn, account["id"])}
    e = out["cycle_date"]
    set_at = local_from_utc(account["expected_set_at"])
    if e is None or (set_at is not None and e < set_at.date()):
        return out
    if out["last_upload"] is not None and out["last_upload"].date() >= e:
        out["status"] = "received"
    elif conn.execute("SELECT 1 FROM statement_dismissals WHERE account_id = ? AND cycle_date = ?",
                      (account["id"], e.isoformat())).fetchone():
        out["status"] = "dismissed"
    elif on > e:
        out["status"], out["days_overdue"] = "overdue", (on - e).days
    else:
        out["status"] = "due"
    return out


def column_label(st: dict | None) -> tuple[str, str]:
    """The Next statement column: ("Oct 12" | "Overdue 3 days" | "Dismissed" | "", css class)."""
    if st is None:
        return "", ""
    if st["status"] == "overdue":
        n = st["days_overdue"]
        return f"Overdue {n} day{'s' if n != 1 else ''}", "due-overdue"
    if st["status"] == "dismissed":
        return "Dismissed", "due-dismissed"
    if st["status"] == "due":
        return label(st["cycle_date"]), "due-today"
    return label(st["next_date"]), ""


def _owner_accounts(conn, user_id: str) -> list:
    return conn.execute(
        "SELECT * FROM accounts WHERE user_id = ? AND deleted_at IS NULL AND is_archived = 0 "
        "AND expected_every_months IS NOT NULL ORDER BY name COLLATE NOCASE", (user_id,)).fetchall()


def overdue_for(conn, user_id: str, on: date | None = None) -> list[dict]:
    """The person's own overdue statements (not received, not dismissed), oldest expected date first."""
    on = on or today()
    out = [st for st in (cycle_status(conn, a, on) for a in _owner_accounts(conn, user_id))
           if st and st["status"] == "overdue"]
    for st in out:
        st["cycle_label"] = label(st["cycle_date"])
    return sorted(out, key=lambda s: (s["cycle_date"], s["name"].casefold()))


def can_see(current, acting) -> bool:
    """Owners only: the person themself, or an admin acting as them; never someone it's shared with."""
    return current.id == acting.id or bool(current.is_admin)


def visible_overdue(conn, current, acting) -> list[dict]:
    """What Home, Upload and the badge show for this request: [] when the App setting is off or the viewer isn't
    the owner (or an admin acting as them)."""
    if not enabled() or not can_see(current, acting):
        return []
    return overdue_for(conn, acting.id)


def badge_count(current, acting) -> int:
    """The Upload menu item's count. Never breaks a page."""
    try:
        if not enabled() or not can_see(current, acting):
            return 0
        with get_db() as conn:
            return len(overdue_for(conn, acting.id))
    except Exception:
        logger.debug("statement badge failed", exc_info=True)
        return 0


def statuses(conn, rows, current, acting) -> dict:
    """{account id: (column text, css class)} for the lists, or {} when the column isn't shown."""
    if not enabled() or not can_see(current, acting):
        return {}
    on = today()
    out = {}
    for r in rows:
        keys = r.keys() if hasattr(r, "keys") else r
        if "expected_every_months" not in keys or not is_set(r):
            continue
        out[r["id"]] = column_label(cycle_status(conn, r, on))
    return out


def dismiss(conn, account, cycle_date: str, by: str) -> bool:
    """Dismiss the account's latest cycle (only that one: an older date is ignored)."""
    st = cycle_status(conn, account)
    if st is None or st["cycle_date"] is None or st["cycle_date"].isoformat() != cycle_date:
        return False
    conn.execute("INSERT OR IGNORE INTO statement_dismissals (account_id, cycle_date, dismissed_by) VALUES (?, ?, ?)",
                 (account["id"], cycle_date, by))
    return True


# ------------------------------------------------------------------ the account form

def parse_account_form(every: str, day: str, start: str, reminders: str) -> tuple[dict | None, str | None]:
    """({expected_every_months, expected_day, expected_from, reminders_on}, error)."""
    reminders_on = 1 if str(reminders or "").strip() in ("1", "on", "true") else 0
    every = str(every or "").strip()
    if every in ("", "0", "off"):
        return {"expected_every_months": None, "expected_day": None, "expected_from": None,
                "reminders_on": reminders_on}, None
    if not every.isdigit() or int(every) not in dict(EVERY_CHOICES):
        return None, "Choose how often the statement comes."
    n = int(every)
    day = str(day or "").strip()
    if day == "last":
        d = 0
    elif day.isdigit() and 1 <= int(day) <= 31:
        d = int(day)
    else:
        return None, "Pick the day the statement is ready (1–31, or the last day of the month)."
    frm = None
    if n != 1:
        ym = parse_month(start)
        if ym is None:
            return None, "Pick the month the statements count from (Starting)."
        frm = f"{ym[0]:04d}-{ym[1]:02d}"
    return {"expected_every_months": n, "expected_day": d, "expected_from": frm, "reminders_on": reminders_on}, None


def changed(old, new: dict) -> bool:
    return any((old[k] if old is not None else None) != new[k]
               for k in ("expected_every_months", "expected_day", "expected_from"))


# ------------------------------------------------------------------ My reminders

def get_prefs(conn, user_id: str) -> dict | None:
    r = conn.execute("SELECT * FROM reminder_prefs WHERE user_id = ?", (user_id,)).fetchone()
    if r is None:
        return None
    try:
        offsets = [int(o) for o in json.loads(r["offsets"] or "[]")]
    except (ValueError, TypeError):
        offsets = []
    return {"mode": r["mode"], "offsets": sorted(set(offsets)), "repeat_days": r["repeat_days"],
            "at_time": r["at_time"] or DEFAULT_AT, "updated_at": r["updated_at"]}


def parse_prefs_form(mode: str, offsets: list[str], repeat: str, at: str) -> tuple[dict | None, str | None]:
    mode = str(mode or "").strip()
    if mode not in ("off", "remind"):
        return None, "Choose No notifications or Remind me."
    if mode == "off":
        return {"mode": "off", "offsets": [], "repeat_days": 0, "at_time": DEFAULT_AT}, None
    allowed = dict(OFFSET_CHOICES)
    picked = set()
    for o in offsets or []:
        o = str(o).strip()
        if not o.isdigit() or int(o) not in allowed:
            return None, "That reminder day isn't one of the choices."
        picked.add(int(o))
    if not picked:
        return None, "Pick at least one, or choose No notifications."
    if len(picked) > MAX_OFFSETS:
        return None, f"Pick at most {MAX_OFFSETS}."
    repeat = str(repeat or "0").strip()
    if not repeat.isdigit() or int(repeat) not in dict(REPEAT_CHOICES):
        return None, "Choose how often to keep reminding."
    at = str(at or DEFAULT_AT).strip()
    if not _TIME_RE.match(at):
        return None, "Give the time as HH:MM."
    return {"mode": "remind", "offsets": sorted(picked), "repeat_days": int(repeat), "at_time": at}, None


def save_prefs(conn, user_id: str, p: dict) -> None:
    conn.execute(
        "INSERT INTO reminder_prefs (user_id, mode, offsets, repeat_days, at_time, updated_at) "
        "VALUES (?, ?, ?, ?, ?, datetime('now')) ON CONFLICT(user_id) DO UPDATE SET mode = excluded.mode, "
        "offsets = excluded.offsets, repeat_days = excluded.repeat_days, at_time = excluded.at_time, "
        "updated_at = excluded.updated_at",
        (user_id, p["mode"], json.dumps(p["offsets"]), p["repeat_days"], p["at_time"]))


# ------------------------------------------------------------------ reminders

def _moments(st: dict, prefs: dict) -> list[tuple[int, datetime]]:
    """(offset days, when) of every reminder of this cycle, before the next expected date."""
    e, nxt = st["cycle_date"], st["next_date"]
    hh, mm = (int(x) for x in prefs["at_time"].split(":"))
    at = time(hh, mm)
    offsets = list(prefs["offsets"])
    if prefs["repeat_days"] and offsets:
        last, step = max(offsets), prefs["repeat_days"]
        limit = (nxt - e).days if nxt else LOOK_BACK_DAYS
        o = last + step
        while o < limit:
            offsets.append(o)
            o += step
    return [(o, datetime.combine(e + timedelta(days=o), at)) for o in offsets
            if nxt is None or e + timedelta(days=o) < nxt]


_given_up: set = set()


def due_reminders(conn, at: datetime | None = None) -> list[dict]:
    """[{"user_id", "items": [{account_id, name, cycle_date, offset_days, retry}]}]: what to send now.

    Per person (mode Remind me) and account (expected date set, its own Remind me on, not archived or deleted, the
    latest cycle due or overdue): the newest reminder whose day and time have passed. One already in
    statement_reminders isn't sent again, except a failed one, retried for six hours from its first try. An older
    missed one is never sent (after the app was off, only the newest)."""
    if not enabled():
        return []
    at = at or now()
    on = at.date()
    plan = []
    for p in conn.execute("SELECT user_id FROM reminder_prefs WHERE mode = 'remind' ORDER BY user_id").fetchall():
        prefs = get_prefs(conn, p["user_id"])
        if not prefs or not prefs["offsets"]:
            continue
        items = []
        for a in _owner_accounts(conn, p["user_id"]):
            if not a["reminders_on"]:
                continue
            st = cycle_status(conn, a, on)
            if st is None or st["status"] not in ("due", "overdue"):
                continue
            passed = [(o, m) for o, m in _moments(st, prefs) if m <= at]
            if not passed:
                continue
            offset, _ = max(passed, key=lambda x: x[1])
            key = (p["user_id"], a["id"], st["cycle_date"].isoformat(), offset)
            row = conn.execute(
                "SELECT result, sent_at FROM statement_reminders WHERE user_id = ? AND account_id = ? "
                "AND cycle_date = ? AND offset_days = ?", key).fetchone()
            retry = False
            if row is not None:
                if row["result"] != "failed":
                    continue
                first = local_from_utc(row["sent_at"])
                if first is None or (at - first).total_seconds() >= RETRY_SECONDS:
                    if key not in _given_up:
                        _given_up.add(key)
                        logger.warning("Gave up on a statement reminder for %s (account %s, %s) after six hours "
                                       "of failed sends", p["user_id"], a["id"], key[2])
                    continue
                retry = True
            items.append({"account_id": a["id"], "name": a["name"], "last4": a["account_number_last4"],
                          "cycle_date": st["cycle_date"], "offset_days": offset, "retry": retry})
        if items:
            plan.append({"user_id": p["user_id"], "items": items})
    return plan


def safe_name(name: str, last4: str | None = None) -> str:
    """The account's name for a notification: never a number (its last 4 or a long run of digits are masked)."""
    text = re.sub(r"\d{5,}", "····", str(name or "Account"))
    return text.replace(str(last4), "····") if last4 else text


def message(items: list[dict], on: date) -> tuple[str, str]:
    """(title, message)."""
    if len(items) == 1:
        it = items[0]
        days = (on - it["cycle_date"]).days
        when = "today" if days <= 0 else ("1 day ago" if days == 1 else f"{days} days ago")
        return ("Statement overdue",
                f"{safe_name(it['name'], it.get('last4'))} — expected {label(it['cycle_date'])}, {when}. "
                "Upload it in Finance.")
    parts = ", ".join(f"{safe_name(it['name'], it.get('last4'))} ({label(it['cycle_date'])})" for it in items)
    return f"{len(items)} statements overdue", f"{parts}. Upload them in Finance."


def tap_link(items: list[dict]) -> dict | None:
    """The notification's tap: Upload with the account picked (several: none picked), on the app's sidebar page."""
    from . import tools
    panel = tools._panel()
    if not panel:
        return None
    url = f"{panel}/uploads/{items[0]['account_id']}" if len(items) == 1 else f"{panel}/uploads"
    return {"url": url, "clickAction": url}


def phones_for(conn, user_id: str) -> list[str]:
    """Every notify service that reaches the person: their phones in Home Assistant, then Admin → Users' extras."""
    from .common import ha_notify
    try:
        return ha_notify.services_for({"id": user_id}, conn)
    except Exception:
        logger.exception("Reading the person's notify services failed")
        return []


def run_check(at: datetime | None = None) -> int:
    """One check (blocking; never sends while holding a database connection). Returns notifications sent."""
    from .common import ha_notify
    at = at or now()
    with get_db() as conn:
        plan = due_reminders(conn, at)
        for p in plan:
            p["services"] = phones_for(conn, p["user_id"])
    sent = 0
    for p in plan:
        if not p["services"]:
            p["result"] = "no_phone"
            continue
        title, text = message(p["items"], at.date())
        results = ha_notify.send_to_services(p["services"], title, text, tap_link(p["items"]))
        p["result"] = "sent" if any(results.values()) else "failed"
        sent += p["result"] == "sent"
    if plan:
        stamp = _utc_text(at)
        with get_db() as conn:
            for p in plan:
                for it in p["items"]:
                    conn.execute(
                        "INSERT INTO statement_reminders (user_id, account_id, cycle_date, offset_days, sent_at, result) "
                        "VALUES (?, ?, ?, ?, ?, ?) ON CONFLICT(user_id, account_id, cycle_date, offset_days) "
                        "DO UPDATE SET result = excluded.result",
                        (p["user_id"], it["account_id"], it["cycle_date"].isoformat(), it["offset_days"], stamp,
                         p["result"]))
    return sent


def run_all(at: datetime | None = None) -> int:
    """One check of everything the loop sends: overdue statements, then card payments and subscription changes
    (alerts.py, SPEC.md section 31). One failing never stops the other."""
    from . import alerts
    sent = 0
    for what, fn in (("overdue statements", run_check), ("card payments and subscription changes", alerts.run_check)):
        try:
            sent += fn(at)
        except Exception:
            logger.exception("Checking for %s failed — will try again at the next check", what)
    return sent


async def loop() -> None:
    """Every 15 minutes (started by main.py's lifespan inside Home Assistant; cancelled on shutdown): the one
    background loop for every reminder and notification (run_all)."""
    from starlette.concurrency import run_in_threadpool
    while True:
        try:
            await run_in_threadpool(run_all)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Checking for reminders failed — will try again at the next check")
        await asyncio.sleep(CHECK_SECONDS)
