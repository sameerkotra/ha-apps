"""Reminders (§9): one daily digest per user through Home Assistant
notify services — the person's phones from Home Assistant (Settings → People
→ Track device, via ha_people) plus any extras an admin added.

A reminder about someone goes out only when two switches are on: the
person's own 🔔 switch (`people.remind`, shared by the household) and the
user's own reminders (`reminder_prefs.enabled`). Both are off by default.
For a couple's anniversary, either partner's 🔔 is enough.

The digest is sent once a day, in the hour after the user's own time (in
Home Assistant's time zone), and only when there is something to say. It
covers today and the next `lead_days` days. `reminder_log` (user, day) makes
it once a day even across restarts; a failed send isn't logged, so it's
retried on the next tick while the hour lasts.

Content rule: names, relationship and age only — never biographies, notes or
places. Home Assistant is never called while a database connection is open:
the pass reads everything first, closes the connection, then sends.
"""
import asyncio
import logging
from datetime import date, datetime, timedelta

from starlette.concurrency import run_in_threadpool

from . import config, db, graph as graph_mod, ha_client, kin, milestones as milestones_mod, upcoming
from .common import ha_notify

logger = logging.getLogger("reminders")

TITLE = "Family Tree"
TICK_SECONDS = 60
WINDOW_MINUTES = 60
MAX_LINES = 8
MAX_LEAD_DAYS = 14
DEFAULT_TIME = "08:00"
CLOSE_STEPS = 3

DEFAULTS = {"enabled": False, "time": DEFAULT_TIME, "birthdays": True, "anniversaries": True,
            "remembrance": False, "leadDays": 0, "scope": "all", "tithi": True, "tithiLeadDays": 7,
            "milestones": True}
_COLS = {"enabled": "enabled", "time": "send_time", "birthdays": "birthdays", "anniversaries": "anniversaries",
         "remembrance": "remembrance", "leadDays": "lead_days", "scope": "scope", "tithi": "tithi",
         "tithiLeadDays": "tithi_lead_days", "milestones": "milestones"}
MAX_TITHI_LEAD_DAYS = 30


# ---------------------------------------------------------------------------
# Preferences
# ---------------------------------------------------------------------------

def get_prefs(conn, user_id: str) -> dict:
    row = conn.execute("SELECT * FROM reminder_prefs WHERE user_id = ?", (user_id,)).fetchone()
    if not row:
        return dict(DEFAULTS)
    return {"enabled": bool(row["enabled"]), "time": row["send_time"], "birthdays": bool(row["birthdays"]),
            "anniversaries": bool(row["anniversaries"]), "remembrance": bool(row["remembrance"]),
            "leadDays": row["lead_days"], "scope": row["scope"], "tithi": bool(row["tithi"]),
            "tithiLeadDays": row["tithi_lead_days"], "milestones": bool(row["milestones"])}


def save_prefs(conn, user_id: str, prefs: dict) -> None:
    vals = {col: prefs[key] for key, col in _COLS.items()}
    for k in ("enabled", "birthdays", "anniversaries", "remembrance", "tithi", "milestones"):
        vals[k] = 1 if vals[k] else 0
    cols = list(vals)
    conn.execute(
        f"INSERT INTO reminder_prefs (user_id, {', '.join(cols)}) VALUES (?, {', '.join('?' * len(cols))}) "
        f"ON CONFLICT(user_id) DO UPDATE SET {', '.join(f'{c} = excluded.{c}' for c in cols)}",
        [user_id] + [vals[c] for c in cols])


def valid_time(value) -> str:
    """"HH:MM", 24-hour. ValueError otherwise."""
    if not isinstance(value, str):
        raise ValueError("The time must look like 08:00.")
    parts = value.strip().split(":")
    if len(parts) != 2 or not all(p.isdigit() and len(p) == 2 for p in parts):
        raise ValueError("The time must look like 08:00 (24-hour clock).")
    hh, mm = int(parts[0]), int(parts[1])
    if hh > 23 or mm > 59:
        raise ValueError("The time must be between 00:00 and 23:59.")
    return f"{hh:02d}:{mm:02d}"


