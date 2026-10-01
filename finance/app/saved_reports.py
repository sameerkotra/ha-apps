"""Saved reports (the saved_reports table, SPEC.md section 19.7).

Reports belong to the app, not to a user: every user sees the same list and each
run uses the data of whoever runs it. Only the admin creates, edits or deletes them.
"""
from __future__ import annotations

import json
import sqlite3

from .db import get_db
from .report_vars import Variable, from_dicts

CHART_TYPES = ("none", "bar", "line")


class ReportError(Exception):
    pass


def _load(row) -> dict:
    d = dict(row)
    try:
        d["variables"] = from_dicts(json.loads(d.get("variables_json") or "[]"))
    except (ValueError, TypeError):
        d["variables"] = []
    for key, default in (("totals", {"on": True, "skip": []}), ("chart", {"type": "none"})):
        try:
            value = json.loads(d.get(f"{key}_json") or "")
        except (ValueError, TypeError):
            value = None
        d[key] = value if isinstance(value, dict) else dict(default)
    d["totals"].setdefault("on", True)
    d["totals"].setdefault("skip", [])
    chart = d["chart"]
    chart["type"] = chart.get("type") if chart.get("type") in CHART_TYPES else "none"
    chart.setdefault("x", "")
    chart["y"] = [str(y) for y in chart.get("y") or []][:6]
    chart["keep_signs"] = bool(chart.get("keep_signs"))
    d["no_time_limit"] = bool(d.get("no_time_limit"))
    return d


def get(report_id: int) -> dict | None:
    with get_db() as conn:
        row = conn.execute("SELECT * FROM saved_reports WHERE id = ?", (report_id,)).fetchone()
    return _load(row) if row else None


def all_reports() -> list[dict]:
    with get_db() as conn:
        rows = conn.execute("SELECT * FROM saved_reports ORDER BY name COLLATE NOCASE").fetchall()
    return [_load(r) for r in rows]


def chart_from_form(form) -> dict:
    t = form.get("chart_type")
    return {"type": t if t in CHART_TYPES else "none",
            "x": (form.get("chart_x") or "").strip()[:80],
            "y": [s.strip() for s in (form.get("chart_y") or "").split(",") if s.strip()][:6],
            "keep_signs": form.get("chart_keep_signs") == "1"}


def save(*, report_id: int | None, name: str, description: str, sql: str, variables: list[Variable],
         totals: dict, chart: dict, no_time_limit: bool) -> int:
    name = " ".join(name.split())[:80]
    if not name:
        raise ReportError("Give the report a name.")
    values = (name, description.strip()[:500], sql, json.dumps([v.to_dict() for v in variables]),
              json.dumps(totals), json.dumps(chart), 1 if no_time_limit else 0)
    try:
        with get_db() as conn:
            if report_id is not None:
                cur = conn.execute(
                    "UPDATE saved_reports SET name = ?, description = ?, sql = ?, variables_json = ?, totals_json = ?, "
                    "chart_json = ?, no_time_limit = ?, updated_at = datetime('now') WHERE id = ?", values + (report_id,))
                if cur.rowcount == 0:
                    raise ReportError("That report no longer exists.")
                return report_id
            cur = conn.execute(
                "INSERT INTO saved_reports (name, description, sql, variables_json, totals_json, chart_json, no_time_limit) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)", values)
            return cur.lastrowid
    except sqlite3.IntegrityError:
        raise ReportError(f"A report named “{name}” already exists.") from None


def duplicate(report_id: int) -> int | None:
    report = get(report_id)
    if report is None:
        return None
    with get_db() as conn:
        names = {r[0].lower() for r in conn.execute("SELECT name FROM saved_reports")}
    n, name = 2, f"{report['name']} (copy)"
    while name.lower() in names:
        name, n = f"{report['name']} (copy {n})", n + 1
    return save(report_id=None, name=name, description=report["description"], sql=report["sql"],
                variables=report["variables"], totals=report["totals"], chart=report["chart"],
                no_time_limit=report["no_time_limit"])


def delete(report_id: int) -> None:
    with get_db() as conn:
        conn.execute("DELETE FROM saved_reports WHERE id = ?", (report_id,))


def mark_run(report_id: int) -> None:
    try:
        with get_db() as conn:
            conn.execute("UPDATE saved_reports SET last_run_at = datetime('now') WHERE id = ?", (report_id,))
    except sqlite3.Error:
        pass     # a busy database shouldn't fail the report itself
