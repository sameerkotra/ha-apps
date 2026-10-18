"""Patterns (the Symptoms page): for each symptom a person logged, which food tags and foods came in the hours
before it more often than at the same time on days without it.

It counts what happened together; it can't say what causes what. How it counts, for one symptom S over the chosen
days, with H = App settings → "Look back before a symptom (hours)":

- Before S: every time S was logged, the H hours before it (crossing midnight into the day before).
- Other days: every logged day (food on it) with no S, the H hours before the time of day S usually comes
  (the median of its times).
- A tag (or a food) is listed when it was in at least 2 of the "before S" windows, in at least half of them, and in
  at least 25 points more of them than of the other days' windows. Strongest difference first, at most 5 of each.

Entries from before food times were kept have no time: they count at their meal's usual time.
"""
from datetime import date, datetime, timedelta
from statistics import median

from . import foodrows

MIN_BEFORE = 2          # in at least this many "before" windows
MIN_SHARE = 0.5         # … and at least this share of them
MIN_LIFT = 0.25         # … and this much more often than on other days
TOP = 5


def _at(day: str, hhmm: str | None, meal: str | None = None) -> datetime:
    t = hhmm or foodrows.MEAL_TIMES.get(meal or "snack", "15:30")
    return datetime.fromisoformat(f"{day}T{t}")


def analyse(foods: list[dict], symptoms: list[dict], *, start: date, end: date, hours: int, min_count: int) -> dict:
    """foods: {date, eaten_at, meal_type, food_name, tags}; symptoms: {date, time, symptom, severity}."""
    span = timedelta(hours=hours)
    eaten = sorted(((_at(f["date"], f.get("eaten_at"), f.get("meal_type")),
                     " ".join(f["food_name"].split()).lower(), f["food_name"].strip(),
                     foodrows.tags_list(f.get("tags")) if isinstance(f.get("tags"), str) or f.get("tags") is None
                     else list(f["tags"]))
                    for f in foods), key=lambda x: x[0])
    names: dict[str, str] = {}                     # a food's shown name, by its folded name (the latest spelling)
    for _, key, shown, _ in eaten:
        names[key] = shown
    logged_days = {f["date"] for f in foods}

    def window(until: datetime):
        tags, items = set(), set()
        for when, key, _, tg in eaten:
            if until - span <= when < until:
                items.add(key)
                tags.update(tg)
        return tags, items

    by_name: dict[str, dict] = {}
    for s in symptoms:
        key = " ".join(s["symptom"].split()).lower()
        g = by_name.setdefault(key, {"name": s["symptom"].strip(), "rows": []})
        g["rows"].append(s)

    days = [(start + timedelta(days=i)).isoformat() for i in range((end - start).days + 1)]
    out, few = [], []
    for key, g in sorted(by_name.items(), key=lambda kv: -len(kv[1]["rows"])):
        rows = sorted(g["rows"], key=lambda r: (r["date"], r["time"]))
        worst = {}
        for r in rows:
            worst[r["date"]] = max(worst.get(r["date"], 0), r["severity"])
        info = {
            "name": g["name"], "count": len(rows), "days": len(worst),
            "avgSeverity": round(sum(r["severity"] for r in rows) / len(rows), 1),
            "last": rows[-1]["date"],
            "series": [worst.get(d, 0) for d in days],          # each day's worst severity (0 = none)
        }
        if len(rows) < min_count:
            few.append(info)
            continue
        before = [window(_at(r["date"], r["time"])) for r in rows]
        mins = [int(r["time"][:2]) * 60 + int(r["time"][3:]) for r in rows]
        usual = int(median(mins))
        usual_t = f"{usual // 60:02d}:{usual % 60:02d}"
        other = [window(_at(d, usual_t)) for d in sorted(logged_days) if d not in worst and start.isoformat() <= d
                 <= end.isoformat()]

        def compare(pick, label):
            seen: dict[str, int] = {}
            for w in before:
                for x in pick(w):
                    seen[x] = seen.get(x, 0) + 1
            found = []
            for x, n in seen.items():
                base = sum(1 for w in other if x in pick(w))
                share = n / len(before)
                base_share = base / len(other) if other else 0.0
                if n >= MIN_BEFORE and share >= MIN_SHARE and share - base_share >= MIN_LIFT:
                    found.append({label: x if label == "tag" else names.get(x, x), "before": n, "of": len(before),
                                  "other": base, "otherOf": len(other), "lift": round(share - base_share, 3)})
            found.sort(key=lambda f: (-f["lift"], -f["before"], f[label]))
            return found[:TOP]

        info.update(usualTime=usual_t, otherDays=len(other), tags=compare(lambda w: w[0], "tag"),
                    foods=compare(lambda w: w[1], "food"))
        out.append(info)
    return {"from": start.isoformat(), "to": end.isoformat(), "hours": hours, "minCount": min_count,
            "loggedDays": len([d for d in logged_days if start.isoformat() <= d <= end.isoformat()]),
            "dates": days, "symptoms": out, "tooFew": few}
