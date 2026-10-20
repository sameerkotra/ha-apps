"""Card payment reminders and subscription change notifications (SPEC.md section 31).

The same machinery as the overdue statement reminders (statement_due.py, section 27), run by its one background loop
(`statement_due.loop` calls `run_check` here after its own): each person chooses their own on Settings → My settings
(notify_prefs; no row = No notifications), only an account's owner is notified, everything due for a person in one
check is one notification, each one is sent once (payment_reminders / subscription_alerts, so a restart never
repeats one), a failed send is retried for six hours, a notification never carries an account number, and a tap
opens the page it is about. "Now" is statement_due's clock (tests replace it).

* **Card payments** (kind `card_payments`): 1 to 6 of 7, 5, 3, 2, 1 days before the due date, on the day, 1 or 3
  days after, at a time of day. Per card, only the newest reminder whose day and time have passed is sent (after the
  app was off, only the newest), while its due date is unpaid and not marked as paid, and never for a due date
  before the setting (card_payments.py).
* **Subscription changes** (kind `subscriptions`): each price change or new subscription (subscriptions.py) once, at
  the person's chosen time after the check that first saw it (recorded as 'pending' with found_at).
"""
from __future__ import annotations

import json
import logging
import re
from datetime import date, datetime, time, timedelta

from . import card_payments, statement_due, subscriptions
from .db import get_db

logger = logging.getLogger(__name__)

KINDS = ("card_payments", "subscriptions")
CARD_OFFSETS = ((-7, "7 days before"), (-5, "5 days before"), (-3, "3 days before"), (-2, "2 days before"),
                (-1, "1 day before"), (0, "On the day"), (1, "1 day after"), (3, "3 days after"))
MAX_OFFSETS = 6
DEFAULT_AT = statement_due.DEFAULT_AT
_TIME_RE = re.compile(r"^([01]\d|2[0-3]):([0-5]\d)$")
MAX_LISTED = 4                  # changes named in one notification; the rest are "and N more"


# ------------------------------------------------------------------ prefs (Setup → Settings)

def get_prefs(conn, user_id: str, kind: str) -> dict | None:
    r = conn.execute("SELECT * FROM notify_prefs WHERE user_id = ? AND kind = ?", (user_id, kind)).fetchone()
    if r is None:
        return None
    try:
        offsets = sorted({int(o) for o in json.loads(r["offsets"] or "[]")})
    except (ValueError, TypeError):
        offsets = []
    return {"mode": r["mode"], "offsets": offsets, "at_time": r["at_time"] or DEFAULT_AT, "updated_at": r["updated_at"]}


def parse_form(kind: str, mode: str, offsets: list[str], at: str) -> tuple[dict | None, str | None]:
    """({mode, offsets, at_time}, error) for one card of Setup → Settings."""
    mode = str(mode or "").strip()
    if mode not in ("off", "on"):
        return None, "Choose No notifications or " + ("Remind me." if kind == "card_payments" else "Notify me.")
    if mode == "off":
        return {"mode": "off", "offsets": [], "at_time": DEFAULT_AT}, None
    picked = set()
    if kind == "card_payments":
        allowed = dict(CARD_OFFSETS)
        for o in offsets or []:
            o = str(o).strip()
            try:
                value = int(o)
            except ValueError:
                return None, "That reminder day isn't one of the choices."
            if value not in allowed:
                return None, "That reminder day isn't one of the choices."
            picked.add(value)
        if not picked:
            return None, "Pick at least one, or choose No notifications."
        if len(picked) > MAX_OFFSETS:
            return None, f"Pick at most {MAX_OFFSETS}."
    at = str(at or DEFAULT_AT).strip()
    if not _TIME_RE.match(at):
        return None, "Give the time as HH:MM."
    return {"mode": "on", "offsets": sorted(picked), "at_time": at}, None


def save_prefs(conn, user_id: str, kind: str, p: dict) -> None:
    conn.execute(
        "INSERT INTO notify_prefs (user_id, kind, mode, offsets, at_time, updated_at) "
        "VALUES (?, ?, ?, ?, ?, datetime('now')) ON CONFLICT(user_id, kind) DO UPDATE SET mode = excluded.mode, "
        "offsets = excluded.offsets, at_time = excluded.at_time, updated_at = excluded.updated_at",
        (user_id, kind, p["mode"], json.dumps(p["offsets"]), p["at_time"]))


def _at(prefs: dict) -> time:
    hh, mm = (int(x) for x in prefs["at_time"].split(":"))
    return time(hh, mm)


