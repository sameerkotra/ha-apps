"""Report variables: finding them in SQL, their inputs, and the values bound for a run (SPEC.md section 19.6).

A variable is a named SQL parameter. A period variable `p` supplies two parameters,
:p_start and :p_end. NULL always means "All" / no filter.
"""
from __future__ import annotations

import calendar
import re
from dataclasses import dataclass, asdict
from datetime import date, timedelta

from .query_engine import parameters, QueryError


class InputError(QueryError):
    """A bad value typed into a report's form: safe to show anyone."""

TYPES = ("account", "period", "category", "text", "number", "date")
TYPE_LABELS = {"account": "Account", "period": "Period", "category": "Category",
               "text": "Text", "number": "Number", "date": "Date"}
PERIOD_PRESETS = (("month", "This month"), ("last_month", "Last month"), ("year", "This year"),
                  ("last_year", "Last year"), ("last_12", "Last 12 months"), ("all", "All time"),
                  ("custom", "Custom dates"))
_NAME = re.compile(r"^[A-Za-z_]\w{0,39}$")
_ISO = re.compile(r"^\d{4}-\d{2}-\d{2}$")


@dataclass
class Variable:
    name: str
    type: str = "text"
    label: str = ""
    default: str = ""
    allow_all: bool = True

    def to_dict(self) -> dict:
        return asdict(self)

    @property
    def params(self) -> list[str]:
        return [f"{self.name}_start", f"{self.name}_end"] if self.type == "period" else [self.name]

    @property
    def display_label(self) -> str:
        return self.label or self.name.replace("_", " ").capitalize()


def suggest_type(name: str) -> str:
    n = name.lower()
    if n in ("account", "account_id") or n.endswith("_account"):
        return "account"
    if n in ("period",) or n.endswith("_period"):
        return "period"
    if n in ("category",) or n.endswith("_category"):
        return "category"
    if n in ("start", "end", "from", "to", "since", "until", "day") or n.endswith(("_date", "_start", "_end")) \
            or n.startswith(("from_", "to_", "since_", "until_")):
        return "date"
    if n.startswith(("min_", "max_")) or n in ("amount", "limit", "top", "count", "n"):
        return "number"
    return "text"


def from_dicts(items) -> list[Variable]:
    out = []
    for d in items or []:
        if not isinstance(d, dict) or not _NAME.match(str(d.get("name", ""))):
            continue
        t = d.get("type") if d.get("type") in TYPES else "text"
        out.append(Variable(name=d["name"], type=t, label=str(d.get("label") or "")[:60],
                            default=str(d.get("default") or "")[:200], allow_all=bool(d.get("allow_all", True))))
    return out


def detect(sql: str, known: list[Variable] | None = None) -> tuple[list[Variable], list[str]]:
    """The variables the SQL needs, reusing `known` definitions, and warnings for known ones not used.

    :x_start with :x_end (or a known period x) become one period variable x."""
    known_by = {v.name: v for v in (known or [])}
    params = parameters(sql)
    pset = set(params)
    out: list[Variable] = []
    taken: set[str] = set()
    for p in params:
        if p in taken:
            continue
        base = None
        for suffix in ("_start", "_end"):
            if p.endswith(suffix):
                cand = p[: -len(suffix)]
                k = known_by.get(cand)
                if cand not in pset and ((k and k.type == "period") or (not k and {cand + "_start", cand + "_end"} <= pset)):
                    base = cand
        if base:
            var = known_by.get(base) or Variable(name=base, type="period", default="month")
            taken.update({base + "_start", base + "_end"})
        else:
            var = known_by.get(p)
            if var is None or var.type == "period":
                t = suggest_type(p)
                var = Variable(name=p, type=t, default="month" if t == "period" else "")
                if t == "period":           # a period needs _start/_end parameters; treat a bare name as text
                    var.type = "text"
            taken.add(p)
        if var.name not in {v.name for v in out}:
            out.append(var)
    used = {v.name for v in out}
    unused = [name for name in known_by if name not in used]
    return out, unused


# ---- periods -------------------------------------------------------------------------------------

