"""The morning and evening briefings (HOUSEHOLD_ASSISTANT_SPEC.md §16): once a day each, at the times a person chose,
the assistant asks the household apps about that person's day — exactly as if they had asked — and sends a short
summary to their phone, and reads it aloud on a Home Assistant speaker when they picked one.

- **Morning** (🌅): today's tasks and schedule (`todo.tasks today`), birthdays and anniversaries this week
  (`tree.birthdays`), bills in the next 3 days (`finance.bills`) and money owed in Splitpot (`splitpot.balances`).
- **Evening** (🌙): tomorrow's tasks and schedule — trash day included (`todo.tasks tomorrow`), birthdays and
  anniversaries today and tomorrow (`tree.birthdays 2`) and bills due by tomorrow (`finance.bills 2`).
- Each part only when the person may use that tool (catalogue.for_user), and only when it has something in it;
  the tasks line is always kept ("No tasks for tomorrow").
- No AI model is needed: each app's own summary line is used as it is, so a briefing works (and costs nothing)
  without one, and says only what the apps said.
- It shows up in the person's conversation ("Morning briefing" / "Evening briefing"), with Sources and "What was
  shared", like any answer, and goes to the phones Home Assistant links to the person (Settings → People → Track
  device).
- **Speaker** (optional, per person): a `media_player` in Home Assistant — one of those the admin allows (App
  settings → Briefings → *Speakers briefings may use*; empty: any) — the briefing is read aloud there with Home
  Assistant's text-to-speech (`tts.speak` with the first `tts.*` engine, Settings → Voice assistants), without the
  emoji; never during the admin's *Quiet hours* (it still goes to the phones).
- Settings are per person (`briefings`): the morning on/off, time, every day or weekdays; the evening on/off and
  time (same days); the speaker. Each is sent at most once a day, from its time until three hours after it (also
  past midnight: a 23:00 one missed while the app restarted still goes at 00:30, as that day's).
- At most SENDS_PER_HOUR briefings an hour per person, "Send me one now" and the day's own together, counted in
  `rate_events` (clearing one's questions doesn't reset it); a "Send me one now" takes its place first.
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
from .common import assist_tools, ha_client, ha_notify, ha_people

logger = logging.getLogger("briefing")

TIME_RE = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")
SPEAKER_RE = re.compile(r"^media_player\.[a-z0-9_]{1,100}$")
DAYS = ("every", "weekdays")
LATE = timedelta(hours=3)           # never sent later than this after the chosen time
MAX_LINE = 300
MAX_MESSAGE = 1200
SENDS_PER_HOUR = 5                  # "Send me one now" (and the day's own) per person per hour

# kind -> its words and what it asks: (tool, args, emoji, always: kept even when the app found nothing)
KINDS = {
    "morning": {"title": "🌅 Morning briefing", "question": "Morning briefing", "default": "07:30",
                "empty": "Nothing from the household apps this morning.", "day": 0,
                "parts": (("todo.tasks", {"when": "today"}, "📋", True),
                          ("tree.birthdays", {"days": 7}, "🎂", False),
                          ("finance.bills", {"days": 3}, "💳", False),
                          ("splitpot.balances", {}, "🤝", False))},
    "evening": {"title": "🌙 Evening briefing", "question": "Evening briefing", "default": "20:00",
                "empty": "Nothing from the household apps for tomorrow.", "day": 1,
                "parts": (("todo.tasks", {"when": "tomorrow"}, "📋", True),
                          ("tree.birthdays", {"days": 2}, "🎂", False),
                          ("finance.bills", {"days": 2}, "💳", False))},
}
# the briefings table's columns for each kind
COLS = {"morning": ("on_", "at", "last_sent"), "evening": ("evening_on", "evening_at", "evening_last_sent")}
# kept for the old name
TITLE = KINDS["morning"]["title"]
QUESTION = KINDS["morning"]["question"]
PARTS = KINDS["morning"]["parts"]

_running: set[tuple[str, str]] = set()
_lock = threading.Lock()


# --------------------------------------------------------------------------------------------- settings

def get(conn, user_id: str) -> dict:
    r = conn.execute("SELECT * FROM briefings WHERE user_id = ?", (user_id,)).fetchone()
    if r is None:
        return {"on": False, "time": KINDS["morning"]["default"], "days": "every", "lastSent": None,
                "eveningOn": False, "eveningTime": KINDS["evening"]["default"], "speaker": None}
    return {"on": bool(r["on_"]), "time": r["at"], "days": r["days"], "lastSent": r["last_sent"],
            "eveningOn": bool(r["evening_on"]), "eveningTime": r["evening_at"] or KINDS["evening"]["default"],
            "speaker": r["speaker"]}


NOT_A_SPEAKER = "Choose one of Home Assistant's speakers."


def check_speaker(speaker) -> None:
    """A new speaker must be one of Home Assistant's that the admin allows (it asks Home Assistant: call it before
    opening a database transaction, then save(..., speaker_checked=True))."""
    if speaker not in (False, None, "") and not SPEAKER_RE.match(speaker):
        raise ValueError("The speaker must be a media player, e.g. media_player.kitchen.")
    if speaker not in (False, None, "") and not is_speaker(speaker):
        raise ValueError(NOT_A_SPEAKER)


def save(conn, user_id: str, on: bool, time: str, days: str, *, evening_on: bool | None = None,
         evening_time: str | None = None, speaker: str | None | bool = False, speaker_checked: bool = False) -> dict:
    """Save the person's settings; the evening and speaker only when given (speaker None clears it).
    `speaker_checked`: check_speaker() already said yes (outside the transaction)."""
    if not TIME_RE.match(time or ""):
        raise ValueError("The time must be HH:MM (24-hour), e.g. 07:30.")
    if days not in DAYS:
        raise ValueError("Days must be every day or weekdays.")
    if evening_time is not None and not TIME_RE.match(evening_time):
        raise ValueError("The evening time must be HH:MM (24-hour), e.g. 20:00.")
    if speaker not in (False, None, "") and not SPEAKER_RE.match(speaker):
        raise ValueError("The speaker must be a media player, e.g. media_player.kitchen.")
    old = get(conn, user_id)
    if speaker not in (False, None, "") and speaker != old["speaker"] and not speaker_checked \
            and not is_speaker(speaker):
        raise ValueError(NOT_A_SPEAKER)
    ev_on = old["eveningOn"] if evening_on is None else bool(evening_on)
    ev_at = evening_time or old["eveningTime"]
    spk = old["speaker"] if speaker is False else (speaker or None)
    conn.execute("INSERT INTO briefings (user_id, on_, at, days, evening_on, evening_at, speaker, updated_at) "
                 "VALUES (?, ?, ?, ?, ?, ?, ?, ?) ON CONFLICT(user_id) DO UPDATE SET on_ = excluded.on_, "
                 "at = excluded.at, days = excluded.days, evening_on = excluded.evening_on, "
                 "evening_at = excluded.evening_at, speaker = excluded.speaker, updated_at = excluded.updated_at",
                 (user_id, 1 if on else 0, time, days, 1 if ev_on else 0, ev_at, spk, engine._iso()))
    return get(conn, user_id)


def phones(user_id: str) -> list[dict]:
    """The person's phones in Home Assistant: [{name, label}] (what the settings show)."""
    p = ha_people.person_for(user_id) or {}
    return [{"service": ph["service"], "label": ph.get("label") or ph.get("name") or ph["service"]}
            for ph in p.get("phones", []) if ph.get("service")]