def close_family(g, me: str | None) -> set:
    """Everyone within 3 steps of "This is me" (living and deceased), me included."""
    if not me or me not in g.people:
        return set()
    return upcoming.close_family(g, me, CLOSE_STEPS)


# ---------------------------------------------------------------------------
# What goes in a digest
# ---------------------------------------------------------------------------

def collect(g, prefs: dict, me: str | None, today: date, lang: str = "en", conn=None) -> list:
    """The upcoming entries this user is reminded about: 🔔 on, the kinds they
    chose, within their scope, from today through today + lead days. With a
    connection also tithi days — on the day and `tithiLeadDays` before —
    and milestones on their notice days (90, 30, 7 days before and on the day)."""
    me = me if me in g.people else None
    if prefs["scope"] == "close" and not me:
        return []                    # "close family" needs "This is me"; never widen it silently
    entries = upcoming.entries(g, today, int(prefs["leadDays"]), me, prefs["scope"], bool(prefs["remembrance"]),
                               lang=lang)
    wanted = {"birthday": prefs["birthdays"], "anniversary": prefs["anniversaries"],
              "remembrance": prefs["remembrance"]}
    out = [e for e in entries if e["remind"] and wanted.get(e["kind"])]
    if conn is not None and (prefs.get("tithi") or prefs.get("milestones")):
        extra = upcoming.extras(conn, g, today, int(prefs["leadDays"]), me, prefs["scope"], lang=lang,
                                tithis=bool(prefs.get("tithi")), leads_only=True,
                                tithi_lead=int(prefs.get("tithiLeadDays") or 0),
                                milestones_on=bool(prefs.get("milestones")))
        extra = [e for e in extra if e["remind"]]
        if not prefs["birthdays"]:
            extra = [e for e in extra if not (e["kind"] == "milestone" and e["sub"] != "anniversary")]
        if not prefs["anniversaries"]:
            extra = [e for e in extra if not (e["kind"] == "milestone" and e["sub"] == "anniversary")]
        out = upcoming.merge(out, extra)
    return out


def _when(d: date, today: date) -> str:
    n = (d - today).days
    if n == 0:
        return "today"
    if n == 1:
        return "tomorrow"
    return f"on {d.strftime('%a')} {d.day} {d.strftime('%b')}"


def _ago(years: int) -> str:
    return f"{years} year{'s' if years != 1 else ''} ago"


def line(e: dict, today: date) -> str:
    """One line of the digest. Names, relationship and age only."""
    when = _when(date.fromisoformat(e["date"]), today)
    years = e.get("years")
    if e["kind"] == "tithi":
        p = e["people"][0]
        rel = f" (your {p['relationship']})" if p.get("relationship") else ""
        if e["sub"] == "death":
            return f"🪔 Tithi for {p['name']}{rel} — {e['tithiName']} {when}"
        return f"🪔 Janma tithi of {p['name']}{rel} — {e['tithiName']} {when}"
    if e["kind"] == "milestone":
        d = date.fromisoformat(e["date"])
        away = (d - today).days
        w = when if away <= 7 else f"{when} ({milestones_mod.in_words(away)})"
        if e["sub"] == "anniversary":
            names = " & ".join(p.get("given") or p["name"] for p in e["people"])
            return f"🎉 {names} — {e['label']} ({years} years) {w}"
        p = e["people"][0]
        rel = f" (your {p['relationship']})" if p.get("relationship") else ""
        if e["sub"] == "full_moons":
            return f"🎉 {p['name']}{rel} — {e['label']} {w}"
        return f"🎉 {p['name']}{rel} — {e['label']}, turns {years} {w}"
    star = " 🎉 " + e["milestone"] if e.get("milestone") else ""
    if e["kind"] == "anniversary":
        names = " & ".join(p.get("given") or p["name"] for p in e["people"])
        what = f"{upcoming.ordinal(years)} anniversary" if years else "anniversary"
        return f"💍 {names} — {what} {when}{star}"
    p = e["people"][0]
    rel = f" (your {p['relationship']})" if p.get("relationship") else ""
    name = p["name"]
    if e["kind"] == "birthday":
        if years is not None:
            return f"🎂 {name}{rel} turns {years} {when}{star}"
        return f"🎂 It's {name}'s birthday {when}{rel}"
    if e.get("sub") == "death":
        if years:
            return f"🕯 Remembering {name}{rel}, who died {_ago(years)} {when}"
        return f"🕯 Remembering {name}{rel} — the day they died, {when}"
    if years:
        return f"🕯 Remembering {name}{rel}, born {_ago(years)} {when}"
    return f"🕯 Remembering {name}{rel} — their birthday, {when}"


