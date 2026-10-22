"""Storage, battery health and the Home Assistant app's version (spec/SPEC.md §15.5, §15.6).

- **Storage almost full:** the phone's Storage sensor (Internal storage; its state is the percentage of free space)
  below the App setting (10%): one alert, again only after it has recovered by 2 points.
- **Battery health:** the Battery health sensor worse than "good" (overheated, cold, dead, over_voltage, failed):
  one alert per change.
- **HA app outdated:** each phone's Companion app version (its Current version / App version sensor, else what the
  registration said) against the newest one seen on the household's monitored phones with the same system (Android
  and iOS number their versions differently), and against the admin's optional minimum version.

Alerts follow the usual rules (alerts.send): on/off, recipients and cooldown in App settings, never during
do-not-monitor hours, not while alerts or watching are paused.
"""
import re

from . import alerts, config, db, monitor, phones, settings

STORAGE_KEYS = ("internal_storage", "storage_sensor", "storage")
HEALTH_KEYS = ("battery_health",)
VERSION_KEYS = ("current_version", "app_version")
GOOD = ("good",)
UNKNOWN = ("unknown", "unavailable", "", None)
HEALTH_TEXT = {"overheat": "overheated", "overheated": "overheated", "cold": "too cold", "dead": "dead",
               "over_voltage": "over voltage", "failed": "failed", "unspecified_failure": "failed"}


def storage_free(pid: str) -> float | None:
    v = phones.value_any(pid, STORAGE_KEYS)
    try:
        return float(str(v).rstrip("%")) if v is not None else None
    except ValueError:
        return None


def battery_health(pid: str) -> str | None:
    v = phones.value_any(pid, HEALTH_KEYS)
    return str(v).strip().lower() if v not in UNKNOWN else None


def health_text(h: str | None) -> str | None:
    if h is None:
        return None
    return "Good" if h in GOOD else HEALTH_TEXT.get(h, h.replace("_", " "))


# --------------------------------------------------------------------------------------------- versions

def parse_version(v) -> tuple | None:
    """"2026.10.1-full" → (2026, 10, 1); "2024.10 (2024.1064)" → (2024, 10); None when it isn't a version."""
    m = re.match(r"^\s*v?(\d+(?:\.\d+)*)", str(v or ""))
    if not m:
        return None
    return tuple(int(x) for x in m.group(1).split("."))


def _cmp_key(t: tuple) -> tuple:
    return tuple(t) + (0,) * (4 - len(t)) if len(t) < 4 else tuple(t)


def older(a: tuple, b: tuple) -> bool:
    return _cmp_key(a) < _cmp_key(b)


def app_version(p) -> str | None:
    """The Companion app's version: its sensor, else the registration's."""
    v = phones.value_any(p["id"], VERSION_KEYS)
    return str(v)[:40] if v else (p["app_version"] or None)


def system(p) -> str:
    return "ios" if phones.is_ios(p) else "android"


def newest(conn) -> dict:
    """{system: (version tuple, text)} over the monitored phones."""
    out: dict = {}
    for p in conn.execute("SELECT * FROM phones WHERE retired = 0 AND monitored = 1").fetchall():
        text = app_version(p)
        t = parse_version(text)
        if t is None:
            continue
        cur = out.get(system(p))
        if cur is None or older(cur[0], t):
            out[system(p)] = (t, text)
    return out


def version_view(p, best: dict) -> dict:
    """{version, outdated, why} for one phone, given newest()."""
    text = app_version(p)
    t = parse_version(text)
    out = {"version": text, "outdated": False, "why": None, "newest": None}
    if t is None:
        return out
    top = best.get(system(p))
    minimum = parse_version(settings.get("min_app_version")) if settings.get("min_app_version") else None
    if minimum and not phones.is_ios(p) and older(t, minimum):
        out.update(outdated=True, why=f"older than the household's minimum {settings.get('min_app_version')}")
    elif top and older(t, top[0]):
        out.update(outdated=True, why=f"another phone has {top[1]}", newest=top[1])
    return out


# --------------------------------------------------------------------------------------------- the round

def observe(conn, p, now: float) -> list:
    """Storage and battery-health alerts due for one phone: [(alert, phone id, text)]."""
    out = []
    pid = p["id"]
    name = phones.display_name(conn, p)
    muted = monitor.alerts_paused(p, now) or monitor.watching_paused(p, now)
    free = storage_free(pid)
    if free is not None:
        pct = settings.get("storage_low_pct")
        if free < pct and not p["storage_low_sent"]:
            conn.execute("UPDATE phones SET storage_low_sent = 1 WHERE id = ?", (pid,))
            if alerts.enabled("storage") and not muted:
                out.append(("storage", pid, f"{name} is almost out of storage: {free:g}% free."))
        elif free >= pct + 2 and p["storage_low_sent"]:
            conn.execute("UPDATE phones SET storage_low_sent = 0 WHERE id = ?", (pid,))
    h = battery_health(pid)
    if h is not None and h != p["battery_health"]:
        conn.execute("UPDATE phones SET battery_health = ? WHERE id = ?", (h, pid))
        if h not in GOOD:
            phones.history(conn, pid, "health", f"Battery health: {health_text(h)}.", now=now)
            if alerts.enabled("battery_health") and not muted:
                out.append(("battery_health", pid, f"{name}'s battery health is {health_text(h)} (it was "
                                                   f"{health_text(p['battery_health']) or 'not reported'})."))
    return out


def tick(now: float | None = None) -> int:
    """Every round: storage and battery health of the monitored phones (not during do-not-monitor hours: alerts.send
    drops those, and nothing is remembered as sent)."""
    from . import dnm
    now = config.clock() if now is None else now
    todo = []
    with db.get_conn() as conn:
        for p in conn.execute("SELECT * FROM phones WHERE retired = 0 AND monitored = 1").fetchall():
            if dnm.active(p, now):
                continue
            todo += observe(conn, p, now)
    for alert, pid, text in todo:
        alerts.send(alert, pid, text, now=now)
    return len(todo)


def view(p, best: dict) -> dict:
    """The phone page's Health card."""
    h = battery_health(p["id"])
    free = storage_free(p["id"])
    return {"storageFree": free, "storageLow": free is not None and free < settings.get("storage_low_pct"),
            "batteryHealth": health_text(h), "batteryHealthBad": h is not None and h not in GOOD,
            "app": version_view(p, best)}