def speakers() -> dict:
    """{speakers: [{entity, name}], tts: the first text-to-speech engine or None} from Home Assistant's states;
    only the speakers the admin allows (App settings → Briefings; empty: all)."""
    states = ha_client.fetch_states_blocking() or []
    allowed = settings.allowed_speakers()
    players = sorted(({"entity": s["entity_id"], "name": (s.get("attributes") or {}).get("friendly_name")
                       or s["entity_id"]} for s in states
                      if str(s.get("entity_id", "")).startswith("media_player.") and SPEAKER_RE.match(s["entity_id"])
                      and (not allowed or s["entity_id"] in allowed)),
                     key=lambda x: x["name"].casefold())
    tts = sorted(s["entity_id"] for s in states if str(s.get("entity_id", "")).startswith("tts."))
    return {"speakers": players, "tts": tts[0] if tts else None}


def is_speaker(entity: str) -> bool:
    """Whether `entity` is one of the speakers Home Assistant has now and the admin allows (speakers())."""
    return any(p["entity"] == entity for p in speakers()["speakers"])


def _minutes(hhmm: str) -> int:
    h, m = (int(x) for x in hhmm.split(":"))
    return h * 60 + m


def quiet_now(now: datetime | None = None) -> bool:
    """Whether it is the admin's quiet hours now (Home Assistant's zone): no reading aloud. They may run past
    midnight (22:00–07:00); both times empty, or the same: never quiet."""
    start, end = settings.get("quiet_from"), settings.get("quiet_until")
    if not (TIME_RE.match(start or "") and TIME_RE.match(end or "")) or start == end:
        return False
    now = now or config.now()
    t, a, b = now.hour * 60 + now.minute, _minutes(start), _minutes(end)
    return a <= t < b if a < b else (t >= a or t < b)


