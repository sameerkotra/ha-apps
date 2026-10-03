"""Children and limits (SPEC §6).

An admin marks a person as a child on Admin → Users and sets that child's
limits there. Limits never apply to anyone who isn't marked, and never to an
admin (auth.get_current_user clears `is_child` for admins).

- **Day type.** A date is a *school day* if its weekday is one of the
  App settings' school days and it isn't one of the household's holidays;
  every other date (weekends, holidays) is a *weekend* day.
- **Daily play time.** Minutes for school days and for weekend days (NULL =
  no limit), plus any extra time an admin gave for today. Time counts only
  while a game runs: the browser reports the active seconds of each play
  session, and the server caps them by the wall time since the session began.
  A session counts on the day it started.
- **Quiet hours.** From–to times for school nights and for weekends. A window
  whose end is before its start runs past midnight. Which set applies is
  decided by the day the window ENDS on: the window that ends on a school-day
  morning (Sunday night → Monday) uses the school-night times; the one ending
  on a weekend day (Friday night → Saturday) uses the weekend times. A window
  within one day uses that day's type. Quiet hours block starting a game; a
  game already running finishes.
- **Allowed games** and **leaderboard visibility** are per child.

All in Home Assistant's time zone (config.now()).
"""
import json
import re
from datetime import date, datetime, time, timedelta

from . import config, games, settings

TIME_RE = re.compile(r"^([01][0-9]|2[0-3]):[0-5][0-9]$")
WARN_SECONDS = 5 * 60
LEADERBOARD_CHOICES = ("full", "first", "hidden")
EXTRA_CHOICES = (15, 30, 60)
MAX_DAILY_MINUTES = 24 * 60

EMPTY_LIMITS = {
    "minutesSchool": None, "minutesWeekend": None,
    "quietSchoolFrom": None, "quietSchoolTo": None,
    "quietWeekendFrom": None, "quietWeekendTo": None,
    "allowedGames": None, "leaderboard": "full",
}


def holidays(conn) -> set[str]:
    return {r["date"] for r in conn.execute("SELECT date FROM holidays")}


def is_school_day(d: date, school_days: list[int], holiday_dates: set[str]) -> bool:
    return d.isoweekday() in school_days and d.isoformat() not in holiday_dates


def load_limits(conn, user_id: str) -> dict:
    row = conn.execute("SELECT * FROM child_limits WHERE user_id = ?", (user_id,)).fetchone()
    if not row:
        return dict(EMPTY_LIMITS)
    allowed = None
    if row["allowed_games"] is not None:
        try:
            allowed = [g for g in json.loads(row["allowed_games"]) if isinstance(g, str)]
        except ValueError:
            allowed = None
    return {
        "minutesSchool": row["minutes_school"], "minutesWeekend": row["minutes_weekend"],
        "quietSchoolFrom": row["quiet_school_from"], "quietSchoolTo": row["quiet_school_to"],
        "quietWeekendFrom": row["quiet_weekend_from"], "quietWeekendTo": row["quiet_weekend_to"],
        "allowedGames": allowed, "leaderboard": row["leaderboard"],
    }


def _hm(s: str) -> time:
    h, m = s.split(":")
    return time(int(h), int(m))


def quiet_window(limits: dict, now: datetime, school_days: list[int], holiday_dates: set[str]):
    """(start, end) of the quiet window `now` falls in, or None."""
    tz = now.tzinfo
    for kind in ("School", "Weekend"):
        f, t = limits.get(f"quiet{kind}From"), limits.get(f"quiet{kind}To")
        if not f or not t or f == t:
            continue
        ft, tt = _hm(f), _hm(t)
        crosses = tt <= ft
        for back in (0, 1):
            d = now.date() - timedelta(days=back)
            start = datetime.combine(d, ft, tzinfo=tz)
            end_day = d + timedelta(days=1) if crosses else d
            end = datetime.combine(end_day, tt, tzinfo=tz)
            school = is_school_day(end_day, school_days, holiday_dates)
            if (kind == "School") != school:
                continue
            if start <= now < end:
                return start, end
    return None


def used_seconds(conn, user_id: str, d: date) -> float:
    start, end = config.local_day_bounds_utc(d)
    row = conn.execute(
        "SELECT COALESCE(SUM(active_seconds), 0) AS s FROM play_sessions "
        "WHERE user_id = ? AND started_at >= ? AND started_at < ?", (user_id, start, end)).fetchone()
    return float(row["s"] or 0)


def extra_minutes(conn, user_id: str, d: date) -> int:
    row = conn.execute("SELECT COALESCE(SUM(minutes), 0) AS m FROM extra_time WHERE user_id = ? AND date = ?",
                       (user_id, d.isoformat())).fetchone()
    return int(row["m"] or 0)


