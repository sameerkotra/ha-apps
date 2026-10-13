"""Tithi anniversaries — shraddha (§13.12) — and janma tithi birthdays.

A death (or birth) event can carry a tithi: lunar month, paksha and tithi,
entered directly (common for older generations) or worked out from the date.
Each year the calendar date is computed with panchang.py for Home Assistant's
home location and time zone, following the `tithi_rule` App setting, and
kept in memory (never written while a page is read); a hand correction for a
year ("our priest says the 15th") is kept in `tithi_dates` and never recomputed.
"""
import hashlib
import threading
from datetime import date, timedelta

from . import config, features, graph as graph_mod, panchang, settings


_memo_lock = threading.Lock()
_memo: dict = {}
MEMO_MAX = 20000


def calc_signature(conn=None) -> str:
    """What every computed date depends on besides the tithi itself: the rule, the place and the time zone."""
    lat, lon = config.location()
    return hashlib.sha1(f"{lat:.3f},{lon:.3f},{config.timezone_name()},{settings.get('tithi_rule')}".encode()).hexdigest()[:16]


def compute_date(t: dict, year: int, sig: str | None = None) -> date | None:
    """The calendar date of a tithi in a year, worked out once per rule, place and time zone and kept in
    memory — reading a page never writes to the database."""
    key = (sig or calc_signature(), t["masa"], t["paksha"], t["tithi"], year)
    with _memo_lock:
        if key in _memo:
            return _memo[key]
    lat, lon = config.location()
    d = panchang.date_for(year, t["masa"], t["paksha"], t["tithi"], lat, lon, config.tz(), settings.get("tithi_rule"))
    with _memo_lock:
        if len(_memo) >= MEMO_MAX:
            _memo.clear()
        _memo[key] = d
    return d


def date_in(conn, event_id: str, t: dict, year: int, sig: str | None = None) -> tuple[date | None, bool]:
    """(date, overridden) for an event's tithi in a Gregorian year: a hand correction (tithi_dates) wins."""
    row = conn.execute("SELECT date FROM tithi_dates WHERE event_id = ? AND year = ? AND overridden_by IS NOT NULL",
                       (event_id, year)).fetchone()
    if row:
        return date.fromisoformat(row["date"]), True
    return compute_date(t, year, sig), False


def label(t: dict, lang: str = "en") -> str:
    return panchang.name(t["masa"], t["paksha"], t["tithi"], lang if lang in ("te", "hi") else "en")


def all_tithis(conn) -> list:
    return [dict(r) for r in conn.execute(
        "SELECT t.*, e.type, e.person_id FROM event_tithi t JOIN events e ON e.id = t.event_id "
        "WHERE e.person_id IS NOT NULL AND e.type IN ('death','birth')")]


def entries(conn, g, today: date, days: int, lang: str = "en", me: str | None = None, label_fn=None) -> list:
    """Upcoming tithi days (death: 🪔 shraddha; birth: janma tithi of the living) from today to today + days."""
    sig = calc_signature()
    horizon = today + timedelta(days=days)
    out = []
    for t in all_tithis(conn):
        p = g.people.get(t["person_id"])
        if not p:
            continue
        alive = graph_mod.living(p)
        if t["type"] == "birth" and not alive:
            continue
        for year in sorted({today.year, horizon.year}):
            d, overridden = date_in(conn, t["event_id"], t, year, sig)
            if not d or not today <= d <= horizon:
                continue
            name = label(t, lang)
            rel = label_fn(p.id) if label_fn and me and p.id != me else None
            who = {"id": p.id, "name": p.name, "given": p.given, "surname": p.surname, "photo": p.photo,
                   "photoRegion": p.photo_region, "gender": p.gender, "relationship": rel, "remind": p.remind,
                   "nameLocal": p.local_name if features.on("script_names") else None}
            title = (f"Tithi for {p.name} — {name}" if t["type"] == "death" else f"Janma tithi of {p.name} — {name}")
            out.append({"date": d.isoformat(), "daysAway": (d - today).days, "kind": "tithi",
                        "sub": t["type"], "title": title, "tithiName": name, "years": None, "people": [who],
                        "remind": p.remind, "eventId": t["event_id"], "overridden": overridden})
    return out
