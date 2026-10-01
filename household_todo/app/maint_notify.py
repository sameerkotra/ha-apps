"""Maintenance notifications (SPEC §14.5).

Who: each item's recipients — the household default an admin picks (Admin → Maintenance), or the item's
own list — plus its assignee; one-off jobs in the Maintenance list: their assignee through the normal task
reminders, or the household recipients while nobody is assigned. Disabled people and anyone who turned
Maintenance off in their own Settings get nothing. Phones come from Home Assistant (ha_notify.services_for).

When: in the hour after each person's own daily time (Settings → Reminders → Send at; 08:00 by default), in
ONE notification listing everything that's their business that morning:
- due soon: `lead_days` before the due date (once per due date);
- due today (once);
- overdue: every `overdue_every` days (3 / 7 / 14; 0 = never) while it stays overdue;
- a one-off job with nobody assigned that is due today (once);
- the new season's suggestions, once per season, in its first two weeks, if any fit and aren't set up.
Each line is logged in maint_notify_log when the send succeeded, so a restart never repeats it; a failed
send is retried on the next tick inside the hour.
"""
import logging
from datetime import date, datetime, timedelta

from . import config, db, ha_client, ha_notify, maint_catalog as cat, maintenance as mt, reminders

logger = logging.getLogger("maint_notify")

TITLE = "Household Todo — Maintenance"
MAX_LINES = 8
SEASON_WINDOW_DAYS = 14


def _when(due: date, today: date) -> str:
    n = (due - today).days
    if n < 0:
        return f"{-n} day{'s' if n != -1 else ''} overdue"
    if n == 0:
        return "due today"
    if n == 1:
        return "due tomorrow"
    return f"due {due.strftime('%a')} {due.day} {due.strftime('%b')}"


def _logged(conn, ref, uid, kind, due, sent_on=""):
    return conn.execute("SELECT 1 FROM maint_notify_log WHERE ref = ? AND user_id = ? AND kind = ? AND due_date = ? "
                        "AND sent_on = ?", (ref, uid, kind, due, sent_on)).fetchone() is not None


def _last_overdue(conn, ref, uid, due) -> date | None:
    r = conn.execute("SELECT MAX(sent_on) FROM maint_notify_log WHERE ref = ? AND user_id = ? AND kind = 'overdue' "
                     "AND due_date = ?", (ref, uid, due)).fetchone()[0]
    return date.fromisoformat(r) if r else None


def collect(conn, today: date) -> dict[str, dict]:
    """user id → {"user": row, "lines": [(sort key, text)], "log": [(ref, kind, due, sent_on)]}, for
    everyone who has something to hear about today (before the time-of-day check)."""
    out: dict[str, dict] = {}

    def add(u, key, text, log):
        slot = out.setdefault(u["id"], {"user": u, "lines": [], "log": []})
        slot["lines"].append((key, text))
        slot["log"].append(log)

    for item in mt.all_items(conn):
        st, due = mt.status_of(item, today)
        if due is None or st == "upcoming":
            continue
        iso = due.isoformat()
        people = mt.eligible(conn, mt.chosen_recipients(conn, item))
        icon = (item["icon"] + " ") if item["icon"] else ""
        for u in people:
            if st == "overdue":
                if not item["overdue_every"]:
                    continue
                last = _last_overdue(conn, item["id"], u["id"], iso)
                if last and (today - last).days < item["overdue_every"]:
                    continue
                add(u, (0, iso), f"• {icon}{item['name']} — {_when(due, today)}", (item["id"], "overdue", iso, today.isoformat()))
            elif due == today:
                if not _logged(conn, item["id"], u["id"], "today", iso):
                    add(u, (1, iso), f"• {icon}{item['name']} — due today", (item["id"], "today", iso, ""))
            elif not _logged(conn, item["id"], u["id"], "soon", iso):
                add(u, (2, iso), f"• {icon}{item['name']} — {_when(due, today)}", (item["id"], "soon", iso, ""))

    lid = mt.list_id(conn)
    if lid:
        jobs = conn.execute("SELECT id, title, due_date FROM tasks WHERE list_id = ? AND completed = 0 AND assigned_to IS NULL "
                            "AND due_date = ?", (lid, today.isoformat())).fetchall()
        if jobs:
            people = mt.eligible(conn, mt.default_recipients())
            for j in jobs:
                for u in people:
                    if not _logged(conn, j["id"], u["id"], "job", j["due_date"]):
                        add(u, (1, j["due_date"]), f"• 🔧 {j['title']} — job due today", (j["id"], "job", j["due_date"], ""))

    sth = mt.south()
    season = cat.season_of(today, sth)
    start = cat.season_start(season, today, sth)
    if (today - start).days < SEASON_WINDOW_DAYS:
        fits = mt.season_suggestions(conn, today)
        if fits:
            key = cat.season_key(today, sth)
            names = ", ".join(s["name"] for s in fits[:3]) + ("…" if len(fits) > 3 else "")
            text = (f"• {cat.SEASON_LABEL[season]}: {len(fits)} job{'s' if len(fits) != 1 else ''} to consider "
                    f"({names}) — see Suggestions")
            for u in mt.eligible(conn, mt.default_recipients()):
                if not _logged(conn, key, u["id"], "season", key):
                    add(u, (3, ""), text, (key, "season", key, ""))
    return out


def run_pass_blocking(now: datetime | None = None, sender=None) -> int:
    """One tick: send each person their maintenance notification if it's their time. Returns how many sent."""
    now = now or config.now()
    if not mt.enabled() or not ha_client.has_token():
        return 0
    sender = sender or ha_notify.send_notify
    today = now.date()
    sent = 0
    with db.get_conn() as conn:
        pending = collect(conn, today)
        for uid, slot in pending.items():
            u = slot["user"]
            try:
                hh, mm = (int(x) for x in (u.get("digest_time") or db.DEFAULT_DIGEST_TIME).split(":"))
            except ValueError:
                hh, mm = 8, 0
            if not reminders._in_time_window(now, hh, mm):
                continue
            services = ha_notify.services_for(u, conn)
            if not services:
                continue
            lines = [t for _, t in sorted(slot["lines"], key=lambda x: x[0])]
            text = "\n".join(lines[:MAX_LINES]) + (f"\n+{len(lines) - MAX_LINES} more" if len(lines) > MAX_LINES else "")
            data = {"url": config.INGRESS_PANEL, "clickAction": config.INGRESS_PANEL} if config.INGRESS_PANEL else None
            ok = False
            for s in services:
                ok = bool(sender(s, TITLE, text, data=data) if data else sender(s, TITLE, text)) or ok
            if not ok:
                continue          # retried next tick, inside the hour
            for ref, kind, due, sent_on in slot["log"]:
                conn.execute("INSERT OR IGNORE INTO maint_notify_log (id, ref, user_id, kind, due_date, sent_on, created_at) "
                             "VALUES (?, ?, ?, ?, ?, ?, ?)", (db.new_id(), ref, uid, kind, due, sent_on, config.now_iso()))
            conn.commit()
            sent += 1
    return sent


def weekly_lines(conn, user_id: str, today: date) -> list[str]:
    """For the weekly summary: this person's maintenance that is overdue or due in the next 7 days."""
    if not mt.enabled():
        return []
    out = []
    for item in mt.all_items(conn):
        st, due = mt.status_of(item, today)
        if due is None or (due - today).days > 7:
            continue
        if user_id not in [u["id"] for u in mt.eligible(conn, mt.chosen_recipients(conn, item))]:
            continue
        out.append((due, f"• 🔧 {item['name']} — {_when(due, today)}"))
    out.sort()
    return [t for _, t in out]
