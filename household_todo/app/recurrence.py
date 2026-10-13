"""Pure date logic for schedule-item rules (SPEC §8a, §8k).

No I/O, no imports from the rest of the app — everything here can be
unit-tested without a server, a database or Home Assistant.

Rule grammar (always read together with an `anchor_date`):

    daily
    weeks:<N>:<d,d,...>   every N weeks on ISO weekdays (1=Mon..7=Sun), ascending
    every:<N>             every N days
    monthly:<day>         day of month 1-31 (clamped to the month's last day)
    months:<N>:<day>      every N months (2-36) on that day, counted from the anchor's month
    years:<N>             every N years (1-10) on the anchor's month and day
                          (a 29 Feb anchor falls on 28 Feb in non-leap years)
    nth:<n>:<d>           nth weekday of the month: n in 1,2,3,4,-1 (last)

A date D is an occurrence only if D >= anchor_date and D matches the rule.
"""
import re
from calendar import monthrange
from datetime import date, timedelta
from typing import Iterable

RULE_RE = re.compile(
    r"^(daily"
    r"|weeks:([1-9]|[1-4][0-9]|5[0-2]):[1-7](,[1-7]){0,6}"
    r"|every:[1-9][0-9]{0,2}"
    r"|monthly:([1-9]|[12][0-9]|3[01])"
    r"|months:([2-9]|[12][0-9]|3[0-6]):([1-9]|[12][0-9]|3[01])"
    r"|years:([1-9]|10)"
    r"|nth:(1|2|3|4|-1):[1-7])$"
)
_ISO_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")

WEEKDAY_ABBR = {1: "Mon", 2: "Tue", 3: "Wed", 4: "Thu", 5: "Fri", 6: "Sat", 7: "Sun"}
WEEKDAY_FULL = {1: "Monday", 2: "Tuesday", 3: "Wednesday", 4: "Thursday",
                5: "Friday", 6: "Saturday", 7: "Sunday"}

# How far ahead an occurrence is looked for: the sparsest legal rule is years:10 (~3,653 days).
_MAX_STEP_DAYS = 3700


def parse_date(value) -> date | None:
    """ISO 'YYYY-MM-DD' string (or a date) -> date, or None if invalid."""
    if isinstance(value, date):
        return value
    if not isinstance(value, str) or not _ISO_DATE_RE.match(value):
        return None
    try:
        return date.fromisoformat(value)
    except ValueError:
        return None


def _parse_rule(rule: str):
    """('daily',) | ('weeks', N, [d..]) | ('every', N) | ('monthly', day)
    | ('months', N, day) | ('years', N) | ('nth', n, d)."""
    if rule == "daily":
        return ("daily",)
    kind, _, rest = rule.partition(":")
    if kind == "weeks":
        n, _, days = rest.partition(":")
        return ("weeks", int(n), [int(x) for x in days.split(",")])
    if kind == "every":
        return ("every", int(rest))
    if kind == "monthly":
        return ("monthly", int(rest))
    if kind == "months":
        n, _, d = rest.partition(":")
        return ("months", int(n), int(d))
    if kind == "years":
        return ("years", int(rest))
    if kind == "nth":
        n, _, d = rest.partition(":")
        return ("nth", int(n), int(d))
    raise ValueError(f"bad rule {rule!r}")


def validate_rule(rule, anchor_date) -> str | None:
    """An error message, or None if (rule, anchor_date) is valid."""
    if not isinstance(rule, str) or not RULE_RE.match(rule):
        return "That repeat rule isn't valid."
    parsed = _parse_rule(rule)
    if parsed[0] == "weeks":
        days = parsed[2]
        if any(b <= a for a, b in zip(days, days[1:])):
            return "Weekdays must be listed once each, in order."
    anchor = parse_date(anchor_date)
    if anchor is None:
        return "The date isn't a valid date (use YYYY-MM-DD)."
    if parsed[0] == "weeks" and anchor.isoweekday() not in parsed[2]:
        names = ", ".join(WEEKDAY_ABBR[d] for d in parsed[2])
        return (f"The first occurrence must fall on one of the chosen weekdays "
                f"({names}); {anchor.isoformat()} is a {WEEKDAY_ABBR[anchor.isoweekday()]}.")
    return None


def _monday(d: date) -> date:
    return d - timedelta(days=d.isoweekday() - 1)


