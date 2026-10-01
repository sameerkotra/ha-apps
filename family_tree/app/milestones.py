"""Milestone birthdays and anniversaries (§13.15): 🎉 and extra notice
at 90, 30 and 7 days before, and on the day. Only for the living, and only
when the year is known. Sahasra Chandra Darshanam is the day of the 1000th
full moon after birth (panchang.py)."""
import json
from datetime import date, datetime, time, timedelta

from . import config, db, graph as graph_mod, panchang

DEFAULTS = [
    ("age", 1, "First birthday", "మొదటి పుట్టినరోజు", "पहला जन्मदिन"),
    ("age", 60, "60th birthday", "షష్టిపూర్తి", "षष्टिपूर्ति"),
    ("age", 70, "70th birthday", "70వ పుట్టినరోజు", "70वाँ जन्मदिन"),
    ("age", 80, "80th birthday", "80వ పుట్టినరోజు", "80वाँ जन्मदिन"),
    ("full_moons", 1000, "Sahasra Chandra Darshanam", "సహస్ర చంద్ర దర్శనం", "सहस्र चंद्र दर्शन"),
    ("anniversary", 25, "Silver wedding anniversary", "రజతోత్సవం", "रजत जयंती"),
    ("anniversary", 50, "Golden wedding anniversary", "స్వర్ణోత్సవం", "स्वर्ण जयंती"),
    ("anniversary", 60, "Diamond wedding anniversary", "వజ్రోత్సవం", "हीरक जयंती"),
]
DEFAULT_LEADS = [90, 30, 7, 0]


def ensure_seeded(conn) -> None:
    if db.get_setting(conn, "milestones_seeded"):
        return
    for kind, value, en, te, hi in DEFAULTS:
        conn.execute("INSERT INTO milestones (id, kind, value, label_en, label_te, label_hi, lead_days) VALUES (?, ?, ?, ?, ?, ?, ?)",
                     (db.new_id(), kind, value, en, te, hi, json.dumps(DEFAULT_LEADS)))
    db.set_setting(conn, "milestones_seeded", "1")


def rows(conn, enabled_only: bool = True) -> list:
    ensure_seeded(conn)
    q = "SELECT * FROM milestones" + (" WHERE enabled = 1" if enabled_only else "") + " ORDER BY kind, value"
    out = []
    for r in conn.execute(q):
        d = dict(r)
        try:
            d["leads"] = sorted({int(x) for x in json.loads(r["lead_days"]) if 0 <= int(x) <= 366}, reverse=True)
        except (ValueError, TypeError):
            d["leads"] = DEFAULT_LEADS
        out.append(d)
    return out


def label(m: dict, lang: str = "en") -> str:
    return m.get(f"label_{lang}") or m["label_en"]


def _next(m: int, d: int, today: date) -> date:
    from .upcoming import next_date
    return next_date(m, d, today)


def upcoming(conn, g, today: date, horizon_days: int, lang: str = "en", leads_only: bool = False) -> list:
    """Milestones from today to today + horizon_days: [{date, daysAway, kind: "milestone", sub, title,
    label, value, people, remind, lead}]. With leads_only, only those whose days-away is one of the
    milestone's notice days (for reminders)."""
    ms = rows(conn)
    out = []
    ages = {m["value"]: m for m in ms if m["kind"] == "age"}
    anns = {m["value"]: m for m in ms if m["kind"] == "anniversary"}
    from . import features
    moons = [m for m in ms if m["kind"] == "full_moons"] if features.on("tithi") else []   # needs the lunar calendar
    horizon = today + timedelta(days=horizon_days)

    def who(p):
        return {"id": p.id, "name": p.name, "given": p.given, "surname": p.surname, "photo": p.photo,
                "photoRegion": p.photo_region, "gender": p.gender, "remind": p.remind, "nameLocal": p.local_name}

    def add(when, m, sub, title, people, remind, extra=None):
        away = (when - today).days
        if not 0 <= away <= horizon_days:
            return
        if leads_only and away not in m["leads"]:
            return
        e = {"date": when.isoformat(), "daysAway": away, "kind": "milestone", "sub": sub, "title": title,
             "label": label(m, lang), "value": m["value"], "people": people, "remind": remind, "years": m["value"]}
        e.update(extra or {})
        out.append(e)

    for p in g.people.values():
        b = p.birth or {}
        if not graph_mod.living(p) or not b.get("date_y") or not b.get("date_m") or not b.get("date_d"):
            continue
        nd = _next(b["date_m"], b["date_d"], today)
        age = nd.year - b["date_y"]
        if age in ages:
            add(nd, ages[age], "age", f"{p.name}'s {label(ages[age], lang)} ({age})", [who(p)], p.remind)
        for m in moons:
            t = time(*(int(x) for x in b["time"].split(":"))) if b.get("time") else time(6, 0)   # the birth time when known
            born = datetime.combine(date(b["date_y"], b["date_m"], b["date_d"]), t, tzinfo=config.tz())
            if born.date() + timedelta(days=int(m["value"] * 29.53) - 40) > horizon:
                continue
            try:
                d = panchang.full_moon_count_date(born, m["value"])
            except ValueError:
                continue
            add(d, m, "full_moons", f"{p.name}'s {label(m, lang)} ({m['value']} full moons)", [who(p)], p.remind)
    for f in g.families.values():
        mar = f.marriage or {}
        if not mar.get("date_y") or not mar.get("date_m") or not mar.get("date_d") or f.ended in ("divorced", "separated"):
            continue
        partners = f.partners()
        if len(partners) != 2 or not all(graph_mod.living(g.people[x]) for x in partners):
            continue
        nd = _next(mar["date_m"], mar["date_d"], today)
        years = nd.year - mar["date_y"]
        if years in anns:
            names = " & ".join(g.people[x].given or g.people[x].name for x in partners)
            add(nd, anns[years], "anniversary", f"{names}'s {label(anns[years], lang)} ({years})",
                [who(g.people[x]) for x in partners], any(g.people[x].remind for x in partners), {"familyId": f.id})
    out.sort(key=lambda e: e["date"])
    return out


def in_words(days: int) -> str:
    if days == 0:
        return "today"
    if days == 1:
        return "tomorrow"
    if days >= 60:
        return f"in {round(days / 30)} months"
    if days >= 14:
        return f"in {round(days / 7)} weeks"
    return f"in {days} days"
