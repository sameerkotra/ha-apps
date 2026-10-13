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

from . import config, db, ha_client, maint_catalog as cat, maintenance as mt, reminders
from .common import ha_notify

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


class _Who:
    """Everyone who may hear about maintenance (enabled, not muted), the items' own recipient lists and what was
    already sent — each read once per pass instead of once per item and person."""

    def __init__(self, conn, only=None):
        rows = conn.execute("SELECT u.id, u.name, u.username, p.digest_time, COALESCE(p.maintenance_notify, 1) AS on_ "
                            "FROM users u LEFT JOIN user_prefs p ON p.user_id = u.id WHERE u.disabled = 0").fetchall()
        self.users = {r["id"]: dict(r) for r in rows if r["on_"] and (only is None or r["id"] in only)}
        self.default = mt.default_recipients()
        self.custom: dict[str, list[str]] = {}
        for r in conn.execute("SELECT item_id, user_id FROM maint_recipients"):
            self.custom.setdefault(r["item_id"], []).append(r["user_id"])
        self.sent: set[tuple] = set()
        self.last_overdue: dict[tuple, str] = {}
        ids = list(self.users)
        for i in range(0, len(ids), 500):
            chunk = ids[i:i + 500]
            for r in conn.execute("SELECT ref, user_id, kind, due_date, sent_on FROM maint_notify_log "
                                  f"WHERE user_id IN ({','.join('?' * len(chunk))})", chunk):
                self.sent.add((r["ref"], r["user_id"], r["kind"], r["due_date"], r["sent_on"]))
                if r["kind"] == "overdue":
                    k = (r["ref"], r["user_id"], r["due_date"])
                    self.last_overdue[k] = max(self.last_overdue.get(k, ""), r["sent_on"])

    def of(self, ids) -> list[dict]:
        """mt.eligible(), from what's loaded (the same people, in the same order: by id)."""
        return [self.users[i] for i in sorted(set(ids)) if i in self.users]

    def for_item(self, item: dict) -> list[dict]:
        """mt.eligible(conn, mt.chosen_recipients(conn, item))."""
        ids = list(self.custom.get(item["id"], [])) if item["recipients_mode"] == "custom" else list(self.default)
        if item.get("assigned_to"):
            ids.append(item["assigned_to"])
        return self.of(ids)

    def logged(self, ref, uid, kind, due, sent_on="") -> bool:
        return (ref, uid, kind, due, sent_on) in self.sent

    def last_overdue_on(self, ref, uid, due) -> date | None:
        r = self.last_overdue.get((ref, uid, due))
        return date.fromisoformat(r) if r else None


def collect(conn, today: date, only=None) -> dict[str, dict]:
    """user id → {"user": row, "lines": [(sort key, text)], "log": [(ref, kind, due, sent_on)]}, for
    everyone (of `only`, when given) who has something to hear about today."""
    out: dict[str, dict] = {}
    who = _Who(conn, only)
    if not who.users:
        return out

    def add(u, key, text, log):
        slot = out.setdefault(u["id"], {"user": u, "lines": [], "log": []})
        slot["lines"].append((key, text))
        slot["log"].append(log)

    for item in mt.all_items(conn):
        st, due = mt.status_of(item, today)
        if due is None or st == "upcoming":
            continue
        iso = due.isoformat()
        people = who.for_item(item)
        icon = (item["icon"] + " ") if item["icon"] else ""
        for u in people:
            if st == "overdue":
                if not item["overdue_every"]:
                    continue
                last = who.last_overdue_on(item["id"], u["id"], iso)
                if last and (today - last).days < item["overdue_every"]:
                    continue
                add(u, (0, iso), f"• {icon}{item['name']} — {_when(due, today)}", (item["id"], "overdue", iso, today.isoformat()))
            elif due == today:
                if not who.logged(item["id"], u["id"], "today", iso):
                    add(u, (1, iso), f"• {icon}{item['name']} — due today", (item["id"], "today", iso, ""))
            elif not who.logged(item["id"], u["id"], "soon", iso):
                add(u, (2, iso), f"• {icon}{item['name']} — {_when(due, today)}", (item["id"], "soon", iso, ""))

    lid = mt.list_id(conn)
    if lid:
        jobs = conn.execute("SELECT id, title, due_date FROM tasks WHERE list_id = ? AND completed = 0 AND assigned_to IS NULL "
                            "AND due_date = ?", (lid, today.isoformat())).fetchall()
        if jobs:
            people = who.of(who.default)
            for j in jobs:
                for u in people:
                    if not who.logged(j["id"], u["id"], "job", j["due_date"]):
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
            for u in who.of(who.default):
                if not who.logged(key, u["id"], "season", key):
                    add(u, (3, ""), text, (key, "season", key, ""))
    return out


