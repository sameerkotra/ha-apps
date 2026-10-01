"""Tithi anniversaries — shraddha (§13.12) — and janma tithi birthdays.

A death (or birth) event can carry a tithi: lunar month, paksha and tithi,
entered directly (common for older generations) or worked out from the date.
Each year the calendar date is computed with panchang.py for Home Assistant's
home location and time zone, following the `tithi_rule` App setting, and
cached in `tithi_dates`; a hand correction for a year ("our priest says the
15th") is kept and never recomputed.
"""
import hashlib
from datetime import date, timedelta

from . import config, db, graph as graph_mod, panchang, settings


def calc_signature(conn=None) -> str:
    """Rule, place, time zone and every kept tithi (so an undo of a change can't leave stale dates)."""
    lat, lon = config.location()
    kept = ""
    if conn is not None:
        kept = ";".join(f"{r[0]}:{r[1]}{r[2]}{r[3]}" for r in conn.execute(
            "SELECT event_id, masa, paksha, tithi FROM event_tithi ORDER BY event_id"))
    return hashlib.sha1(f"{lat:.3f},{lon:.3f},{config.timezone_name()},{settings.get('tithi_rule')},{kept}".encode()).hexdigest()[:16]


def _refresh_cache(conn) -> None:
    """Computed dates depend on the rule, the place and the time zone: drop them when those change."""
    sig = calc_signature(conn)
    if db.get_setting(conn, "tithi_calc") != sig:
        conn.execute("DELETE FROM tithi_dates WHERE overridden_by IS NULL")
        db.set_setting(conn, "tithi_calc", sig)


def compute_date(t: dict, year: int) -> date | None:
    lat, lon = config.location()
    return panchang.date_for(year, t["masa"], t["paksha"], t["tithi"], lat, lon, config.tz(), settings.get("tithi_rule"))


def date_in(conn, event_id: str, t: dict, year: int) -> tuple[date | None, bool]:
    """(date, overridden) for an event's tithi in a Gregorian year."""
    row = conn.execute("SELECT date, overridden_by FROM tithi_dates WHERE event_id = ? AND year = ?", (event_id, year)).fetchone()
    if row:
        return date.fromisoformat(row["date"]), bool(row["overridden_by"])
    d = compute_date(t, year)
    if d:
        conn.execute("INSERT OR REPLACE INTO tithi_dates (event_id, year, date, overridden_by, computed_at) "
                     "VALUES (?, ?, ?, NULL, ?)", (event_id, year, d.isoformat(), config.now_iso()))
    return d, False


def label(t: dict, lang: str = "en") -> str:
    return panchang.name(t["masa"], t["paksha"], t["tithi"], lang if lang in ("te", "hi") else "en")


def all_tithis(conn) -> list:
    return [dict(r) for r in conn.execute(
        "SELECT t.*, e.type, e.person_id FROM event_tithi t JOIN events e ON e.id = t.event_id "
        "WHERE e.person_id IS NOT NULL AND e.type IN ('death','birth')")]


def entries(conn, g, today: date, days: int, lang: str = "en", me: str | None = None, label_fn=None) -> list:
    """Upcoming tithi days (death: 🪔 shraddha; birth: janma tithi of the living) from today to today + days."""
    _refresh_cache(conn)
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
            d, overridden = date_in(conn, t["event_id"], t, year)
            if not d or not today <= d <= horizon:
                continue
            name = label(t, lang)
            rel = label_fn(p.id) if label_fn and me and p.id != me else None
            who = {"id": p.id, "name": p.name, "given": p.given, "surname": p.surname, "photo": p.photo,
                   "photoRegion": p.photo_region, "gender": p.gender, "relationship": rel, "remind": p.remind,
                   "nameLocal": p.local_name}
            title = (f"Tithi for {p.name} — {name}" if t["type"] == "death" else f"Janma tithi of {p.name} — {name}")
            out.append({"date": d.isoformat(), "daysAway": (d - today).days, "kind": "tithi",
                        "sub": t["type"], "title": title, "tithiName": name, "years": None, "people": [who],
                        "remind": p.remind, "eventId": t["event_id"], "overridden": overridden})
    return out