def game_allowed(limits: dict, game: str) -> bool:
    allowed = limits.get("allowedGames")
    return allowed is None or game in allowed


def status(conn, user: dict) -> dict:
    """Today's play-time picture for `user` (a dict with id and is_child).
    `leftSeconds` is None when there is no daily limit."""
    now = config.now()
    today = now.date()
    used = used_seconds(conn, user["id"], today)
    if not user.get("is_child"):
        return {"isChild": False, "usedSeconds": round(used), "leftSeconds": None, "limitMinutes": None,
                "extraMinutes": 0, "dayType": None, "quiet": False, "quietUntil": None, "canStart": True,
                "reason": None, "limits": None}
    school_days = settings.get("school_days")
    hol = holidays(conn)
    limits = load_limits(conn, user["id"])
    school = is_school_day(today, school_days, hol)
    limit = limits["minutesSchool"] if school else limits["minutesWeekend"]
    extra = extra_minutes(conn, user["id"], today)
    left = None
    if limit is not None:
        left = max(0, round((limit + extra) * 60 - used))
    win = quiet_window(limits, now, school_days, hol)
    reason = None
    if win:
        reason = f"Time for a break — games can be played again at {win[1].strftime('%H:%M')}."
    elif left is not None and left <= 0:
        reason = "No play time left today. See you tomorrow!"
    return {
        "isChild": True, "usedSeconds": round(used), "leftSeconds": left, "limitMinutes": limit,
        "extraMinutes": extra, "dayType": "school" if school else "weekend",
        "quiet": bool(win), "quietUntil": win[1].strftime("%H:%M") if win else None,
        "canStart": reason is None, "reason": reason, "limits": limits,
    }


def clean_limits(body: dict) -> dict:
    """Validate an Admin → Users limits form. Raises ValueError (readable)."""
    if not isinstance(body, dict):
        raise ValueError("Send the limits as an object.")
    unknown = set(body) - set(EMPTY_LIMITS)
    if unknown:
        raise ValueError("Unknown field(s): " + ", ".join(sorted(unknown)))
    out = dict(EMPTY_LIMITS)
    for key in ("minutesSchool", "minutesWeekend"):
        v = body.get(key)
        if v is None:
            continue
        if isinstance(v, bool) or not isinstance(v, int) or v < 0 or v > MAX_DAILY_MINUTES:
            raise ValueError("Daily minutes must be a whole number from 0 to 1440, or empty for no limit.")
        out[key] = v
    for kind in ("School", "Weekend"):
        f, t = body.get(f"quiet{kind}From"), body.get(f"quiet{kind}To")
        if f in (None, "") and t in (None, ""):
            continue
        if not isinstance(f, str) or not isinstance(t, str) or not TIME_RE.match(f) or not TIME_RE.match(t):
            raise ValueError("Quiet hours need both a from and a to time (HH:MM), or neither.")
        if f == t:
            raise ValueError("Quiet hours can't start and end at the same time.")
        out[f"quiet{kind}From"], out[f"quiet{kind}To"] = f, t
    allowed = body.get("allowedGames")
    if allowed is not None:
        if not isinstance(allowed, list) or any(not isinstance(g, str) or not games.exists(g) for g in allowed):
            raise ValueError("Allowed games: unknown game.")
        out["allowedGames"] = [g for g in games.GAME_IDS if g in allowed]
    lb = body.get("leaderboard", "full")
    if lb not in LEADERBOARD_CHOICES:
        raise ValueError("Leaderboard must be full, first or hidden.")
    out["leaderboard"] = lb
    return out


def save_limits(conn, user_id: str, limits: dict, who: str | None) -> None:
    conn.execute(
        "INSERT INTO child_limits (user_id, minutes_school, minutes_weekend, quiet_school_from, quiet_school_to, "
        "quiet_weekend_from, quiet_weekend_to, allowed_games, leaderboard, updated_at, updated_by) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) ON CONFLICT(user_id) DO UPDATE SET "
        "minutes_school = excluded.minutes_school, minutes_weekend = excluded.minutes_weekend, "
        "quiet_school_from = excluded.quiet_school_from, quiet_school_to = excluded.quiet_school_to, "
        "quiet_weekend_from = excluded.quiet_weekend_from, quiet_weekend_to = excluded.quiet_weekend_to, "
        "allowed_games = excluded.allowed_games, leaderboard = excluded.leaderboard, "
        "updated_at = excluded.updated_at, updated_by = excluded.updated_by",
        (user_id, limits["minutesSchool"], limits["minutesWeekend"], limits["quietSchoolFrom"],
         limits["quietSchoolTo"], limits["quietWeekendFrom"], limits["quietWeekendTo"],
         None if limits["allowedGames"] is None else json.dumps(limits["allowedGames"]),
         limits["leaderboard"], config.now_iso(), who))