def _users(conn, kind: str) -> list[str]:
    return [r[0] for r in conn.execute("SELECT user_id FROM notify_prefs WHERE kind = ? AND mode = 'on' "
                                       "ORDER BY user_id", (kind,))]


def _retry(row, at: datetime, key, given_up: set, what: str) -> bool | None:
    """For a row already in a send log: None = don't send, True = send again (a failed one within six hours)."""
    if row["result"] != "failed":
        return None
    first = statement_due.local_from_utc(row["sent_at"])
    if first is None or (at - first).total_seconds() >= statement_due.RETRY_SECONDS:
        if key not in given_up:
            given_up.add(key)
            logger.warning("Gave up on a %s for %s after six hours of failed sends", what, key[0])
        return None
    return True


_given_up: set = set()


# ------------------------------------------------------------------ card payments

def card_plan(conn, at: datetime) -> list[dict]:
    """[{"user_id", "kind": "card_payments", "items": [{account_id, name, last4, due_date, offset_days, retry}]}]."""
    if not card_payments.enabled():
        return []
    on = at.date()
    plan = []
    for uid in _users(conn, "card_payments"):
        prefs = get_prefs(conn, uid, "card_payments")
        if not prefs or not prefs["offsets"]:
            continue
        clock = _at(prefs)
        items = []
        for a in card_payments._cards(conn, uid):
            st = card_payments.status(conn, a, on)
            if st is None:
                continue
            cycles = [st["next"]]
            if st["next"]["days"] > 0:                       # the previous one only until the next due date
                cycles.insert(0, st["previous"])
            passed = []
            for c in cycles:
                if c["state"] != "unpaid":
                    continue
                for o in prefs["offsets"]:
                    when = datetime.combine(c["due_date"] + timedelta(days=o), clock)
                    if when <= at:
                        passed.append((when, c["due_date"], o))
            if not passed:
                continue
            _, due, offset = max(passed)
            key = (uid, a["id"], due.isoformat(), offset)
            row = conn.execute("SELECT result, sent_at FROM payment_reminders WHERE user_id = ? AND account_id = ? "
                               "AND due_date = ? AND offset_days = ?", key).fetchone()
            retry = False
            if row is not None:
                retry = _retry(row, at, ("card",) + key, _given_up, "card payment reminder")
                if retry is None:
                    continue
            items.append({"account_id": a["id"], "name": a["name"], "last4": a["account_number_last4"],
                          "due_date": due, "offset_days": offset, "retry": retry})
        if items:
            plan.append({"user_id": uid, "kind": "card_payments", "items": items})
    return plan


def _when(due: date, on: date) -> str:
    days = (due - on).days
    if days == 0:
        return f"payment due today ({statement_due.label(due)})"
    if days > 0:
        return f"payment due {statement_due.label(due)}, in {days} day{'s' if days != 1 else ''}"
    return f"payment was due {statement_due.label(due)}, {-days} day{'s' if days != -1 else ''} ago"


def card_message(items: list[dict], on: date) -> tuple[str, str]:
    """(title, message): "Card payment due" / "Visa — payment due Nov 5, in 3 days. Mark it paid in Finance."."""
    if len(items) == 1:
        it = items[0]
        title = "Card payment overdue" if it["due_date"] < on else "Card payment due"
        return title, f"{statement_due.safe_name(it['name'], it.get('last4'))} — {_when(it['due_date'], on)}."
    parts = ", ".join(f"{statement_due.safe_name(it['name'], it.get('last4'))} ({statement_due.label(it['due_date'])})"
                      for it in sorted(items, key=lambda i: (i["due_date"], i["name"].casefold())))
    return f"{len(items)} card payments due", f"{parts}."


def card_link(items: list[dict]) -> dict | None:
    """The tap: the card's page (several cards: Home, which lists them)."""
    from . import tools
    panel = tools._panel()
    if not panel:
        return None
    url = f"{panel}/accounts/{items[0]['account_id']}" if len(items) == 1 else f"{panel}/dashboard"
    return {"url": url, "clickAction": url}


# ------------------------------------------------------------------ subscription changes

def _next_at(found: datetime, clock: time) -> datetime:
    """The person's chosen time on or after the check that found the change."""
    same_day = datetime.combine(found.date(), clock)
    return same_day if found <= same_day else same_day + timedelta(days=1)


