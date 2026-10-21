"""Do-not-monitor hours (spec/SPEC.md §3.1, §9): windows the phone's owner sets — days of the week plus a start and an
end, crossing midnight allowed (Mon–Fri 22:00–07:00 covers Monday 22:00 to Tuesday 07:00) — in Home Assistant's time
zone. While one is on: no "please report", no alerts (none held or queued), no history rows for status changes, and
admins see only "Not monitored (until 07:00)". Forwarding goes on unless the window says "also pause forwarding".

Everything here works on minutes of the week (Monday 00:00 = 0) and is pure apart from `active` / `since_end`, which
read the App setting and Home Assistant's clock. A window's day is the day it starts.
"""
import json
import re
from datetime import timedelta

from . import config, settings

WEEK = 7 * 1440
DAY_NAMES = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")
HHMM = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")
MAX_WINDOWS = 7


class Invalid(ValueError):
    pass


def _mins(hhmm: str) -> int:
    h, m = hhmm.split(":")
    return int(h) * 60 + int(m)


def clean(windows) -> list[dict]:
    """The owner's windows as sent by the page, checked: [{days, start, end, pauseForwarding}]. Raises Invalid."""
    if not isinstance(windows, list):
        raise Invalid("Send a list of windows.")
    if len(windows) > MAX_WINDOWS:
        raise Invalid(f"At most {MAX_WINDOWS} windows.")
    out = []
    for w in windows:
        if not isinstance(w, dict) or set(w) - {"days", "start", "end", "pauseForwarding"}:
            raise Invalid("Each window has days, start, end and pauseForwarding.")
        days = w.get("days")
        if not isinstance(days, list) or not days or not all(isinstance(d, int) and not isinstance(d, bool)
                                                              and 0 <= d <= 6 for d in days):
            raise Invalid("Choose at least one day for each window.")
        start, end = w.get("start"), w.get("end")
        if not (isinstance(start, str) and HHMM.match(start) and isinstance(end, str) and HHMM.match(end)):
            raise Invalid("Use times like 22:00 (24-hour).")
        if start == end:
            raise Invalid("A window's start and end can't be the same time.")
        pf = w.get("pauseForwarding", False)
        if not isinstance(pf, bool):
            raise Invalid("pauseForwarding is true or false.")
        out.append({"days": sorted(set(days)), "start": start, "end": end, "pauseForwarding": pf})
    return out


def parse(text) -> list[dict]:
    """A phone's stored windows (bad or missing JSON: none)."""
    try:
        v = json.loads(text) if text else []
        return clean(v)
    except (ValueError, TypeError):
        return []


def windows_of(p) -> list[dict]:
    return parse(p["dnm_windows"])


def _intervals(windows: list[dict]):
    """(start minute of the week, length in minutes, window) for each day of each window."""
    for w in windows:
        a = _mins(w["start"])
        length = (_mins(w["end"]) - a) % 1440
        for d in w["days"]:
            yield d * 1440 + a, length, w


def covered_minutes(windows: list[dict]) -> list[int]:
    """Minutes not monitored on each calendar day, Monday first (overlaps counted once)."""
    week = bytearray(WEEK)
    for s, length, _ in _intervals(windows):
        for m in range(s, s + length):
            week[m % WEEK] = 1
    return [sum(week[d * 1440:(d + 1) * 1440]) for d in range(7)]


def check_limit(windows: list[dict], max_hours: int) -> None:
    worst = max(covered_minutes(windows) or [0])
    if worst > max_hours * 60:
        day = DAY_NAMES[covered_minutes(windows).index(worst)]
        raise Invalid(f"At most {max_hours} hours a day without monitoring (this household's limit); "
                      f"{day} would have {worst / 60:g}.")


def _containing(windows: list[dict], m: int):
    return [(s, length, w) for s, length, w in _intervals(windows) if (m - s) % WEEK < length]


