"""How food rows leave the app: an entry (or saved food) logged without calories stores 0 and no_calories = 1
(so sums stay sums of what's known) and is shown with calories: null; tags are a list."""

# the usual time of a meal, for an entry logged on another day without a time (Patterns needs one)
MEAL_TIMES = {"breakfast": "08:00", "lunch": "12:30", "dinner": "19:00", "snack": "15:30"}


def tags_text(tags) -> str | None:
    return ",".join(tags) if tags else None


def tags_list(text) -> list[str]:
    return [t for t in (text or "").split(",") if t]


def out(row) -> dict:
    d = dict(row)
    if d.pop("no_calories", 0):
        d["calories"] = None
    if "tags" in d:
        d["tags"] = tags_list(d["tags"])
    return d
