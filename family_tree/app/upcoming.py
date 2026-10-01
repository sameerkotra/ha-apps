"""Upcoming birthdays, anniversaries and remembrance days (§9, §11).

Shared by the Upcoming view and the reminder digest (reminders.py).
Every entry carries `remind`: the 🔔 switch of its person (for an
anniversary, of either partner); the Upcoming view lists everyone anyway. Dates with a
day and month count even without a year; the age or number of years is then
simply left out. 29 February falls on 28 February in other years.
"""
import calendar
from datetime import date, timedelta

from . import graph as graph_mod, kin, relations


def ordinal(n: int) -> str:
    suffix = "th" if 10 <= n % 100 <= 20 else {1: "st", 2: "nd", 3: "rd"}.get(n % 10, "th")
    return f"{n}{suffix}"


def close_family(g, me: str, steps: int = 3) -> set:
    """Everyone within `steps` links of me, where a parent, child, sibling or
    partner each count as one step."""
    return g.within(me, steps)


def next_date(m: int, d: int, today: date) -> date:
    """The next time day d of month m comes round (today included)."""
    for year in (today.year, today.year + 1):
        day = d
        if m == 2 and d == 29 and not calendar.isleap(year):
            day = 28
        when = date(year, m, day)
        if when >= today:
            return when
    raise ValueError("unreachable")


def rel_fn(g, me: str | None, lang: str | None = None):
    """pid → relationship name to me (cached), or None."""
    cache = {}

    def rel(pid):
        if not me or me not in g.people or pid == me:
            return None
        if pid not in cache:
            r = relations.relationship(g, me, pid)
            cache[pid] = kin.label_for(g, me, pid, lang, r) if r["kind"] not in ("self", "none") else None
        return cache[pid]
    return rel


def extras(conn, g, today: date, days: int, me: str | None, scope: str = "close", lang: str | None = None,
           tithis: bool = True, remembrance: bool = True, leads_only: bool = False, tithi_lead: int | None = None,
           milestones_on: bool = True) -> list:
    """Tithi days (🪔 shraddha, janma tithi) and milestones (🎉), each only while its switch is on.
    With leads_only (reminders), milestones only on their notice days and tithis
    only on the day and `tithi_lead` days before."""
    from . import features, milestones as ms, tithi as tithi_mod
    tithis = tithis and features.on("tithi")                     # switched-off modules add nothing
    milestones_on = milestones_on and features.on("milestones")
    allowed = close_family(g, me) if (scope == "close" and me and me in g.people) else None
    rel = rel_fn(g, me, lang)
    lang2 = lang or kin.current()
    out = []
    if tithis:
        span = max(days, tithi_lead or 0)
        for e in tithi_mod.entries(conn, g, today, span, lang2, me, rel):
            if e["sub"] == "death" and not remembrance:
                continue
            if leads_only and e["daysAway"] not in (0, tithi_lead):
                continue
            if not leads_only and e["daysAway"] > days:
                continue
            out.append(e)
    if milestones_on:
        horizon = 366 if leads_only else days
        for e in ms.upcoming(conn, g, today, horizon, lang2, leads_only):
            out.append(e)
    res = []
    for e in out:
        if allowed is not None and not any(p["id"] in allowed for p in e["people"]):
            continue
        for p in e["people"]:
            p.setdefault("relationship", rel(p["id"]))
            if p.get("relationship") is None:
                p["relationship"] = rel(p["id"])
        res.append(e)
    return res


def merge(base: list, extra: list) -> list:
    """Adds the extras; an age or anniversary milestone on the same day as the
    birthday / anniversary entry marks that entry (🎉) instead of repeating it."""
    out = list(base)
    for e in extra:
        if e["kind"] == "milestone" and e["sub"] in ("age", "anniversary"):
            want = "birthday" if e["sub"] == "age" else "anniversary"
            twin = next((b for b in out if b["kind"] == want and b["date"] == e["date"] and b.get("years") == e["years"]
                         and {p["id"] for p in b["people"]} == {p["id"] for p in e["people"]}), None)
            if twin:
                twin["milestone"] = e["label"]
                continue
        out.append(e)
    out.sort(key=lambda e: (e["date"], e["kind"], e["title"]))
    return out