def parts_for(conn, user: dict, kind: str = "morning") -> list[dict]:
    """What the person's briefing would include: [{tool, app, appName}] for the tools they may use."""
    tools = catalogue.for_user(conn, user)
    return [{"tool": name, "app": tools[name]["app"], "appName": tools[name]["appName"]}
            for name, _args, _e, _always in KINDS[kind]["parts"] if name in tools]


def may_have(user: dict) -> str | None:
    """None when the person may have a briefing, else why not."""
    if not user["enabled"]:
        return "An admin has turned the assistant off for you (Admin → People)."
    if user["is_child"] and not settings.get("children_may_ask"):
        return "The assistant isn't open to children here (an admin can change that in App settings)."
    return None


# --------------------------------------------------------------------------------------------- making one

def _short(text: str) -> str:
    text = " ".join(str(text or "").split())
    return text if len(text) <= MAX_LINE else text[:MAX_LINE - 1].rstrip() + "…"


def build(user: dict, kind: str = "morning", *, timeout: float = engine.CALL_TIMEOUT) -> dict:
    """Ask the apps as `user` and store the result as a finished question; {question, head, lines, spoken, sources}."""
    k = KINDS[kind]
    now = config.utcnow()
    qid = bus.new_ulid(now)
    with db.get_conn() as conn:
        tools = catalogue.for_user(conn, user)
        wanted = [(name, args, emoji, always) for name, args, emoji, always in k["parts"] if name in tools]
        conn.execute("INSERT INTO questions (id, user_id, asked_at, text, state) VALUES (?, ?, ?, ?, 'calling')",
                     (qid, user["id"], engine._iso(now), k["question"]))
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
    lines, spoken, used = [], [], []
    for (name, _args, emoji, always), a in zip(wanted, answers):
        res = a.get("result") if a.get("state") == "ok" else None
        if not isinstance(res, dict):
            continue
        if not always and not res.get("items"):
            continue
        lines.append(f"{emoji} {_short(res.get('text'))}")
        spoken.append(_short(res.get("text")))
        used.append(tools[name]["appName"])
    if not lines:
        lines = spoken = [k["empty"]]
    day = config.now() + timedelta(days=k["day"])
    head = f"{day.strftime('%A')} {day.day} {day.strftime('%B')}"
    answer = f"**{head}**\n\n" + "\n\n".join(lines)
    with db.get_conn() as conn:
        conn.execute("UPDATE questions SET state = 'done', answer = ?, rounds = 1, seconds = ? WHERE id = ?",
                     (answer, round((config.utcnow() - now).total_seconds(), 2), qid))
        conn.execute("INSERT INTO usage_days (day, calls) VALUES (?, ?) ON CONFLICT(day) DO UPDATE SET "
                     "calls = calls + excluded.calls", (config.today().isoformat(), len(rows)))
    engine.touch(qid)
    return {"question": qid, "head": head, "lines": lines, "spoken": spoken, "sources": used}


_page = {"value": None, "known": False}


def _sidebar_page() -> str | None:
    if not _page["known"]:
        _page["value"] = assist_tools.sidebar_page(config.SLUG, token=config.SUPERVISOR_TOKEN,
                                                   supervisor_api=config.SUPERVISOR_API)
        _page["known"] = True
    return _page["value"]


def speak(speaker: str, text: str) -> bool:
    """Read `text` aloud on `speaker` with Home Assistant's first text-to-speech engine; True when it took it."""
    sp = speakers()
    tts = sp["tts"]
    if not any(p["entity"] == speaker for p in sp["speakers"]):
        logger.warning("%s isn't one of Home Assistant's speakers now: the briefing wasn't read aloud.", speaker)
        return False
    if not tts:
        logger.warning("No text-to-speech engine in Home Assistant: the briefing wasn't read aloud.")
        return False
    status, body = ha_client.request("POST", "/services/tts/speak",
                                     {"entity_id": tts, "media_player_entity_id": speaker, "message": text},
                                     timeout=15)
    if status != 200:
        logger.warning("Reading the briefing aloud on %s failed (HTTP %s): %s", speaker, status, body[:200])
    return status == 200


def sends_left(user_id: str) -> int:
    """How many more briefings the person may have this hour (SENDS_PER_HOUR; each one asks every app)."""
    with db.get_conn() as conn:
        n = engine.count_recent(conn, user_id, "briefing", config.utcnow() - timedelta(hours=1))
    return max(0, SENDS_PER_HOUR - n)