def occurs_on(rule: str, anchor_date, day) -> bool:
    """True if `day` is a rule occurrence (ignoring exceptions)."""
    anchor = parse_date(anchor_date)
    day = parse_date(day)
    if anchor is None or day is None or day < anchor:
        return False
    parsed = _parse_rule(rule)
    kind = parsed[0]
    if kind == "daily":
        return True
    if kind == "weeks":
        _, n, days = parsed
        if day.isoweekday() not in days:
            return False
        weeks_between = (_monday(day) - _monday(anchor)).days // 7
        return weeks_between % n == 0
    if kind == "every":
        return (day - anchor).days % parsed[1] == 0
    if kind == "monthly":
        last = monthrange(day.year, day.month)[1]
        return day.day == min(parsed[1], last)
    if kind == "months":
        _, n, dom = parsed
        gap = (day.year * 12 + day.month) - (anchor.year * 12 + anchor.month)
        return gap % n == 0 and day.day == min(dom, monthrange(day.year, day.month)[1])
    if kind == "years":
        n = parsed[1]
        if (day.year - anchor.year) % n != 0 or day.month != anchor.month:
            return False
        return day.day == min(anchor.day, monthrange(day.year, day.month)[1])
    if kind == "nth":
        _, n, wd = parsed
        if day.isoweekday() != wd:
            return False
        if n > 0:
            return (day.day - 1) // 7 + 1 == n
        last = monthrange(day.year, day.month)[1]
        return day.day + 7 > last  # the last such weekday of the month
    return False


def _months_later(year: int, month: int, n: int) -> tuple[int, int]:
    m = year * 12 + (month - 1) + n
    return m // 12, m % 12 + 1


def _clamped(year: int, month: int, dom: int) -> date | None:
    """`dom` in that month, clamped to its last day (None past the calendar's end, year 9999)."""
    if year > 9999:
        return None
    return date(year, month, min(dom, monthrange(year, month)[1]))


def _nth_weekday(year: int, month: int, n: int, wd: int) -> date:
    """The nth (1–4, or -1 = last) ISO weekday `wd` of the month."""
    if n > 0:
        first = date(year, month, 1)
        return first + timedelta(days=(wd - first.isoweekday()) % 7 + 7 * (n - 1))
    last = date(year, month, monthrange(year, month)[1])
    return last - timedelta(days=(last.isoweekday() - wd) % 7)


def _next_parsed(parsed, anchor: date, d: date) -> date | None:
    """First occurrence of the parsed rule on or after `d` (d >= anchor), worked out directly rather than by
    trying every day — the same dates occurs_on() accepts (tests/test_recurrence.py compares the two)."""
    kind = parsed[0]
    if kind == "daily":
        return d
    if kind == "every":
        n = parsed[1]
        return d + timedelta(days=-(d - anchor).days % n)
    if kind == "weeks":
        _, n, days = parsed
        days = sorted(set(days))
        a_monday = _monday(anchor)
        week = (_monday(d) - a_monday).days // 7
        if week % n == 0:
            later = [x for x in days if x >= d.isoweekday()]
            if later:
                return _monday(d) + timedelta(days=later[0] - 1)
            week += n
        else:
            week += n - week % n
        return a_monday + timedelta(days=7 * week + days[0] - 1)
    if kind == "monthly":
        dom = parsed[1]
        this = _clamped(d.year, d.month, dom)
        if this >= d:
            return this
        return _clamped(*_months_later(d.year, d.month, 1), dom)
    if kind == "months":
        _, n, dom = parsed
        gap = (d.year * 12 + d.month) - (anchor.year * 12 + anchor.month)
        if gap % n == 0:
            this = _clamped(d.year, d.month, dom)
            if this >= d:
                return this
            step = n
        else:
            step = n - gap % n
        return _clamped(*_months_later(d.year, d.month, step), dom)
    if kind == "years":
        n = parsed[1]
        year = d.year + (-(d.year - anchor.year)) % n
        this = _clamped(year, anchor.month, anchor.day)
        if this is not None and this < d:
            this = _clamped(year + n, anchor.month, anchor.day)
        return this
    if kind == "nth":
        _, n, wd = parsed
        this = _nth_weekday(d.year, d.month, n, wd)
        if this >= d:
            return this
        y, m = _months_later(d.year, d.month, 1)
        return None if y > 9999 else _nth_weekday(y, m, n, wd)
    return None


def next_rule_occurrence(rule: str, anchor_date, day) -> date | None:
    """First rule occurrence on or after `day` (no exceptions), or None."""
    anchor = parse_date(anchor_date)
    day = parse_date(day)
    if anchor is None or day is None:
        return None
    d = max(day, anchor)
    found = _next_parsed(_parse_rule(rule), anchor, d)
    if found is None or (found - d).days > _MAX_STEP_DAYS:
        return None
    return found


