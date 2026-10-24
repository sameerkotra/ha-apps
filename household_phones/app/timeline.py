"""Each phone's online line (spec/SPEC.md §5.1): the status machine's changes kept as a small log, drawn as a strip
like Household Proxy's Monitor strip — green online, amber late / can't check, red offline / app not reporting, grey
paused, do-not-monitor hours or not monitored, faint where there is no data (before it was monitored, or while the app
wasn't running).

- **Recording** (`record`, from monitor.tick under its connection): one `status_log` row per change of a phone's
  status, only for monitored phones (and the one "not monitored" row when monitoring stops). While watching is paused
  or in do-not-monitor hours the row says only that — the line never shows more than the status did then. When the
  app was stopped for a while, a "none" row marks the gap (from the last heartbeat, `app_state` `timeline_alive`).
- **Kept** the App setting's days (`online_history_days`, at most 30: the longest range shown); the last change
  before the cut stays, so the line knows the status at its start.
- **Drawn** (`line`): 24 hours in half hours, 7 days in 2 hours, 30 days in 12 hours, in Home Assistant's time zone.
  A part's colour is the worst status that lasted at least 2 % of it (short blips are in its tooltip only); its
  tooltip says what each status lasted. **Online %** (`uptime`): Online and Late count as online, Offline / App not
  reporting / Can't check as not; pauses, do-not-monitor hours and gaps are left out.
"""
import math
import threading

from . import config, db, monitor, settings

RANGES = {"24h": (86400, 48), "7d": (7 * 86400, 84), "30d": (30 * 86400, 60)}
UPTIMES = (("day", 86400), ("week", 7 * 86400), ("month", 30 * 86400))
GAP_S = 10 * 60                  # the app stopped for longer than this: the line shows a gap
ALIVE_EVERY_S = 5 * 60           # the heartbeat's resolution
BLIP = 0.02                      # share of a part a status must fill to colour it
CLASS = {"online": "up", "late": "late", "cant_check": "late", "offline": "down", "app_not_reporting": "down",
         "paused": "off", "dnm": "off", "unknown": "off", "none": "none"}
ORDER = ("down", "late", "up", "off")
UP = ("online", "late")
DOWN = ("offline", "app_not_reporting", "cant_check")
LABEL = {"online": "Online", "late": "Late", "cant_check": "Can't check", "offline": "Offline",
         "app_not_reporting": "App not reporting", "paused": "Paused", "dnm": "Do-not-monitor hours",
         "unknown": "Not monitored", "none": "No data"}
SEVERITY = ("offline", "app_not_reporting", "cant_check", "late", "online", "paused", "dnm", "unknown", "none")

_lock = threading.Lock()
_cache: dict = {"gen": None, "last": {}, "started": False, "alive": None}


def _load(conn) -> dict:
    """phone id -> its last logged status (loaded once, again after a restore)."""
    if _cache["gen"] != db.generation:
        _cache["last"] = {r["phone_id"]: r["status"] for r in conn.execute(
            "SELECT phone_id, status FROM status_log WHERE id IN (SELECT MAX(id) FROM status_log GROUP BY phone_id)")}
        _cache["gen"] = db.generation
    return _cache["last"]


def record(conn, rows, statuses: dict, now: float) -> int:
    """Log each monitored phone's status when it differs from the last one logged; the rows written."""
    n = 0
    with _lock:
        last = _load(conn)
        if not _cache["started"]:
            _cache["started"] = True
            alive = db.state_get(conn, "timeline_alive")
            if alive is not None and now - alive > GAP_S:
                for pid, st in list(last.items()):
                    if st not in ("unknown", "none"):
                        conn.execute("INSERT INTO status_log (phone_id, at, status) VALUES (?, ?, 'none')", (pid, alive))
                        last[pid] = "none"
                        n += 1
        for p in rows:
            pid = p["id"]
            st = statuses[pid][0] if monitor.runs(p) else "unknown"
            prev = last.get(pid)
            if st == prev or (prev is None and not monitor.runs(p)):
                continue
            conn.execute("INSERT INTO status_log (phone_id, at, status) VALUES (?, ?, ?)", (pid, now, st))
            last[pid] = st
            n += 1
        if _cache["alive"] is None or now - _cache["alive"] >= ALIVE_EVERY_S or now < _cache["alive"]:
            db.state_set(conn, "timeline_alive", now)
            _cache["alive"] = now
    return n


def forget() -> None:
    """A phone's log was deleted or moved (Stop monitoring without history, Replaces): read it again next round."""
    with _lock:
        _cache["gen"] = None


def reset() -> None:
    """Tests."""
    with _lock:
        _cache.update(gen=None, last={}, started=False, alive=None)


def prune(conn, now: float, days: int) -> int:
    cut = now - days * 86400
    return conn.execute("DELETE FROM status_log WHERE at < ? AND id NOT IN "
                        "(SELECT MAX(id) FROM status_log WHERE at < ? GROUP BY phone_id)", (cut, cut)).rowcount


