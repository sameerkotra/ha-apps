"""Slots (HOUSEHOLD_ASSISTANT_SPEC.md §17.5): when, how many and how much, read from the normalised tokens.

`read(tokens, today)` returns a Slots with what it found and which tokens it used (so the free text left for a
search, an item or a name doesn't include "this week"). Everything is in the household's own day (`today`, Home
Assistant's zone, as the briefing uses).
"""
from __future__ import annotations

import calendar
import re
from dataclasses import dataclass, field
from datetime import date, timedelta

from .normalise import DATE, ISO, MONEY, NUM, TIME, WORD, Token

MONTHS = {"january": 1, "jan": 1, "february": 2, "feb": 2, "march": 3, "mar": 3, "april": 4, "apr": 4, "may": 5,
          "june": 6, "jun": 6, "july": 7, "jul": 7, "august": 8, "aug": 8, "september": 9, "sep": 9, "sept": 9,
          "october": 10, "oct": 10, "november": 11, "nov": 11, "december": 12, "dec": 12}
WEEKDAYS = {"monday": 0, "mon": 0, "tuesday": 1, "tue": 1, "tues": 1, "wednesday": 2, "wed": 2, "thursday": 3,
            "thu": 3, "thur": 3, "thurs": 3, "friday": 4, "fri": 4, "saturday": 5, "sat": 5, "sunday": 6, "sun": 6}
# "may" and "march" are also everyday words: a month only with one of these before it or a year after it
MONTH_LEADS = {"in", "for", "of", "during", "last", "this", "since", "from", "until", "through", "to", "and", "&",
               "about", "on", "back", "early", "late", "mid"}
UNITS = {"litre", "litres", "liter", "liters", "l", "ltr", "kg", "kilo", "kilos", "g", "gram", "grams", "pack",
         "packs", "packet", "packets", "bottle", "bottles", "box", "boxes", "bag", "bags", "loaf", "loaves", "can",
         "cans", "tin", "tins", "jar", "jars", "dozen", "bunch", "bunches", "piece", "pieces", "pcs", "lb", "lbs",
         "pound", "pounds", "ml", "carton", "cartons", "tub", "tubs", "x"}

TODAY_WORDS = ("today", "tonight", "now", "currently", "right now", "so far", "this morning", "this afternoon",
               "this evening", "this arvo", "rest of the day", "for the day", "the day")
TOMORROW_WORDS = ("tomorrow", "tomorrow morning", "tomorrow night", "tomorrow evening", "tomorrow afternoon",
                  "the day after", "next day")
WEEK_WORDS = ("this week", "the week", "for the week", "rest of the week", "coming days", "upcoming", "coming up",
              "soon", "next few days", "next couple of days", "few days", "coming week", "week", "weekend",
              "this weekend", "the weekend", "over the weekend", "7 days")
NEXT_WEEK_WORDS = ("next week", "the week after", "next 2 weeks", "2 weeks", "fortnight", "next fortnight")
OVERDUE_WORDS = ("overdue", "late", "behind", "missed", "forgot", "forgotten", "past due", "outstanding")
ALL_WORDS = ("everything", "all of them", "anything at all", "ever")
MONTH_WORDS = {"this month": 0, "the month": 0, "month so far": 0, "last month": -1, "previous month": -1,
               "past month": -1, "the month before": -1, "a month ago": -1, "next month": 1}
YESTERDAY_WORDS = ("yesterday", "last night")


@dataclass
class Slots:
    when: str | None = None                 # today | tomorrow | week | next_week | overdue | all
    date: str | None = None                 # YYYY-MM-DD (a weekday, yesterday, a date)
    days: int | None = None                 # "next 3 days", "this week" (7), "next week" (14)
    month: str | None = None                # YYYY-MM
    time: str | None = None                 # HH:MM
    amount: float | None = None             # 40, ₹500, $12.50
    numbers: list = field(default_factory=list)   # other numbers, in order (quantities, servings)
    back: bool = False                      # `days` looks back ("the last 7 days"), not ahead
    used: set = field(default_factory=set)  # token positions the slots took
    words: list = field(default_factory=list)     # (start, end, kind): for the learned phrases' placeholders

    @property
    def any_time(self) -> bool:
        return bool(self.when or self.date or self.days or self.month)


