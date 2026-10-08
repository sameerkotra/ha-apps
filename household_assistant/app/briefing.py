"""The morning briefing (HOUSEHOLD_ASSISTANT_SPEC.md §16): once a day, at the time a person chose, the assistant asks
the household apps for that person's day — exactly as if they had asked — and sends a short summary to their phone.

- What it asks, each only when the person may use that tool (catalogue.for_user): today's tasks and schedule
  (`todo.tasks`), birthdays and anniversaries this week (`tree.birthdays`), bills in the next 3 days
  (`finance.bills`) and money owed in Splitpot (`splitpot.balances`). A part with nothing in it is left out;
  "No tasks for today" is kept.
- No AI model is needed: each app's own summary line is used as it is, so the briefing works (and costs nothing)
  without one, and says only what the apps said.
- It shows up in the person's conversation as "🌅 Morning briefing", with Sources and "What was shared", like any
  answer, and goes to the phones Home Assistant links to the person (Settings → People → Track device).
- Settings are per person (`briefings`): on or off, the time (HH:MM, Home Assistant's zone), every day or weekdays.
  It is sent at most once a day, from that time until three hours after it (an app that was stopped at 7 and
  started at noon doesn't send a morning briefing in the afternoon).
- Who: people the assistant is on for (Admin → People); children only while *Children may ask* is on.
"""
from __future__ import annotations

import json
import logging
import re
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta

from . import catalogue, config, db, engine, settings
from .common import app_bus as bus
from .common import assist_tools, ha_notify, ha_people

logger = logging.getLogger("briefing")

TITLE = "🌅 Morning briefing"
QUESTION = "Morning briefing"
TIME_RE = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")
DAYS = ("every", "weekdays")
DEFAULT_TIME = "07:30"
LATE = timedelta(hours=3)           # never sent later than this after the chosen time
MAX_LINE = 300
MAX_MESSAGE = 1200

# (tool, args, emoji, always: kept even when the app found nothing)
PARTS = (
    ("todo.tasks", {"when": "today"}, "📋", True),
    ("tree.birthdays", {"days": 7}, "🎂", False),
    ("finance.bills", {"days": 3}, "💳", False),
    ("splitpot.balances", {}, "🤝", False),
)

_running: set[str] = set()
_lock = threading.Lock()


# --------------------------------------------------------------------------------------------- settings

def get(conn, user_id: str) -> dict:
    r = conn.execute("SELECT * FROM briefings WHERE user_id = ?", (user_id,)).fetchone()
    if r is None:
        return {"on": False, "time": DEFAULT_TIME, "days": "every", "lastSent": None}
    return {"on": bool(r["on_"]), "time": r["at"], "days": r["days"], "lastSent": r["last_sent"]}


def save(conn, user_id: str, on: bool, time: str, days: str) -> dict:
    if not TIME_RE.match(time or ""):
        raise ValueError("The time must be HH:MM (24-hour), e.g. 07:30.")
    if days not in DAYS:
        raise ValueError("Days must be every day or weekdays.")
    conn.execute("INSERT INTO briefings (user_id, on_, at, days, updated_at) VALUES (?, ?, ?, ?, ?) "
                 "ON CONFLICT(user_id) DO UPDATE SET on_ = excluded.on_, at = excluded.at, days = excluded.days, "
                 "updated_at = excluded.updated_at", (user_id, 1 if on else 0, time, days, engine._iso()))
    return get(conn, user_id)


def phones(user_id: str) -> list[dict]:
    """The person's phones in Home Assistant: [{name, label}] (what the settings show)."""
    p = ha_people.person_for(user_id) or {}
    return [{"service": ph["service"], "label": ph.get("label") or ph.get("name") or ph["service"]}
            for ph in p.get("phones", []) if ph.get("service")]


def parts_for(conn, user: dict) -> list[dict]:
    """What the person's briefing would include: [{tool, app, appName}] for the tools they may use."""
    tools = catalogue.for_user(conn, user)
    return [{"tool": name, "app": tools[name]["app"], "appName": tools[name]["appName"]}
            for name, _args, _e, _always in PARTS if name in tools]


def may_have(user: dict) -> str | None:
    """None when the person may have a briefing, else why not."""
    if not user["enabled"]:
        return "An admin has turned the assistant off for you (Admin → People)."
    if user["is_child"] and not settings.get("children_may_ask"):
        return "The assistant isn't open to children here (an admin can change that in App settings)."
    return None


# --------------------------------------------------------------------------------------------- making one

def _line(emoji: str, text: str) -> str:
    text = " ".join(str(text or "").split())
    return f"{emoji} " + (text if len(text) <= MAX_LINE else text[:MAX_LINE - 1].rstrip() + "…")