def next_on_or_after(rule: str, anchor_date, day, removed: Iterable = (), added: Iterable = ()) -> date | None:
    """First *effective* occurrence on or after `day` (§8k): the earlier of
    (a) the first rule occurrence not in `removed` and (b) the earliest date
    in `added` on or after `day`. Looks at most _MAX_STEP_DAYS × 51 days
    ahead (an occurrence, plus up to 50 removed ones to step over)."""
    anchor = parse_date(anchor_date)
    day = parse_date(day)
    if anchor is None or day is None:
        return None
    removed = {parse_date(x) for x in removed}
    added = {parse_date(x) for x in added}
    added_ahead = [a for a in added if a is not None and a >= day]
    best_added = min(added_ahead) if added_ahead else None

    start = d = max(day, anchor)
    parsed = _parse_rule(rule)
    horizon = start + timedelta(days=_MAX_STEP_DAYS * 51)     # the first day no longer looked at
    while True:
        found = _next_parsed(parsed, anchor, d)
        if found is None or found >= horizon:
            return best_added
        if best_added is not None and found >= best_added:
            return best_added
        if found not in removed:
            return found
        d = found + timedelta(days=1)


def is_effective(rule: str, anchor_date, day, removed: Iterable = (), added: Iterable = ()) -> bool:
    """(occurs_on and not removed) or added."""
    day = parse_date(day)
    if day is None:
        return False
    removed = {parse_date(x) for x in removed}
    added = {parse_date(x) for x in added}
    return (occurs_on(rule, anchor_date, day) and day not in removed) or day in added


def rule_occurrences_between(rule: str, anchor_date, start, end) -> list[date]:
    """Every rule occurrence in [start, end] inclusive (no exceptions)."""
    anchor = parse_date(anchor_date)
    start = parse_date(start)
    end = parse_date(end)
    if anchor is None or start is None or end is None:
        return []
    out = []
    d = max(start, anchor)
    if d > end:
        return out
    parsed = _parse_rule(rule)
    while d <= end:
        found = _next_parsed(parsed, anchor, d)
        if found is None or found > end:
            break
        out.append(found)
        d = found + timedelta(days=1)
    return out


def next_rule_occurrences(rule: str, anchor_date, day, count: int) -> list[date]:
    """The next `count` rule occurrences on or after `day` (no exceptions)."""
    out: list[date] = []
    cursor = parse_date(day)
    while cursor is not None and len(out) < count:
        nxt = next_rule_occurrence(rule, anchor_date, cursor)
        if nxt is None:
            break
        out.append(nxt)
        cursor = nxt + timedelta(days=1)
    return out


def _ordinal(n: int) -> str:
    if 10 <= n % 100 <= 20:
        suffix = "th"
    else:
        suffix = {1: "st", 2: "nd", 3: "rd"}.get(n % 10, "th")
    return f"{n}{suffix}"


def _join_days(days: list[int]) -> str:
    names = [WEEKDAY_ABBR[d] for d in days]
    if len(names) == 1:
        return names[0]
    return ", ".join(names[:-1]) + " & " + names[-1]


def describe_rule(rule: str) -> str:
    """Human label used in the UI, the API (`ruleLabel`) and the sensor."""
    try:
        parsed = _parse_rule(rule)
    except ValueError:
        return rule
    kind = parsed[0]
    if kind == "daily":
        return "Daily"
    if kind == "weeks":
        _, n, days = parsed
        if n == 1:
            if len(days) == 1:
                return f"Every {WEEKDAY_FULL[days[0]]}"
            return f"Every {_join_days(days)}"
        return f"Every {n} weeks on {_join_days(days)}"
    if kind == "every":
        n = parsed[1]
        return "Every day" if n == 1 else f"Every {n} days"
    if kind == "monthly":
        return f"Monthly on the {_ordinal(parsed[1])}"
    if kind == "months":
        return f"Every {parsed[1]} months on the {_ordinal(parsed[2])}"
    if kind == "years":
        n = parsed[1]
        return "Every year" if n == 1 else f"Every {n} years"
    if kind == "nth":
        _, n, wd = parsed
        if n == -1:
            return f"Last {WEEKDAY_FULL[wd]} of the month"
        return f"{_ordinal(n)} {WEEKDAY_FULL[wd]} of the month"
    return rule


def min_gap_days(rule: str) -> int:
    """The smallest number of days between two consecutive occurrences of a
    rule. The item form warns "this sensor will always be on" when
    lead_days is at least this."""
    try:
        parsed = _parse_rule(rule)
    except ValueError:
        return 1
    kind = parsed[0]
    if kind == "daily":
        return 1
    if kind == "every":
        return parsed[1]
    if kind == "weeks":
        _, n, days = parsed
        gaps = [b - a for a, b in zip(days, days[1:])]
        gaps.append(n * 7 - (days[-1] - days[0]))  # wrap to the next active week
        return min(gaps)
    if kind == "months":
        return 28 * parsed[1]
    if kind == "years":
        return 365 * parsed[1]
    # monthly / nth: the closest two consecutive occurrences are 28 days apart
    return 28