def subs_plan(conn, at: datetime) -> tuple[list[dict], list[tuple]]:
    """(plan, newly found): the changes to send now per person, and the (user, account, series, change) seen for
    the first time at this check (recorded as 'pending')."""
    if not subscriptions.enabled():
        return [], []
    plan, found = [], []
    for uid in _users(conn, "subscriptions"):
        prefs = get_prefs(conn, uid, "subscriptions")
        if not prefs:
            continue
        clock = _at(prefs)
        last4 = {r["id"]: r["account_number_last4"] for r in conn.execute(
            "SELECT id, account_number_last4 FROM accounts WHERE user_id = ?", (uid,))}
        items = []
        for c in subscriptions.changes(conn, uid, at.date()):
            key = (uid, c["account_id"], c["series"], c["change_key"])
            row = conn.execute("SELECT result, found_at, sent_at FROM subscription_alerts WHERE user_id = ? AND "
                               "account_id = ? AND series = ? AND change_key = ?", key).fetchone()
            first_seen = at if row is None else statement_due.local_from_utc(row["found_at"]) or at
            if row is None:
                found.append(key)
            retry = False
            if row is not None and row["result"] != "pending":
                retry = _retry(row, at, ("subs",) + key, _given_up, "subscription change notification")
                if retry is None:
                    continue
            elif _next_at(first_seen, clock) > at:
                continue
            items.append({**c, "last4": last4.get(c["account_id"]), "retry": retry})
        if items:
            plan.append({"user_id": uid, "kind": "subscriptions", "items": items})
    return plan, found


def subs_message(items: list[dict]) -> tuple[str, str]:
    def line(c):
        return subscriptions.text(c, statement_due.safe_name(c["name"], c.get("last4")))
    if len(items) == 1:
        c = items[0]
        return ("New subscription" if c["kind"] == "new" else "Subscription price change"), line(c) + "."
    shown = items[:MAX_LISTED]
    more = len(items) - len(shown)
    return (f"{len(items)} subscription changes",
            "; ".join(line(c) for c in shown) + (f"; and {more} more" if more else "") + ".")


def subs_link() -> dict | None:
    from . import tools
    panel = tools._panel()
    if not panel:
        return None
    return {"url": f"{panel}/recurring", "clickAction": f"{panel}/recurring"}


# ------------------------------------------------------------------ one check

def run_check(at: datetime | None = None) -> int:
    """One check (blocking; never sends while holding a database connection). Returns notifications sent."""
    from .common import ha_notify
    at = at or statement_due.now()
    stamp = statement_due._utc_text(at)
    with get_db() as conn:
        cards = card_plan(conn, at)
        subs, found = subs_plan(conn, at)
        for key in found:
            conn.execute("INSERT OR IGNORE INTO subscription_alerts (user_id, account_id, series, change_key, found_at, "
                         "result) VALUES (?, ?, ?, ?, ?, 'pending')", key + (stamp,))
        conn.commit()
        plan = cards + subs
        for p in plan:
            p["services"] = statement_due.phones_for(conn, p["user_id"])
    sent = 0
    for p in plan:
        if not p["services"]:
            p["result"] = "no_phone"
            continue
        if p["kind"] == "card_payments":
            title, text = card_message(p["items"], at.date())
            link = card_link(p["items"])
        else:
            title, text = subs_message(p["items"])
            link = subs_link()
        results = ha_notify.send_to_services(p["services"], title, text, link)
        p["result"] = "sent" if any(results.values()) else "failed"
        sent += p["result"] == "sent"
    if plan:
        with get_db() as conn:
            for p in plan:
                for it in p["items"]:
                    if p["kind"] == "card_payments":
                        conn.execute(
                            "INSERT INTO payment_reminders (user_id, account_id, due_date, offset_days, sent_at, result) "
                            "VALUES (?, ?, ?, ?, ?, ?) ON CONFLICT(user_id, account_id, due_date, offset_days) "
                            "DO UPDATE SET result = excluded.result",
                            (p["user_id"], it["account_id"], it["due_date"].isoformat(), it["offset_days"], stamp,
                             p["result"]))
                    else:
                        # sent_at: the first try (a retry keeps it, so the six hours count from there)
                        conn.execute(
                            "UPDATE subscription_alerts SET result = ?, sent_at = COALESCE(sent_at, ?) "
                            "WHERE user_id = ? AND account_id = ? AND series = ? AND change_key = ?",
                            (p["result"], stamp, p["user_id"], it["account_id"], it["series"], it["change_key"]))
            conn.commit()
    return sent
