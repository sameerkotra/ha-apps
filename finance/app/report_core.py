"""Showing a query result: sorting, paging, totals, money columns and CSV (SPEC.md section 19).

Shared by the Query tab (routes/query.py) and the Reports page (routes/reports.py).
"""
from __future__ import annotations

import json
import math
import re
from urllib.parse import urlencode

from markupsafe import Markup, escape

from .common import csv_export
from .query_engine import Result

PAGE_SIZES = (100, 250, 500, "all")
DEFAULT_PAGE_SIZE = 100
ALL_WARNING_ROWS = 5000
CELL_PREVIEW_CHARS = 200
_MONEY_NAME = re.compile(r"amount|total|cost|balance|charge|spen[dt]|income|paid|price|money|\bnet\b", re.I)


def is_number(v) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def _is_id(name: str) -> bool:
    n = name.lower()
    return n == "id" or n.endswith("_id") or n.endswith("_ref")


def numeric_columns(result: Result) -> list[int]:
    """Columns whose non-empty values are all numbers (and at least one is present)."""
    out = []
    for i in range(len(result.columns)):
        seen = False
        for row in result.rows:
            v = row[i]
            if v is None:
                continue
            if not is_number(v):
                break
            seen = True
        else:
            if seen:
                out.append(i)
    return out


def money_columns(result: Result, numeric: list[int]) -> set[int]:
    """Numeric columns shown as money: a money-like name, or values that are all cents (with some non-whole)."""
    out = set()
    for i in numeric:
        if _is_id(result.columns[i]):
            continue
        if _MONEY_NAME.search(result.columns[i]):
            out.add(i)
            continue
        values = [row[i] for row in result.rows if row[i] is not None]
        finite = [v for v in values if math.isfinite(v)]
        if finite and len(finite) == len(values) and any(isinstance(v, float) and v != int(v) for v in finite) \
                and all(round(v, 2) == v for v in finite):
            out.add(i)
    return out


def totals(result: Result, numeric: list[int], skip: list[str] | None = None) -> dict[int, float]:
    """Sum of each numeric column except ids and those listed in `skip`, rounded to cents."""
    skip_l = {s.strip().lower() for s in (skip or []) if s.strip()}
    out = {}
    for i in numeric:
        name = result.columns[i]
        if _is_id(name) or name.lower() in skip_l:
            continue
        out[i] = round(sum(row[i] for row in result.rows if row[i] is not None), 2)
    return out


def _sort_key(v):
    if v is None:
        return (2, 0, "")
    if is_number(v):
        return (0, v, "")
    return (1, 0, str(v).lower())


def sorted_rows(result: Result, column: int | None, desc: bool) -> list[tuple]:
    if column is None or not (0 <= column < len(result.columns)):
        return result.rows
    present = [r for r in result.rows if r[column] is not None]
    missing = [r for r in result.rows if r[column] is None]
    present.sort(key=lambda r: _sort_key(r[column]), reverse=desc)
    return present + missing


def parse_page_size(raw) -> int | str:
    if raw == "all":
        return "all"
    try:
        n = int(raw)
    except (TypeError, ValueError):
        return DEFAULT_PAGE_SIZE
    return n if n in PAGE_SIZES else DEFAULT_PAGE_SIZE


def _int(raw, default=None):
    try:
        return int(raw)
    except (TypeError, ValueError):
        return default