def _seq(toks: list[Token], i: int, phrase: str) -> bool:
    words = phrase.split()
    if i + len(words) > len(toks):
        return False
    return all(toks[i + k].text == w for k, w in enumerate(words))


def _find(toks: list[Token], phrases, used: set) -> tuple[int, int] | None:
    """The first span (longest phrase first) where one of `phrases` is, not already used."""
    for p in sorted(phrases, key=lambda x: -len(x.split())):
        n = len(p.split())
        for i in range(len(toks)):
            if _seq(toks, i, p) and not (set(range(i, i + n)) & used):
                return i, i + n
    return None


def _take(s: Slots, span, kind: str):
    s.used.update(range(*span))
    s.words.append((span[0], span[1], kind))


def month_back(today: date, delta: int) -> str:
    y, m = today.year, today.month + delta
    while m < 1:
        y, m = y - 1, m + 12
    while m > 12:
        y, m = y + 1, m - 12
    return f"{y:04d}-{m:02d}"


def read(toks: list[Token], today: date) -> Slots:
    s = Slots()
    # numbers spelled with "next"/"in"/"within": "next 3 days", "in 2 weeks", "within 10 days"
    for i, t in enumerate(toks):
        if t.kind == NUM and i + 1 < len(toks) and toks[i + 1].text in ("day", "week", "month") and isinstance(
                t.value, (int, float)):
            unit = toks[i + 1].text
            start = i - 1 if i > 0 and toks[i - 1].text in ("next", "in", "within", "coming", "the", "for",
                                                               "past", "last") else i
            if unit == "month":
                continue
            n = int(t.value) * (7 if unit == "week" else 1)
            if start < i and toks[start].text in ("past", "last"):
                s.days, s.back = n, True                     # "the last 3 days": a look back, not ahead
                _take(s, (start, i + 2), "days")
                break
            s.days, s.when = n, ("week" if n > 1 else "today")
            _take(s, (start, i + 2), "days")
            break
    # dates and months written as numbers
    for i, t in enumerate(toks):
        if i in s.used:
            continue
        if t.kind == ISO:
            if len(t.text) == 7:
                s.month = t.text
                _take(s, (i, i + 1), "month")
            else:
                try:
                    date.fromisoformat(t.text)
                    s.date = t.text
                    _take(s, (i, i + 1), "date")
                except ValueError:
                    pass
        elif t.kind == DATE:
            parts = [int(x) for x in t.text.split("/")]
            try:
                if len(parts) == 2 and parts[1] > 12 and parts[0] <= 12 and len(t.text.split("/")[1]) == 4:
                    s.month = f"{parts[1]:04d}-{parts[0]:02d}"
                    _take(s, (i, i + 1), "month")
                    continue
                if len(parts) == 2 and parts[0] <= 12 and parts[1] >= 1900:
                    s.month = f"{parts[1]:04d}-{parts[0]:02d}"
                    _take(s, (i, i + 1), "month")
                    continue
                d, m = parts[0], parts[1]
                y = parts[2] if len(parts) == 3 else today.year
                if y < 100:
                    y += 2000
                if m > 12 and d <= 12:
                    d, m = m, d
                s.date = date(y, m, d).isoformat()
                _take(s, (i, i + 1), "date")
            except ValueError:
                pass
    # "this month", "last month", "next month"
    span = _find(toks, MONTH_WORDS, s.used)
    if span and not s.month:
        phrase = " ".join(t.text for t in toks[span[0]:span[1]])
        s.month = month_back(today, MONTH_WORDS[phrase])
        _take(s, span, "month")
    # a month by name: "in september", "sept 2025", "september"
    for i, t in enumerate(toks):
        if i in s.used or t.kind != WORD or t.text not in MONTHS:
            continue
        year = toks[i + 1].value if i + 1 < len(toks) and toks[i + 1].kind == NUM and isinstance(
            toks[i + 1].value, int) and 1900 <= toks[i + 1].value <= 2200 else None
        lead = toks[i - 1].text if i > 0 else ""
        if t.text in ("may", "mar", "march", "jan", "sun", "sat", "dec", "oct") and year is None and \
                lead not in MONTH_LEADS:
            if not (t.text in ("march",) and lead in ("in", "for")):
                continue
        m = MONTHS[t.text]
        if year is None:
            year = today.year if m <= today.month else today.year - 1     # the latest one not in the future
        s.month = f"{year:04d}-{m:02d}"
        _take(s, (i, i + 2) if year and i + 1 < len(toks) and toks[i + 1].kind == NUM and toks[i + 1].value == year
              else (i, i + 1), "month")
        break
    # yesterday / today / tomorrow / weeks / overdue
    for words, when in ((YESTERDAY_WORDS, "yesterday"), (NEXT_WEEK_WORDS, "next_week"), (TOMORROW_WORDS, "tomorrow"),
                        (TODAY_WORDS, "today"), (WEEK_WORDS, "week"), (OVERDUE_WORDS, "overdue"),
                        (ALL_WORDS, "all")):
        if s.when and when != "yesterday":
            continue
        span = _find(toks, words, s.used)
        if not span:
            continue
        phrase = " ".join(t.text for t in toks[span[0]:span[1]])
        more = _find(toks, words, s.used | set(range(*span)))
        while more:                                          # "coming up this week": both are the same when
            _take(s, more, "when")
            more = _find(toks, words, s.used)
        if when == "yesterday":
            s.date = (today - timedelta(days=1)).isoformat()
            _take(s, span, "date")
            continue
        if when == "week" and phrase in ("week",) and span[0] > 0 and toks[span[0] - 1].text in ("last", "past",
                                                                                                    "previous"):
            continue                                         # "last week": a look back, left to the model
        s.when = when
        if when == "week" and not s.days:
            s.days = 7
        if when == "next_week":
            s.days = 14
        _take(s, span, "when")
    # a weekday: "on friday", "this friday", "next friday", "friday"
    for i, t in enumerate(toks):
        if i in s.used or t.text not in WEEKDAYS:
            continue
        if t.text in ("sun", "sat", "wed", "mon") and not (i > 0 and toks[i - 1].text in ("on", "this", "next", "by")):
            continue
        ahead = (WEEKDAYS[t.text] - today.weekday()) % 7
        start = i
        if i > 0 and toks[i - 1].text == "next":
            ahead = ahead + 7 if ahead == 0 else ahead
            start = i - 1
        elif i > 0 and toks[i - 1].text in ("on", "this", "by"):
            start = i - 1
        d = today + timedelta(days=ahead)
        s.date = d.isoformat()
        if not s.when:
            s.when = "today" if ahead == 0 else ("tomorrow" if ahead == 1 else "week")
        if not s.days:
            s.days = max(1, ahead + 1)
        _take(s, (start, i + 1), "date")
        break
    # a time of day: "at 5", "at 5pm", "5:30", "17:00", "noon", "midnight", "in the evening"
    for i, t in enumerate(toks):
        if i in s.used:
            continue
        if t.kind == TIME:
            s.time = t.value
            _take(s, (i - 1 if i > 0 and toks[i - 1].text in ("at", "by", "around") else i, i + 1), "time")
            break
        if t.text in ("noon", "midday", "midnight"):
            s.time = "12:00" if t.text != "midnight" else "00:00"
            _take(s, (i - 1 if i > 0 and toks[i - 1].text == "at" else i, i + 1), "time")
            break
        if t.kind == NUM and i > 0 and toks[i - 1].text in ("at",) and isinstance(t.value, int) and 1 <= t.value <= 23:
            nxt = toks[i + 1].text if i + 1 < len(toks) else ""
            h = t.value
            if nxt in ("oclock", "o"):
                pass
            if h <= 7 and nxt not in ("am",):                 # "at 5" means 5 in the afternoon
                h += 12
            s.time = f"{h:02d}:00"
            _take(s, (i - 1, i + (2 if nxt in ("oclock",) else 1)), "time")
            break
    # money and numbers
    for i, t in enumerate(toks):
        if i in s.used:
            continue
        if t.kind == MONEY:
            s.amount = t.value
            _take(s, (i, i + 1), "amount")
        elif t.kind == NUM:
            nxt = toks[i + 1].text if i + 1 < len(toks) else ""
            if nxt in ("rupees", "rupee", "rs", "dollars", "dollar", "pounds", "pound", "euros", "euro", "bucks",
                       "quid", "inr", "usd"):
                s.amount = float(t.value)
                _take(s, (i, i + 2), "amount")
            else:
                s.numbers.append((i, t.value))
    return s


def days_left_in_month(today: date) -> int:
    return calendar.monthrange(today.year, today.month)[1] - today.day + 1


_HHMM = re.compile(r"^\d{2}:\d{2}$")