def format_digest(entries: list, today: date) -> str | None:
    if not entries:
        return None
    lines = [line(e, today) for e in entries[:MAX_LINES]]
    if len(entries) > MAX_LINES:
        lines.append(f"…and {len(entries) - MAX_LINES} more")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# The daily pass
# ---------------------------------------------------------------------------

def _in_window(now: datetime, send_time: str) -> bool:
    try:
        hh, mm = (int(x) for x in (send_time or DEFAULT_TIME).split(":"))
        start = now.replace(hour=hh, minute=mm, second=0, microsecond=0)
    except (ValueError, AttributeError):
        return False
    return start <= now < start + timedelta(minutes=WINDOW_MINUTES)


def due_digests(now: datetime) -> list:
    """[(user_id, services, message)] for everyone whose digest is due now and
    has something in it. Reads only; the connection is closed on return."""
    today = now.date()
    out = []
    with db.get_conn() as conn:
        rows = conn.execute(
            "SELECT u.id, u.me_person_id, u.kin_lang FROM users u JOIN reminder_prefs p ON p.user_id = u.id "
            "WHERE u.disabled = 0 AND p.enabled = 1 AND NOT EXISTS "
            "(SELECT 1 FROM reminder_log l WHERE l.user_id = u.id AND l.sent_on = ?)", (today.isoformat(),)).fetchall()
        if not rows:
            return out
        g = None
        for r in rows:
            prefs = get_prefs(conn, r["id"])
            if not _in_window(now, prefs["time"]):
                continue
            services = ha_notify.services_for({"id": r["id"]}, conn)
            if not services:
                continue
            g = g or graph_mod.get(conn)
            lang = kin.user_lang({"kin_lang": r["kin_lang"]})
            message = format_digest(collect(g, prefs, r["me_person_id"], today, lang, conn=conn), today)
            if message:
                out.append((r["id"], services, message))
    return out


def run_digest_pass_blocking(now: datetime | None = None, sender=None) -> int:
    """One pass; returns how many digests went out. `sender(service, title,
    message) -> bool` defaults to ha_notify.send_notify (tests inject one)."""
    from . import features
    if not features.on("reminders"):           # switched off (Features): nothing is sent
        return 0
    now = now or config.now()
    if sender is None:
        if not ha_client.has_token():
            ha_client.warn_no_token_once("reminders")
            return 0
        sender = ha_notify.send_notify
    sent = 0
    for user_id, services, message in due_digests(now):
        results = [bool(sender(s, TITLE, message)) for s in services]     # every service, even after a success
        if any(results):
            with db.get_conn() as conn:
                conn.execute("INSERT OR IGNORE INTO reminder_log (user_id, sent_on) VALUES (?, ?)",
                             (user_id, now.date().isoformat()))
            sent += 1
        else:
            logger.warning("No notify service accepted today's reminders for user %s; retrying next minute.", user_id)
    return sent


def prune_log_blocking(keep_days: int = 60) -> None:
    cutoff = (config.today() - timedelta(days=keep_days)).isoformat()
    with db.get_conn() as conn:
        conn.execute("DELETE FROM reminder_log WHERE sent_on < ?", (cutoff,))


async def loop():
    """Every minute: the digest pass. Daily: trim the log."""
    try:
        await run_in_threadpool(ha_notify.check_targets_blocking)
    except Exception:
        logger.exception("Checking the assigned notify services failed")
    tick = 0
    while True:
        try:
            await run_in_threadpool(run_digest_pass_blocking)
            if tick % 1440 == 0:
                await run_in_threadpool(prune_log_blocking)
        except Exception:
            logger.exception("Reminder pass failed")
        tick += 1
        await asyncio.sleep(TICK_SECONDS)