def _due_now(conn, now: datetime) -> dict[date, set[str]]:
    """{the day whose send time it is: the people whose window is open now} — checked before anything else is
    worked out, so the 59 ticks an hour when it's nobody's time cost one query."""
    out: dict[date, set[str]] = {}
    for r in conn.execute("SELECT u.id, p.digest_time FROM users u LEFT JOIN user_prefs p ON p.user_id = u.id "
                          "WHERE u.disabled = 0 AND COALESCE(p.maintenance_notify, 1) = 1"):
        hhmm = r["digest_time"] if reminders._parse_hhmm(r["digest_time"] or "") else db.DEFAULT_DIGEST_TIME
        day = reminders.window_day(now, hhmm)
        if day is not None:
            out.setdefault(day, set()).add(r["id"])
    return out


def run_pass_blocking(now: datetime | None = None, sender=None) -> int:
    """One tick: send each person their maintenance notification if it's their time. Returns how many sent."""
    now = now or config.now()
    if not mt.enabled() or not ha_client.has_token():
        return 0
    sender = sender or ha_notify.send_notify
    sent = 0
    try:
        with db.get_conn() as conn:
            for today, uids in sorted(_due_now(conn, now).items()):
                pending = collect(conn, today, uids)
                for uid, slot in pending.items():
                    u = slot["user"]
                    services = ha_notify.services_for(u, conn)
                    if not services:
                        continue
                    lines = [t for _, t in sorted(slot["lines"], key=lambda x: x[0])]
                    text = "\n".join(lines[:MAX_LINES]) + (f"\n+{len(lines) - MAX_LINES} more" if len(lines) > MAX_LINES else "")
                    page = config.SIDEBAR_PAGE and config.SIDEBAR_PAGE + "/maintenance"    # the Maintenance tab (deeplink.js)
                    data = {"url": page, "clickAction": page} if page else None
                    ok = False
                    for s in services:
                        ok = bool(sender(s, TITLE, text, data=data) if data else sender(s, TITLE, text)) or ok
                    if not ok:
                        reminders._after_failed_send()
                        continue          # retried next tick, inside the hour
                    for ref, kind, due, sent_on in slot["log"]:
                        conn.execute("INSERT OR IGNORE INTO maint_notify_log (id, ref, user_id, kind, due_date, sent_on, created_at) "
                                     "VALUES (?, ?, ?, ?, ?, ?, ?)", (db.new_id(), ref, uid, kind, due, sent_on, config.now_iso()))
                    conn.commit()
                    sent += 1
    except reminders.HomeAssistantDown:
        reminders._down("maintenance")
    return sent


def weekly_lines(conn, user_id: str, today: date) -> list[str]:
    """For the weekly summary: this person's maintenance that is overdue or due in the next 7 days."""
    if not mt.enabled():
        return []
    who = _Who(conn, {user_id})
    if user_id not in who.users:
        return []
    out = []
    for item in mt.all_items(conn):
        st, due = mt.status_of(item, today)
        if due is None or (due - today).days > 7:
            continue
        if not who.for_item(item):
            continue
        out.append((due, f"• 🔧 {item['name']} — {_when(due, today)}"))
    out.sort()
    return [t for _, t in out]