def state_at(windows: list[dict], m: int) -> dict | None:
    """At minute-of-week `m`: None, or {"minutes": until the windows end (back-to-back windows joined),
    "pauseForwarding"}."""
    hits = _containing(windows, m)
    if not hits:
        return None
    pause = any(w["pauseForwarding"] for _, _, w in hits)
    total, cur = 0, m
    for _ in range(50):
        hits = _containing(windows, cur)
        if not hits or total >= WEEK:
            break
        step = max(length - (cur - s) % WEEK for s, length, _ in hits)
        total += step
        cur = (cur + step) % WEEK
    return {"minutes": total, "pauseForwarding": pause}


def minutes_since_end(windows: list[dict], m: int) -> int | None:
    """Minutes since the last window ended (a window followed at once by another doesn't count as ending), or None
    when there are no windows or one is on now."""
    if not windows or _containing(windows, m):
        return None
    best = None
    for s, length, _ in _intervals(windows):
        e = (s + length) % WEEK
        if _containing(windows, e):
            continue                          # joined to the next window
        ago = (m - e) % WEEK
        best = ago if best is None else min(best, ago)
    return best


def _minute_of_week(now: float):
    d = config.local(now)
    return d, d.weekday() * 1440 + d.hour * 60 + d.minute


def applies() -> bool:
    return bool(settings.get("dnm_allowed"))


def active(p, now: float) -> dict | None:
    """Is a do-not-monitor window on for this phone now? None, or {"until": epoch, "untilText": "07:00" or
    "Sat 09:00", "pauseForwarding"}."""
    if not p["dnm_windows"] or not applies():
        return None
    windows = windows_of(p)
    if not windows:
        return None
    d, m = _minute_of_week(now)
    st = state_at(windows, m)
    if st is None:
        return None
    end = d.replace(second=0, microsecond=0) + timedelta(minutes=st["minutes"])
    until = end.timestamp()
    return {"until": until, "untilText": until_text(until), "pauseForwarding": st["pauseForwarding"]}


def since_end(p, now: float) -> float | None:
    """Seconds since this phone's last do-not-monitor window ended (None: none, or one is on)."""
    if not p["dnm_windows"] or not applies():
        return None
    windows = windows_of(p)
    d, m = _minute_of_week(now)
    ago = minutes_since_end(windows, m)
    if ago is None:
        return None
    return ago * 60 + d.second + d.microsecond / 1e6


def until_text(ts: float) -> str:
    d = config.local(ts)
    return d.strftime("%H:%M") if d.date() == config.local(config.clock()).date() else d.strftime("%a %H:%M")


def days_text(days: list[int]) -> str:
    days = sorted(set(days))
    if days == [0, 1, 2, 3, 4, 5, 6]:
        return "every day"
    if days == [0, 1, 2, 3, 4]:
        return "weekdays"
    if days == [5, 6]:
        return "weekends"
    runs, cur = [], [days[0]]
    for x in days[1:]:
        if x == cur[-1] + 1:
            cur.append(x)
        else:
            runs.append(cur)
            cur = [x]
    runs.append(cur)
    return ", ".join(f"{DAY_NAMES[r[0]]}–{DAY_NAMES[r[-1]]}" if len(r) >= 3 else ", ".join(DAY_NAMES[x] for x in r)
                     for r in runs)


def summary(windows: list[dict]) -> str | None:
    """"Not monitored weekdays 22:00–07:00 · weekends 23:00–09:00" (None: no windows)."""
    if not windows:
        return None
    return "Not monitored " + " · ".join(f"{days_text(w['days'])} {w['start']}–{w['end']}" for w in windows)


def view(p) -> dict:
    """What the pages show about a phone's windows (admins see the summary only)."""
    windows = windows_of(p)
    return {"windows": windows, "text": summary(windows) if applies() else None, "allowed": applies(),
            "maxHours": settings.get("dnm_max_hours")}