def _month_end(d: date) -> date:
    return d.replace(day=calendar.monthrange(d.year, d.month)[1])


def period_range(preset: str, today: date | None = None, start: str = "", end: str = "") -> tuple[str | None, str | None]:
    """(start, end) inclusive ISO dates for a preset in the app time zone; (None, None) for All time."""
    today = today or date.today()
    if preset == "month":
        s, e = today.replace(day=1), _month_end(today)
    elif preset == "last_month":
        last = today.replace(day=1) - timedelta(days=1)
        s, e = last.replace(day=1), last
    elif preset == "year":
        s, e = date(today.year, 1, 1), date(today.year, 12, 31)
    elif preset == "last_year":
        s, e = date(today.year - 1, 1, 1), date(today.year - 1, 12, 31)
    elif preset == "last_12":
        y, m = today.year, today.month - 11
        while m < 1:
            m += 12
            y -= 1
        s, e = date(y, m, 1), _month_end(today)
    elif preset == "custom":
        return (start if _ISO.match(start or "") else None), (end if _ISO.match(end or "") else None)
    else:
        return None, None
    return s.isoformat(), e.isoformat()


# ---- binding -------------------------------------------------------------------------------------

def form_value(form, var: Variable) -> dict:
    """What the form sent for `var` (or its default), as strings, for re-rendering the inputs."""
    key = "v_" + var.name
    if var.type == "period":
        return {"preset": form.get(key) if key in form else (var.default or "month"),
                "from": form.get(key + "_from", ""), "to": form.get(key + "_to", "")}
    if key in form:
        return {"value": str(form.get(key) or "")}
    return {"value": var.default}


def bind(variables: list[Variable], form, account_ids: set[int], today: date | None = None) -> dict:
    """SQL parameters for a run. Raises QueryError naming the first bad value."""
    params: dict = {}
    for var in variables:
        got = form_value(form, var)
        label = var.display_label
        if var.type == "period":
            s, e = period_range(got["preset"], today, got["from"], got["to"])
            if s is None and e is None and not var.allow_all:
                raise InputError(f"Pick a period for {label}.")
            if s and e and s > e:
                raise InputError(f"{label}: the start date is after the end date.")
            params[var.name + "_start"], params[var.name + "_end"] = s, e
            continue
        raw = got["value"].strip()
        if raw in ("", "all") and var.type in ("account", "category"):
            if not var.allow_all:
                raise InputError(f"Pick a value for {label}.")
            params[var.name] = None
            continue
        if raw == "":
            if not var.allow_all:
                raise InputError(f"{label} needs a value.")
            params[var.name] = None
            continue
        if var.type == "account":
            try:
                aid = int(raw)
            except ValueError:
                raise InputError(f"Pick a value for {label}.") from None
            if aid not in account_ids:
                raise InputError(f"Pick one of your accounts for {label}.")
            params[var.name] = aid
        elif var.type == "number":
            try:
                num = float(raw)
            except ValueError:
                raise InputError(f"{label} must be a number.") from None
            params[var.name] = int(num) if num.is_integer() else num
        elif var.type == "date":
            if not _ISO.match(raw):
                raise InputError(f"{label} must be a date (YYYY-MM-DD).")
            params[var.name] = raw
        else:
            params[var.name] = raw[:500]
    return params


def definitions_from_form(form, names: list[str]) -> list[Variable]:
    """Variable definitions posted by the Query tab's Variables panel (d_<name>_type etc.)."""
    out = []
    for name in names:
        if not _NAME.match(name):
            continue
        t = form.get(f"d_{name}_type")
        out.append(Variable(
            name=name, type=t if t in TYPES else suggest_type(name),
            label=str(form.get(f"d_{name}_label", "") or "")[:60],
            default=str(form.get(f"d_{name}_default", "") or "")[:200],
            allow_all=form.get(f"d_{name}_all", "1" if f"d_{name}_type" not in form else "") == "1",
        ))
    return out


def query_key(sql: str, params: dict, time_limit: bool) -> str:
    return repr((sql, sorted(params.items()), time_limit))