def build(user: dict, *, timeout: float = engine.CALL_TIMEOUT) -> dict:
    """Ask the apps as `user` and store the result as a finished question; {question, lines, sources}."""
    now = config.utcnow()
    qid = bus.new_ulid(now)
    with db.get_conn() as conn:
        tools = catalogue.for_user(conn, user)
        wanted = [(name, args, emoji, always) for name, args, emoji, always in PARTS if name in tools]
        conn.execute("INSERT INTO questions (id, user_id, asked_at, text, state) VALUES (?, ?, ?, ?, 'calling')",
                     (qid, user["id"], engine._iso(now), QUESTION))
        rows = []
        for name, args, _e, _a in wanted:
            cid = bus.new_ulid()
            conn.execute("INSERT INTO calls (id, question_id, app, tool, args, round, state, sent_at) VALUES "
                         "(?, ?, ?, ?, ?, 1, 'sent', ?)", (cid, qid, tools[name]["app"], name,
                                                          json.dumps(args, sort_keys=True), engine._iso()))
            rows.append(cid)
    answers = []
    if wanted:
        with ThreadPoolExecutor(max_workers=len(wanted)) as pool:
            futures = [pool.submit(engine.call, cid, qid, user, tools[name]["app"], name, args, timeout=timeout)
                       for cid, (name, args, _e, _a) in zip(rows, wanted)]
            answers = [f.result() for f in futures]
    lines, used = [], []
    for (name, _args, emoji, always), a in zip(wanted, answers):
        res = a.get("result") if a.get("state") == "ok" else None
        if not isinstance(res, dict):
            continue
        if not always and not res.get("items"):
            continue
        lines.append(_line(emoji, res.get("text")))
        used.append(tools[name]["appName"])
    if not lines:
        lines = ["Nothing from the household apps this morning."]
    day = config.now()
    head = f"{day.strftime('%A')} {day.day} {day.strftime('%B')}"
    answer = f"**{head}**\n\n" + "\n\n".join(lines)
    with db.get_conn() as conn:
        conn.execute("UPDATE questions SET state = 'done', answer = ?, rounds = 1, seconds = ? WHERE id = ?",
                     (answer, round((config.utcnow() - now).total_seconds(), 2), qid))
        conn.execute("INSERT INTO usage_days (day, calls) VALUES (?, ?) ON CONFLICT(day) DO UPDATE SET "
                     "calls = calls + excluded.calls", (config.today().isoformat(), len(rows)))
    engine.touch(qid)
    return {"question": qid, "head": head, "lines": lines, "sources": used}


_page = {"value": None, "known": False}


def _sidebar_page() -> str | None:
    if not _page["known"]:
        _page["value"] = assist_tools.sidebar_page(config.SLUG, token=config.SUPERVISOR_TOKEN,
                                                   supervisor_api=config.SUPERVISOR_API)
        _page["known"] = True
    return _page["value"]


def send(user: dict) -> dict:
    """Make the person's briefing and send it to their phones: {question, sent: {phone: ok}, phones}."""
    made = build(user)
    message = "\n".join(made["lines"])
    if len(message) > MAX_MESSAGE:
        message = message[:MAX_MESSAGE - 1].rstrip() + "…"
    data = {}
    page = _sidebar_page()
    if page:
        data = {"url": page, "clickAction": page}
    targets = [p["service"] for p in phones(user["id"])]
    sent = ha_notify.send_to_services(targets, f"{TITLE} · {made['head']}", message, data or None) if targets else {}
    return {"question": made["question"], "sent": sent, "phones": targets}


# --------------------------------------------------------------------------------------------- the clock

def due(row, now: datetime) -> bool:
    """Whether a briefing row is due at `now` (Home Assistant's zone)."""
    if not row["on_"] or not TIME_RE.match(row["at"] or ""):
        return False
    if row["days"] == "weekdays" and now.weekday() >= 5:
        return False
    today = now.date().isoformat()
    if row["last_sent"] == today:
        return False
    h, m = (int(x) for x in row["at"].split(":"))
    at = now.replace(hour=h, minute=m, second=0, microsecond=0)
    return at <= now < at + LATE


def check() -> int:
    """Every minute: send the briefings that are due. Returns how many were started."""
    now = config.now()
    today = now.date().isoformat()
    started = 0
    with db.get_conn() as conn:
        rows = conn.execute("SELECT b.*, u.id AS uid, u.name, u.enabled, u.is_child FROM briefings b "
                            "JOIN users u ON u.id = b.user_id WHERE b.on_ = 1").fetchall()
        todo = []
        for r in rows:
            if not due(r, now):
                continue
            user = {"id": r["uid"], "name": r["name"], "enabled": bool(r["enabled"]), "is_child": bool(r["is_child"])}
            if may_have(user):
                continue
            # marked first, so a slow or failing app never makes it send twice
            conn.execute("UPDATE briefings SET last_sent = ? WHERE user_id = ?", (today, r["uid"]))
            todo.append(user)
    for user in todo:
        with _lock:
            if user["id"] in _running:
                continue
            _running.add(user["id"])
        threading.Thread(target=_send_logged, args=(user,), name=f"briefing-{user['id'][:8]}", daemon=True).start()
        started += 1
    return started


def _send_logged(user: dict) -> None:
    try:
        out = send(user)
        ok = sum(1 for v in out["sent"].values() if v)
        logger.info("Sent %s's morning briefing to %s of %s phones.", user["name"], ok, len(out["sent"]))
    except Exception:
        logger.exception("The morning briefing for %s failed", user["name"])
    finally:
        with _lock:
            _running.discard(user["id"])