# --------------------------------------------------------------------------------------------- drawing

def intervals(conn, pid: str, start: float, end: float) -> list[tuple[float, float, str]]:
    """(from, to, status) covering start..end; "none" before the first row."""
    before = conn.execute("SELECT status FROM status_log WHERE phone_id = ? AND at <= ? ORDER BY at DESC, id DESC "
                          "LIMIT 1", (pid, start)).fetchone()
    cur, at = (before["status"] if before else "none"), start
    out = []
    for r in conn.execute("SELECT at, status FROM status_log WHERE phone_id = ? AND at > ? AND at < ? ORDER BY at, id",
                          (pid, start, end)):
        if r["at"] > at:
            out.append((at, r["at"], cur))
        cur, at = r["status"], r["at"]
    if end > at:
        out.append((at, end, cur))
    return out


def uptime(conn, pid: str, now: float, seconds: int) -> float | None:
    up = down = 0.0
    for a, b, st in intervals(conn, pid, now - seconds, now):
        if st in UP:
            up += b - a
        elif st in DOWN:
            down += b - a
    return round(100 * up / (up + down), 1) if up + down else None


def _boundary(now: float, size: int) -> float:
    """The end of the part holding `now` (now itself on a boundary): parts start at Home Assistant's local midnight."""
    d = config.local(now)
    midnight = d.replace(hour=0, minute=0, second=0, microsecond=0).timestamp()
    return midnight + math.ceil((now - midnight) / size) * size


def _when(ts: float, size: int, first: bool) -> str:
    d = config.local(ts)
    if not first:                                # a part's end: "Fri 06:00–08:00"
        return d.strftime("%H:%M")
    if size < 12 * 3600:
        return d.strftime("%a %H:%M")
    return d.strftime("%a %d %b %H:%M")


def buckets(conn, pid: str, now: float, key: str = "24h") -> list[dict]:
    """The strip: [{c: up / late / down / off / none, t: "Mon 14:00–14:30 · Online 25 min, Late 5 min"}]."""
    seconds, n = RANGES[key]
    size = seconds // n
    end = _boundary(now, size)
    start = end - n * size
    spans = intervals(conn, pid, start, min(now, end))
    out = []
    i = 0
    for k in range(n):
        a, b = start + k * size, start + (k + 1) * size
        secs: dict = {}
        while i < len(spans) and spans[i][1] <= a:
            i += 1
        j = i
        while j < len(spans) and spans[j][0] < b:
            lo, hi = max(a, spans[j][0]), min(b, spans[j][1])
            if hi > lo:
                secs[spans[j][2]] = secs.get(spans[j][2], 0) + hi - lo
            j += 1
        by_class: dict = {}
        for st, s in secs.items():
            by_class[CLASS.get(st, "none")] = by_class.get(CLASS.get(st, "none"), 0) + s
        c = next((x for x in ORDER if by_class.get(x, 0) >= max(1.0, BLIP * size)), None)
        if c is None:
            c = max((x for x in ORDER if by_class.get(x)), key=lambda x: by_class[x], default="none")
        future = b > now
        span = f"{_when(a, size, True)}–{'now' if future else _when(b, size, False)}"
        if a >= now:
            out.append({"c": "future", "t": f"{span}: later"})
            continue
        if len(secs) == 1:                     # one status all along: just its name
            parts = [LABEL.get(st, st) for st in secs]
        else:
            parts = [f"{LABEL.get(st, st)} {monitor.duration_text(secs[st], short=True)}"
                     for st in sorted(secs, key=lambda x: SEVERITY.index(x) if x in SEVERITY else 99) if secs[st] >= 30]
        out.append({"c": c, "t": f"{span} · " + (", ".join(parts) if parts else LABEL["none"])})
    return out


def line(conn, pid: str, now: float, key: str = "24h") -> dict:
    """The phone page's Online history: the strip for the range, its ends, and Online % over 24 h, 7 and 30 days."""
    seconds, n = RANGES[key]
    size = seconds // n
    end = _boundary(now, size)
    start = end - n * size
    return {"range": key, "hidden": False, "buckets": buckets(conn, pid, now, key),
            "startText": _when(start, size, True),
            "endText": "now", "uptime": {k: uptime(conn, pid, now, s) for k, s in UPTIMES},
            "since": _first(conn, pid), "keepDays": settings.get("online_history_days")}


def _first(conn, pid: str) -> str | None:
    r = conn.execute("SELECT MIN(at) FROM status_log WHERE phone_id = ?", (pid,)).fetchone()[0]
    return config.local(r).strftime("%a %d %b %H:%M") if r else None


def strip(conn, pid: str, now: float) -> list[dict]:
    """The compact 24-hour strip for All phones."""
    return buckets(conn, pid, now, "24h")


__all__ = ["record", "line", "strip", "uptime", "prune"]