def reserve(user_id: str) -> bool:
    """Take one of the person's briefings for this hour before making it ("Send me one now"): checked and counted in
    one write transaction, so two at once can't both pass the limit. False when none is left."""
    now = config.utcnow()
    with db.get_conn() as conn:
        conn.execute("BEGIN IMMEDIATE")
        if engine.count_recent(conn, user_id, "briefing", now - timedelta(hours=1)) >= SENDS_PER_HOUR:
            return False
        engine.record_event(conn, user_id, "briefing", now)
    return True


def send(user: dict, kind: str = "morning") -> dict:
    """Make the person's briefing and send it to their phones (and speaker): {question, sent, phones, spoken}."""
    k = KINDS[kind]
    made = build(user, kind)
    message = "\n".join(made["lines"])
    if len(message) > MAX_MESSAGE:
        message = message[:MAX_MESSAGE - 1].rstrip() + "…"
    data = {}
    page = _sidebar_page()
    if page:
        data = {"url": page, "clickAction": page}
    targets = [p["service"] for p in phones(user["id"])]
    sent = ha_notify.send_to_services(targets, f"{k['title']} · {made['head']}", message, data or None) \
        if targets else {}
    with db.get_conn() as conn:
        speaker = get(conn, user["id"])["speaker"]
    said, quiet = None, False
    if speaker and quiet_now():
        quiet = True
        logger.info("Quiet hours: %s's briefing wasn't read aloud.", user.get("name"))
    elif speaker:
        word = "Good morning" if kind == "morning" else "Good evening"
        first = (str(user.get("name") or "").split() or ["there"])[0]
        said = speak(speaker, f"{word}, {first}. {k['question']} for {made['head']}. "
                     + " ".join(made["spoken"]))
    return {"question": made["question"], "sent": sent, "phones": targets, "spoken": said, "quiet": quiet}


# --------------------------------------------------------------------------------------------- the clock

def due_day(row, now: datetime, kind: str = "morning") -> str | None:
    """The day (YYYY-MM-DD) whose briefing of `kind` is due at `now` (Home Assistant's zone), or None. Its window is
    from its time until LATE after it, also past midnight: at 00:30, a 23:00 one is yesterday's, still due."""
    on, at_col, last_col = COLS[kind]
    at_text = row[at_col]
    if not row[on] or not TIME_RE.match(at_text or ""):
        return None
    h, m = (int(x) for x in at_text.split(":"))
    for back in (0, 1):
        day = now.date() - timedelta(days=back)
        at = datetime.combine(day, datetime.min.time(), now.tzinfo).replace(hour=h, minute=m)
        if not (at <= now < at + LATE):
            continue
        if row["days"] == "weekdays" and day.weekday() >= 5:
            return None
        if row[last_col] == day.isoformat():
            return None
        return day.isoformat()
    return None


def due(row, now: datetime, kind: str = "morning") -> bool:
    """Whether a person's briefing of `kind` is due at `now` (Home Assistant's zone)."""
    return due_day(row, now, kind) is not None


def check() -> int:
    """Every minute: send the briefings that are due. Returns how many were started."""
    now = config.now()
    started = 0
    todo = []
    with db.get_conn() as conn:
        rows = conn.execute("SELECT b.*, u.id AS uid, u.name, u.enabled, u.is_child FROM briefings b "
                            "JOIN users u ON u.id = b.user_id WHERE b.on_ = 1 OR b.evening_on = 1").fetchall()
        for r in rows:
            user = {"id": r["uid"], "name": r["name"], "enabled": bool(r["enabled"]), "is_child": bool(r["is_child"])}
            for kind in KINDS:
                day = due_day(r, now, kind)
                if day is None or may_have(user):
                    continue
                # marked first, so a slow or failing app never makes it send twice
                conn.execute(f"UPDATE briefings SET {COLS[kind][2]} = ? WHERE user_id = ?", (day, r["uid"]))
                engine.record_event(conn, r["uid"], "briefing")          # counts towards the hourly limit
                todo.append((user, kind))
    for user, kind in todo:
        with _lock:
            if (user["id"], kind) in _running:
                continue
            _running.add((user["id"], kind))
        threading.Thread(target=_send_logged, args=(user, kind), name=f"briefing-{user['id'][:8]}",
                         daemon=True).start()
        started += 1
    return started


def _send_logged(user: dict, kind: str = "morning") -> None:
    try:
        out = send(user, kind)
        ok = sum(1 for v in out["sent"].values() if v)
        logger.info("Sent %s's %s briefing to %s of %s phones%s.", user["name"], kind, ok, len(out["sent"]),
                    "" if out["spoken"] is None else (" and read it aloud" if out["spoken"] else
                                                      " (reading it aloud failed)"))
    except Exception:
        logger.exception("The %s briefing for %s failed", kind, user["name"])
    finally:
        with _lock:
            _running.discard((user["id"], kind))