class ResultView:
    """One page of a kept result, plus everything the results template needs.

    `link` builds the attributes of a pager/sort control from a dict of changes; the Query
    tab uses htmx posts, the Reports page plain GET links."""

    def __init__(self, result: Result, *, page=1, per_page=DEFAULT_PAGE_SIZE, sort=None, desc=False,
                 skip_totals=None, totals_on=True, link=None):
        self.result = result
        self.columns = result.columns
        self.per_page = parse_page_size(per_page)
        self.sort = _int(sort)
        self.desc = bool(desc)
        rows = sorted_rows(result, self.sort, self.desc)
        self.total_rows = len(rows)
        if self.per_page == "all":
            self.pages, self.page, start, end = 1, 1, 0, self.total_rows
        else:
            self.pages = max(1, -(-self.total_rows // self.per_page))
            self.page = min(max(1, _int(page, 1)), self.pages)
            start, end = (self.page - 1) * self.per_page, self.page * self.per_page
        self.rows = rows[start:end]
        self.first = start + 1 if self.rows else 0
        self.last = start + len(self.rows)
        self.numeric = numeric_columns(result)
        self.money = money_columns(result, self.numeric)
        self.totals = totals(result, self.numeric, skip_totals) if totals_on and result.rows else {}
        self.label_column = next((i for i in range(len(self.columns)) if i not in self.numeric),
                                 next((i for i in range(len(self.columns)) if i not in self.totals), None))
        self._link = link or (lambda changes: Markup(""))
        self.show_pager = self.total_rows > DEFAULT_PAGE_SIZE or self.per_page != DEFAULT_PAGE_SIZE
        self.all_warning = self.per_page == "all" and self.total_rows > ALL_WARNING_ROWS

    def state(self, **changes) -> dict:
        s = {"page": self.page, "per_page": self.per_page, "sort": "" if self.sort is None else self.sort,
             "dir": "desc" if self.desc else "asc"}
        s.update(changes)
        return s

    def link(self, **changes) -> Markup:
        return self._link(self.state(**changes))

    def sort_link(self, i: int) -> Markup:
        desc = not self.desc if self.sort == i else False
        return self.link(sort=i, dir="desc" if desc else "asc", page=1)

    def cell(self, i: int, v) -> Markup:
        if v is None:
            return Markup('<span class="q-null">NULL</span>')
        if i in self.money and is_number(v):
            cls = ' class="negative"' if v < 0 else ""
            return Markup(f'<span{cls}>{v:,.2f}</span>')
        if is_number(v):
            return escape(str(v))
        text = str(v)
        if len(text) > CELL_PREVIEW_CHARS:
            return Markup('<details class="q-long"><summary>{}…</summary><div>{}</div></details>').format(
                text[:CELL_PREVIEW_CHARS], text)
        return escape(text)

    def total_cell(self, i: int) -> Markup:
        if i in self.totals:
            v = self.totals[i]
            if i in self.money or isinstance(v, float):
                cls = ' class="negative"' if v < 0 else ""
                return Markup(f'<span{cls}>{v:,.2f}</span>')
            return Markup(escape(f"{v:,}"))
        return Markup("")


def htmx_link(endpoint: str, include: str, extra: dict):
    def link(state: dict) -> Markup:
        vals = json.dumps({**extra, **{k: str(v) for k, v in state.items()}})
        return Markup('hx-post="{}" hx-include="{}" hx-target="#query-results" hx-vals=\'{}\'').format(
            endpoint, include, Markup(escape(vals)))
    return link


def get_link(path: str, base: dict):
    def link(state: dict) -> Markup:
        params = {k: v for k, v in {**base, **state}.items() if v not in ("", None)}
        if params.get("page") in (1, "1"):
            params.pop("page")
        return Markup('href="{}"').format(path + "?" + urlencode(params, doseq=True))
    return link


def csv_bytes(result: Result, sort=None, desc=False) -> bytes:
    """UTF-8, no BOM; text cells (headers included) through the formula guard, numbers as they are."""
    return csv_export.to_bytes([csv_export.guard(c) for c in result.columns],
                               (["" if v is None else csv_export.guard(v) for v in row]
                                for row in sorted_rows(result, _int(sort), bool(desc))))


def csv_filename(name: str, today: str) -> str:
    slug = re.sub(r"[^A-Za-z0-9]+", "-", name).strip("-").lower() or "query"
    return f"{slug}-{today}.csv"