def entries(g, today: date, days: int, me: str | None, scope: str = "close", remembrance: bool = False,
            lang: str | None = None) -> list:
    """`lang`: relationship names (§13.1); None = the current request's."""
    horizon = today + timedelta(days=days)
    allowed = close_family(g, me) if (scope == "close" and me and me in g.people) else None
    rel_cache = {}

    def rel(pid):
        if not me or me not in g.people:
            return None
        if pid not in rel_cache:
            r = relations.relationship(g, me, pid)
            rel_cache[pid] = kin.label_for(g, me, pid, lang, r) if r["kind"] not in ("self", "none") else None
        return rel_cache[pid]

    def who(pid):
        p = g.people[pid]
        return {"id": pid, "name": p.name, "given": p.given, "surname": p.surname, "photo": p.photo,
                "gender": p.gender, "relationship": rel(pid),
                "remind": p.remind}

    out = []
    for pid, p in g.people.items():
        if allowed is not None and pid not in allowed:
            continue
        alive = graph_mod.living(p)
        b = p.birth
        if b and b.get("date_m") and b.get("date_d") and (alive or remembrance):
            when = next_date(b["date_m"], b["date_d"], today)
            years = when.year - b["date_y"] if b.get("date_y") else None
            if when <= horizon and not (years is not None and years < 1):     # born today, or a future date
                if alive:
                    title = f"{p.name} turns {years}" if years is not None else f"{p.name}'s birthday"
                    kind = "birthday"
                else:
                    title = (f"{p.name} — born {years} years ago" if years is not None else f"{p.name}'s birthday")
                    kind = "remembrance"
                out.append({"date": when.isoformat(), "daysAway": (when - today).days, "kind": kind, "sub": "birth",
                            "title": title, "years": years, "people": [who(pid)], "remind": p.remind})
        d = p.death
        if remembrance and not alive and d and d.get("date_m") and d.get("date_d"):
            when = next_date(d["date_m"], d["date_d"], today)
            years = when.year - d["date_y"] if d.get("date_y") else None
            if when <= horizon and not (years is not None and years < 1):
                title = f"Remembering {p.name}" + (f" — {years} years" if years else "")
                out.append({"date": when.isoformat(), "daysAway": (when - today).days, "kind": "remembrance", "sub": "death",
                            "title": title, "years": years, "people": [who(pid)], "remind": p.remind})
    for f in g.families.values():
        m = f.marriage
        if not m or not m.get("date_m") or not m.get("date_d") or f.ended in ("divorced", "separated"):
            continue
        partners = f.partners()
        if len(partners) != 2 or not all(graph_mod.living(g.people[x]) for x in partners):
            continue
        if allowed is not None and not any(x in allowed for x in partners):
            continue
        when = next_date(m["date_m"], m["date_d"], today)
        years = when.year - m["date_y"] if m.get("date_y") else None
        if when > horizon or (years is not None and years < 1):              # the wedding itself isn't an anniversary
            continue
        names = " & ".join(g.people[x].given or g.people[x].name for x in partners)
        title = f"{names} — {ordinal(years)} anniversary" if years else f"{names}'s anniversary"
        out.append({"date": when.isoformat(), "daysAway": (when - today).days, "kind": "anniversary", "sub": "marriage",
                    "title": title, "years": years, "people": [who(x) for x in partners], "familyId": f.id,
                    "remind": any(g.people[x].remind for x in partners)})     # either partner's 🔔 (§9)
    out.sort(key=lambda e: (e["date"], e["kind"], e["title"]))
    return out
