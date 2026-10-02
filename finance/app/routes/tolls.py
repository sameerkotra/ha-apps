"""tolls (SPEC.md section 18): Dashboard, Compare, Analyze, and the
cars / devices / tags / groups behind them. Statement upload, status, restart,
delete, review and debug are in routes/toll_statements.py; parsing runs on the
shared job queue (app/jobs.py). Separate from finance and utilities: toll amounts
never appear in finance totals.

    GET  /tolls?tab=dashboard|upload     summary + toll list, or upload + history + Cars + Devices
    GET  /toll-compare                   two months / two years, trend charts
    GET  /toll-analyze                   regular trips, tags, untagged trips
    POST /toll-car-add /toll-car-update /toll-car-archive /toll-car-delete
    POST /toll-device-assign /toll-device-delete
    POST /toll-group-add /toll-group-update /toll-group-delete /toll-pattern-group-assign
    POST /toll-pattern-save /toll-pattern-delete /toll-pattern-ignore /toll-trip-tag

Every query is scoped to the acting user.
"""
from datetime import date
from pathlib import Path
from urllib.parse import urlencode

from fastapi import APIRouter, Depends, Form, Query, Request
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates

from .. import features as _features

from .. import ai_client as _ai_client

from .. import settings as _settings

from ..auth import User, get_current_user, get_acting_user
from ..charts import column_chart, stacked_column_chart
from ..db import get_db
from ..paging import TableView
from ..toll_trips import (
    clock_value, format_minutes, parse_clock, pretty_signature, rebuild_trips, suggest_regular,
)
from .. import toll_trips as toll_trips_module  # MIN_TRIPS is read at call time (tests adjust it)
from ..tolls import device_label, vehicle_label
from ..version import APP_VERSION
from .accounts import _acting_banner, _acting_qs
from .dashboard import _MONTH_RE, _YEAR_RE, month_label, month_range, resolve_period, year_range

router = APIRouter(dependencies=[Depends(_features.required("tolls"))])
templates = Jinja2Templates(directory=str(Path(__file__).parent.parent / "templates"))
templates.env.globals["app_version"] = APP_VERSION
templates.env.globals["ai_privacy"] = _ai_client.privacy
templates.env.globals["ai_configured"] = _settings.ai_configured
templates.env.globals["vehicle_label"] = vehicle_label
templates.env.globals["format_minutes"] = format_minutes
templates.env.globals["clock_value"] = clock_value
templates.env.globals["pretty_signature"] = pretty_signature

# Passes that count everywhere (Dashboard, Compare, trips): live, confirmed, on a live statement.
_PASS_FROM = (
    "FROM toll_transactions t JOIN toll_statements s ON s.id = t.statement_id "
    "JOIN toll_devices d ON d.id = t.device_ref LEFT JOIN toll_cars c ON c.id = d.car_id "
    "LEFT JOIN toll_trips tr ON tr.id = t.trip_id LEFT JOIN toll_patterns p ON p.id = tr.pattern_id "
    "LEFT JOIN toll_pattern_groups g ON g.id = p.group_id"
)

# Chart colours follow a group's creation order (never a period's rank), so a group keeps its colour on every
# page; a 6th+ group folds into "Other".
_GROUP_CHART_CLASSES = ["chart-cat-1", "chart-cat-2", "chart-cat-3", "chart-cat-4", "chart-cat-5"]
_OTHER_TAGGED_CLASS = "chart-cat-muted"     # a 6th+ group, and any tag with no group, fold in here
_NOT_TAGGED_CLASS = "chart-cat-none"


def _redirect(path: str, current: User, acting: User, **params) -> RedirectResponse:
    query = {k: v for k, v in params.items() if v not in (None, "")}
    qs = _acting_qs(current, acting)
    if qs:
        query["as_user"] = acting.id
    return RedirectResponse(url=path + ("?" + urlencode(query) if query else ""), status_code=303)


def _pass_where(user_id: str, start: str, end: str, car) -> tuple[str, list]:
    """`car`: None = every car, "none" = passes of tags not assigned to a car, an int = that car."""
    where = "t.user_id = ? AND t.deleted_at IS NULL AND t.review_status = 'clean' AND s.deleted_at IS NULL"
    params: list = [user_id]
    if start:
        where += " AND t.date >= ? AND t.date <= ?"
        params += [start, end]
    if car == "none":
        where += " AND d.car_id IS NULL"
    elif car is not None:
        where += " AND d.car_id = ?"
        params.append(car)
    return where, params


def _int_or_none(value) -> int | None:
    return int(value) if str(value or "").strip().isdigit() else None


def _car_filter(value) -> int | str | None:
    """The car picker's value: a car id, "none" (tags not assigned to a car), or nothing."""
    value = str(value or "").strip()
    return "none" if value == "none" else (int(value) if value.isdigit() else None)


def _vehicle_label(row) -> str:
    """A row that carries `car_name` (may be None) and the tag columns -> the car's name or the bare tag."""
    return vehicle_label(row["car_name"], row)


def available_periods(conn, user_id: str) -> tuple[list[str], list[str]]:
    """(months, years) that have at least one confirmed pass, newest first."""
    rows = conn.execute(
        "SELECT DISTINCT substr(t.date, 1, 7) AS m FROM toll_transactions t JOIN toll_statements s ON s.id = t.statement_id "
        "WHERE t.user_id = ? AND t.deleted_at IS NULL AND t.review_status = 'clean' AND s.deleted_at IS NULL "
        "ORDER BY m DESC", (user_id,)).fetchall()
    months = [r["m"] for r in rows if r["m"] and _MONTH_RE.match(r["m"])]
    return months, sorted({m[:4] for m in months}, reverse=True)


def toll_figures(conn, user_id: str, start: str, end: str, car_id) -> dict:
    """Everything the Dashboard and Compare add up, for one date range and an optional car:
    the total, one line per tag plus Not tagged (so tagged + not tagged = total), and totals by car,
    by plaza and by month. Money is rounded to cents; the printed Grand Totals is never used here."""
    where, params = _pass_where(user_id, start, end, car_id)

    total = conn.execute(
        f"SELECT COALESCE(SUM(t.amount), 0) AS cost, COUNT(*) AS passes, COUNT(DISTINCT tr.id) AS trips {_PASS_FROM} WHERE {where}",
        params).fetchone()

    tags, untagged = [], {"cost": 0.0, "passes": 0, "trips": 0}
    for r in conn.execute(
            f"SELECT tr.pattern_id AS pattern_id, p.name AS name, p.group_id AS group_id, g.name AS group_name, "
            f"COALESCE(SUM(t.amount), 0) AS cost, COUNT(*) AS passes, "
            f"COUNT(DISTINCT tr.id) AS trips {_PASS_FROM} WHERE {where} GROUP BY tr.pattern_id", params):
        item = {"id": r["pattern_id"], "name": r["name"], "group_id": r["group_id"], "group_name": r["group_name"],
                "cost": round(r["cost"], 2), "passes": r["passes"], "trips": r["trips"]}
        if r["pattern_id"] is None:
            untagged = item
        else:
            item["average"] = round(item["cost"] / item["trips"], 2) if item["trips"] else 0.0
            tags.append(item)
    tags.sort(key=lambda t: (-t["cost"], t["name"] or ""))

    # Tags in a group are summed into one row; an ungrouped tag shows on its own.
    groups_by_id: dict[int, dict] = {}
    ungrouped_tags = []
    for item in tags:
        gid = item["group_id"]
        if gid is None:
            ungrouped_tags.append(item)
            continue
        acc = groups_by_id.setdefault(gid, {"id": gid, "name": item["group_name"], "cost": 0.0, "passes": 0, "trips": 0})
        acc["cost"] = round(acc["cost"] + item["cost"], 2)
        acc["passes"] += item["passes"]
        acc["trips"] += item["trips"]
    for acc in groups_by_id.values():
        acc["average"] = round(acc["cost"] / acc["trips"], 2) if acc["trips"] else 0.0
    groups = sorted(groups_by_id.values(), key=lambda g: (-g["cost"], g["name"] or ""))
    tag_display_rows = sorted(
        [{**g, "kind": "group"} for g in groups] + [{**t, "kind": "tag"} for t in ungrouped_tags],
        key=lambda r: (-r["cost"], r["name"] or ""),
    )

    by_car = []
    for r in conn.execute(
            f"SELECT d.car_id AS car_id, CASE WHEN d.car_id IS NULL THEN d.id END AS dev, c.name AS car_name, "
            f"d.device_id AS device_id, d.plate AS plate, d.plate_state AS plate_state, COALESCE(SUM(t.amount), 0) AS cost, "
            f"COUNT(*) AS passes, COUNT(DISTINCT tr.id) AS trips {_PASS_FROM} WHERE {where} "
            f"GROUP BY d.car_id, dev ORDER BY cost DESC", params):
        by_car.append({"key": f"c{r['car_id']}" if r["car_id"] is not None else f"d{r['dev']}", "label": _vehicle_label(r),
                       "filter": str(r["car_id"]) if r["car_id"] is not None else "none", "cost": round(r["cost"], 2), "passes": r["passes"], "trips": r["trips"]})

    by_plaza = []
    for r in conn.execute(
            f"SELECT t.road AS road, t.plaza AS plaza, COALESCE(SUM(t.amount), 0) AS cost, COUNT(*) AS passes, "
            f"COUNT(DISTINCT tr.id) AS trips {_PASS_FROM} WHERE {where} GROUP BY t.road, t.plaza ORDER BY cost DESC", params):
        label = f"{(r['plaza'] or '—').title()}" + (f" ({r['road']})" if r["road"] else "")
        by_plaza.append({"key": f"{r['road']}|{r['plaza']}", "label": label, "cost": round(r["cost"], 2),
                         "passes": r["passes"], "trips": r["trips"]})

    months = {r["m"]: {"cost": round(r["cost"], 2), "passes": r["passes"]} for r in conn.execute(
        f"SELECT substr(t.date, 1, 7) AS m, COALESCE(SUM(t.amount), 0) AS cost, COUNT(*) AS passes {_PASS_FROM} "
        f"WHERE {where} GROUP BY m ORDER BY m", params)}

    return {"cost": round(total["cost"], 2), "passes": total["passes"], "trips": total["trips"],
            "tags": tags, "untagged": untagged, "by_car": by_car, "by_plaza": by_plaza, "months": months,
            "groups": groups, "ungrouped_tags": ungrouped_tags, "tag_display_rows": tag_display_rows}


def _month_series(months: dict) -> list[str]:
    """Every month from the first to the last with data, so a quiet month shows as a zero column."""
    if not months:
        return []
    first, last = min(months), max(months)
    y, m = int(first[:4]), int(first[5:7])
    series = []
    while f"{y:04d}-{m:02d}" <= last:
        series.append(f"{y:04d}-{m:02d}")
        y, m = (y + 1, 1) if m == 12 else (y, m + 1)
    return series


def _period_range(mode: str, a: str, b: str) -> list[str]:
    """Every month ('YYYY-MM') or year ('YYYY') from a to b inclusive (a, b in any order), so a trend
    chart shows every period between the two picked, quiet ones included."""
    lo, hi = sorted([a, b])
    if mode == "year":
        return [str(y) for y in range(int(lo), int(hi) + 1)]
    y, m = int(lo[:4]), int(lo[5:7])
    yh, mh = int(hi[:4]), int(hi[5:7])
    out = []
    while (y, m) <= (yh, mh):
        out.append(f"{y:04d}-{m:02d}")
        y, m = (y + 1, 1) if m == 12 else (y, m + 1)
    return out


def _monthly_chart(months: dict):
    series = _month_series(months)
    if len(series) < 2:
        return None, []
    rows = [{"month": m, "cost": months.get(m, {}).get("cost", 0.0), "passes": months.get(m, {}).get("passes", 0)} for m in series]
    points = [{"label": r["month"], "value": r["cost"],
               "tip": f"{month_label(r['month'])}\nTolls ${r['cost']:,.2f}\n{r['passes']} pass{'es' if r['passes'] != 1 else ''}"} for r in rows]
    return column_chart(points, prefix="$", css_class="chart-cost", label="Tolls per month"), rows


_CLEAN_PASS = "x.deleted_at IS NULL AND x.review_status = 'clean' AND s.deleted_at IS NULL"


def _devices_for_user(conn, user_id: str) -> list[dict]:
    """Every tag found on a statement, with the car it is assigned to (if any) and its own figures."""
    rows = conn.execute(
        "SELECT d.*, c.name AS car_name, "
        f"(SELECT COUNT(*) FROM toll_transactions x JOIN toll_statements s ON s.id = x.statement_id WHERE x.device_ref = d.id AND {_CLEAN_PASS}) AS pass_count, "
        f"(SELECT COALESCE(SUM(x.amount), 0) FROM toll_transactions x JOIN toll_statements s ON s.id = x.statement_id WHERE x.device_ref = d.id AND {_CLEAN_PASS}) AS total, "
        f"(SELECT MIN(x.date) FROM toll_transactions x JOIN toll_statements s ON s.id = x.statement_id WHERE x.device_ref = d.id AND {_CLEAN_PASS}) AS first_date, "
        f"(SELECT MAX(x.date) FROM toll_transactions x JOIN toll_statements s ON s.id = x.statement_id WHERE x.device_ref = d.id AND {_CLEAN_PASS}) AS last_date, "
        "(SELECT COUNT(*) FROM toll_transactions x WHERE x.device_ref = d.id) AS any_rows "
        "FROM toll_devices d LEFT JOIN toll_cars c ON c.id = d.car_id WHERE d.user_id = ? ORDER BY d.id", (user_id,)).fetchall()
    return [{**dict(r), "label": device_label(r)} for r in rows]


def _cars_for_user(conn, user_id: str) -> list[dict]:
    """The person's cars, each with its tags and the figures of those tags together."""
    devices = _devices_for_user(conn, user_id)
    cars = []
    for r in conn.execute("SELECT * FROM toll_cars WHERE user_id = ? ORDER BY id", (user_id,)).fetchall():
        mine = [d for d in devices if d["car_id"] == r["id"]]
        firsts = [d["first_date"] for d in mine if d["first_date"]]
        lasts = [d["last_date"] for d in mine if d["last_date"]]
        cars.append({**dict(r), "label": r["name"], "devices": mine, "pass_count": sum(d["pass_count"] for d in mine),
                     "total": round(sum(d["total"] for d in mine), 2), "first_date": min(firsts, default=None),
                     "last_date": max(lasts, default=None)})
    return cars


# ------------------------------------------------------------------ cars

def _cars_redirect(current: User, acting: User, error: str = "", section: str = "cars") -> RedirectResponse:
    """Back to the Upload tab with the section that was just used ("cars" or "devices") left open; the
    two sections are collapsed otherwise."""
    return _redirect("tolls", current, acting, tab="upload", car_error=error, open=section)


@router.post("/toll-car-add")
def toll_car_add(
    request: Request,
    name: str = Form(""),
    current: User = Depends(get_current_user),
    acting: User = Depends(get_acting_user),
):
    """Create a car. A car is just a name; devices are assigned to it afterwards."""
    name = " ".join(name.split())[:60]
    if not name:
        return _cars_redirect(current, acting, "Give the car a name.")
    with get_db() as conn:
        if conn.execute("SELECT 1 FROM toll_cars WHERE user_id = ? AND name = ? COLLATE NOCASE", (acting.id, name)).fetchone():
            return _cars_redirect(current, acting, f"You already have a car called {name}.")
        conn.execute("INSERT INTO toll_cars (user_id, name) VALUES (?, ?)", (acting.id, name))
    return _cars_redirect(current, acting)


@router.post("/toll-car-update")
def toll_car_update(
    request: Request,
    id: int = Form(...),
    name: str = Form(""),
    current: User = Depends(get_current_user),
    acting: User = Depends(get_acting_user),
):
    """Rename a car (a blank name is ignored: a car always has one)."""
    name = " ".join(name.split())[:60]
    if not name:
        return _cars_redirect(current, acting, "A car needs a name.")
    with get_db() as conn:
        clash = conn.execute("SELECT 1 FROM toll_cars WHERE user_id = ? AND name = ? COLLATE NOCASE AND id != ?",
                             (acting.id, name, id)).fetchone()
        if clash:
            return _cars_redirect(current, acting, f"You already have a car called {name}.")
        conn.execute("UPDATE toll_cars SET name = ? WHERE id = ? AND user_id = ?", (name, id, acting.id))
    return _cars_redirect(current, acting)


@router.post("/toll-car-archive")
def toll_car_archive(
    request: Request,
    id: int = Form(...),
    archived: str = Form("1"),
    current: User = Depends(get_current_user),
    acting: User = Depends(get_acting_user),
):
    """Archive (archived=1) or bring back (archived=0) a car. An archived car drops out of the Cars list
    and the car pickers' main list; its passes still count in every total, and a later statement that
    still shows its devices is imported as usual."""
    with get_db() as conn:
        conn.execute("UPDATE toll_cars SET archived_at = " + ("datetime('now')" if archived == "1" else "NULL") +
                     " WHERE id = ? AND user_id = ?", (id, acting.id))
    return _cars_redirect(current, acting)


@router.post("/toll-car-delete")
def toll_car_delete(
    request: Request,
    id: int = Form(...),
    current: User = Depends(get_current_user),
    acting: User = Depends(get_acting_user),
):
    """Delete a car. Its devices are not deleted: they become "not assigned" and keep all their passes,
    so nothing is lost and the devices can be given to another car."""
    conn = get_db()
    try:
        car = conn.execute("SELECT id FROM toll_cars WHERE id = ? AND user_id = ?", (id, acting.id)).fetchone()
        if car is not None:
            conn.execute("UPDATE toll_devices SET car_id = NULL WHERE car_id = ? AND user_id = ?", (id, acting.id))
            conn.execute("DELETE FROM toll_cars WHERE id = ? AND user_id = ?", (id, acting.id))
            conn.commit()
            rebuild_trips(conn, acting.id)
    finally:
        conn.close()
    return _cars_redirect(current, acting)


@router.post("/toll-device-assign")
def toll_device_assign(
    request: Request,
    id: int = Form(...),
    car_id: str = Form(""),
    current: User = Depends(get_current_user),
    acting: User = Depends(get_acting_user),
):
    """Give a device to a car, move it to another car, or (blank car) take it off its car. The car must be
    one of the person's own. Trips are rebuilt so they regroup under the car."""
    conn = get_db()
    try:
        device = conn.execute("SELECT id FROM toll_devices WHERE id = ? AND user_id = ?", (id, acting.id)).fetchone()
        target = _int_or_none(car_id)
        if target is not None and conn.execute("SELECT 1 FROM toll_cars WHERE id = ? AND user_id = ?", (target, acting.id)).fetchone() is None:
            device = None
        if device is not None:
            conn.execute("UPDATE toll_devices SET car_id = ? WHERE id = ?", (target, id))
            conn.commit()
            rebuild_trips(conn, acting.id)
    finally:
        conn.close()
    return _cars_redirect(current, acting, section="devices")


@router.post("/toll-device-delete")
def toll_device_delete(
    request: Request,
    id: int = Form(...),
    current: User = Depends(get_current_user),
    acting: User = Depends(get_acting_user),
):
    """Remove a device that has no passes at all (say, left over after its only statement was purged).
    A device with passes stays: unassign it instead."""
    with get_db() as conn:
        used = conn.execute("SELECT 1 FROM toll_transactions WHERE device_ref = ? LIMIT 1", (id,)).fetchone()
        if used is None:
            conn.execute("DELETE FROM toll_devices WHERE id = ? AND user_id = ?", (id, acting.id))
    return _cars_redirect(current, acting, section="devices")


# ------------------------------------------------------------------ tag groups

def _groups_for_user(conn, user_id: str) -> list[dict]:
    """The person's tag groups in creation order (also the chart colour order)."""
    rows = conn.execute(
        "SELECT g.*, (SELECT COUNT(*) FROM toll_patterns p WHERE p.group_id = g.id) AS tag_count "
        "FROM toll_pattern_groups g WHERE g.user_id = ? ORDER BY g.id", (user_id,)).fetchall()
    return [dict(r) for r in rows]


@router.post("/toll-group-add")
def toll_group_add(
    request: Request,
    name: str = Form(""),
    period: str = Form("all"),
    current: User = Depends(get_current_user),
    acting: User = Depends(get_acting_user),
):
    """Create a group: several tags tracked as one thing on the Dashboard and Compare (e.g. "Work" made
    of a morning and an evening tag). A group is just a name; tags are assigned to it below."""
    name = " ".join(name.split())[:60]
    if not name:
        return _analyze_redirect(current, acting, period, "Give the group a name.")
    with get_db() as conn:
        if conn.execute("SELECT 1 FROM toll_pattern_groups WHERE user_id = ? AND name = ? COLLATE NOCASE", (acting.id, name)).fetchone():
            return _analyze_redirect(current, acting, period, f"You already have a group called {name}.")
        conn.execute("INSERT INTO toll_pattern_groups (user_id, name) VALUES (?, ?)", (acting.id, name))
    return _analyze_redirect(current, acting, period)


@router.post("/toll-group-update")
def toll_group_update(
    request: Request,
    id: int = Form(...),
    name: str = Form(""),
    period: str = Form("all"),
    current: User = Depends(get_current_user),
    acting: User = Depends(get_acting_user),
):
    """Rename a group (a blank name is ignored: a group always has one)."""
    name = " ".join(name.split())[:60]
    if not name:
        return _analyze_redirect(current, acting, period, "A group needs a name.")
    with get_db() as conn:
        clash = conn.execute("SELECT 1 FROM toll_pattern_groups WHERE user_id = ? AND name = ? COLLATE NOCASE AND id != ?",
                             (acting.id, name, id)).fetchone()
        if clash:
            return _analyze_redirect(current, acting, period, f"You already have a group called {name}.")
        conn.execute("UPDATE toll_pattern_groups SET name = ? WHERE id = ? AND user_id = ?", (name, id, acting.id))
    return _analyze_redirect(current, acting, period)


@router.post("/toll-group-delete")
def toll_group_delete(
    request: Request,
    id: int = Form(...),
    period: str = Form("all"),
    current: User = Depends(get_current_user),
    acting: User = Depends(get_acting_user),
):
    """Delete a group. Its tags keep tagging trips exactly as before; they just become ungrouped
    (toll_patterns.group_id -> NULL via the foreign key), same as deleting a car keeps its devices."""
    with get_db() as conn:
        conn.execute("DELETE FROM toll_pattern_groups WHERE id = ? AND user_id = ?", (id, acting.id))
    return _analyze_redirect(current, acting, period)


@router.post("/toll-pattern-group-assign")
def toll_pattern_group_assign(
    request: Request,
    id: int = Form(...),
    group_id: str = Form(""),
    period: str = Form("all"),
    current: User = Depends(get_current_user),
    acting: User = Depends(get_acting_user),
):
    """Put a tag in a group, move it to another group, or (blank) take it out of its group. A tag is in
    at most one group; a group can hold any number of tags."""
    with get_db() as conn:
        pattern = conn.execute("SELECT id FROM toll_patterns WHERE id = ? AND user_id = ?", (id, acting.id)).fetchone()
        target = _int_or_none(group_id)
        if target is not None and conn.execute(
                "SELECT 1 FROM toll_pattern_groups WHERE id = ? AND user_id = ?", (target, acting.id)).fetchone() is None:
            pattern = None
        if pattern is not None:
            conn.execute("UPDATE toll_patterns SET group_id = ? WHERE id = ?", (target, id))
    return _analyze_redirect(current, acting, period)


# ------------------------------------------------------------------ history

def _lower(text):
    return text.lower() if text else None


_STATEMENT_SORT_KEYS = {
    "file": lambda r: _lower(r["original_filename"]),
    "uploaded": lambda r: r["uploaded_at"],
    "period": lambda r: r["period_start_date"] or r["uploaded_at"],
    "tags": lambda r: r["car_count"],
    "passes": lambda r: r["pass_count"],
    "total": lambda r: r["passes_total"],
    "status": lambda r: r["status"],
}
STATEMENT_SORT_COLUMNS = [
    ("file", "File"), ("uploaded", "Uploaded"), ("period", "Period"), ("tags", "Devices"),
    ("passes", "Passes"), ("total", "Total tolls"), ("status", "Status"),
]
_TOLL_SORT_KEYS = {
    "when": lambda r: r["occurred_at"],
    "car": lambda r: _lower(r["car_name"]),
    "plaza": lambda r: _lower(r["plaza"]),
    "lane": lambda r: (r["lane"] or "", r["direction"] or ""),
    "amount": lambda r: r["amount"],
    "tag": lambda r: _lower(r["tag_name"]),
}
TOLL_SORT_COLUMNS = [("when", "Date and time"), ("car", "Car"), ("plaza", "Plaza"), ("lane", "Lane"), ("amount", "Amount"), ("tag", "Tag")]


def _history_rows(conn, user_id: str) -> list[dict]:
    rows = conn.execute(
        "SELECT s.*, "
        "(SELECT COUNT(*) FROM toll_transactions t WHERE t.statement_id = s.id AND t.deleted_at IS NULL) AS pass_count, "
        "(SELECT COUNT(DISTINCT t.device_ref) FROM toll_transactions t WHERE t.statement_id = s.id AND t.deleted_at IS NULL) AS car_count, "
        "(SELECT COALESCE(SUM(t.amount), 0) FROM toll_transactions t WHERE t.statement_id = s.id AND t.deleted_at IS NULL) AS passes_total "
        "FROM toll_statements s WHERE s.user_id = ? AND s.deleted_at IS NULL", (user_id,)).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        d["passes_total"] = round(d["passes_total"], 2)
        printed = d["total_tolls_printed"]
        d["printed_matches"] = printed is not None and abs(printed - d["passes_total"]) < 0.005
        out.append(d)
    return out


@router.get("/tolls")
def tolls_page(
    request: Request,
    tab: str = "dashboard",
    period: str = "all",
    car_id: str = "",
    tag: str = "",
    car_error: str = "",
    open_section: str = Query("", alias="open"),
    current: User = Depends(get_current_user),
    acting: User = Depends(get_acting_user),
):
    tab = tab if tab in ("upload", "dashboard") else "dashboard"
    acting_qs = _acting_qs(current, acting)
    context = {
        "user": acting, "tab": tab, "acting_qs": acting_qs, "acting_as_banner": _acting_banner(current, acting),
        "is_admin": current.is_admin, "active_page": "tolls",
    }

    conn = get_db()
    try:
        if tab == "upload":
            history = _history_rows(conn, acting.id)
            view = TableView(request, "tolls", "tu_", history, _STATEMENT_SORT_KEYS, "uploaded", extra_qs=acting_qs)
            context.update({
                "history_view": view, "history_columns": STATEMENT_SORT_COLUMNS, "statements": view.rows,
                "processing": any(r["status"] == "processing" for r in history),
                "cars": _cars_for_user(conn, acting.id), "devices": _devices_for_user(conn, acting.id),
                "car_error": car_error or None,
                "open_cars": open_section == "cars" or bool(car_error), "open_devices": open_section == "devices",
            })
            return templates.TemplateResponse(request, "tolls.html", context)

        safe_period, start, end, period_label = resolve_period(period, date.today())
        safe_car = _car_filter(car_id)
        months, years = available_periods(conn, acting.id)
        figures = toll_figures(conn, acting.id, start, end, safe_car)
        chart, chart_rows = _monthly_chart(figures["months"])

        # The toll list, filtered like the summary; `tag` narrows it to one tag or to untagged passes.
        where, params = _pass_where(acting.id, start, end, safe_car)
        tag_filter = ""
        if tag == "untagged":
            where += " AND tr.pattern_id IS NULL"
            tag_filter = "untagged"
        elif tag.startswith("g") and _int_or_none(tag[1:]) is not None:
            # A Dashboard group row links here with "g<id>": every tag in that group.
            where += " AND p.group_id = ?"
            params.append(int(tag[1:]))
            tag_filter = tag
        elif _int_or_none(tag) is not None:
            where += " AND tr.pattern_id = ?"
            params.append(int(tag))
            tag_filter = str(int(tag))
        rows = [dict(r) for r in conn.execute(
            f"SELECT t.id, t.occurred_at, t.road, t.plaza, t.lane, t.direction, t.amount, p.name AS tag_name, "
            f"c.name AS car_name, d.device_id, d.plate, d.plate_state {_PASS_FROM} WHERE {where}", params)]
        for r in rows:
            r["car_name"] = _vehicle_label(r)
        list_view = TableView(request, "tolls", "tl_", rows, _TOLL_SORT_KEYS, "when", extra_qs=acting_qs)
        pending = conn.execute("SELECT COUNT(*) AS n FROM toll_statements WHERE user_id = ? AND deleted_at IS NULL "
                               "AND status = 'pending_review'", (acting.id,)).fetchone()["n"]
        all_cars = _cars_for_user(conn, acting.id)
        unassigned = sum(1 for d in _devices_for_user(conn, acting.id) if d["car_id"] is None and d["pass_count"])
    finally:
        conn.close()

    base = {"tab": "dashboard", "period": safe_period}
    if safe_car is not None:
        base["car_id"] = safe_car
    context.update({
        "period": safe_period, "period_label": period_label, "months": months, "years": years,
        "month_labels": {m: month_label(m) for m in months}, "all_cars": all_cars, "selected_car_id": safe_car,
        "selected_car": ("Not assigned to a car" if safe_car == "none" else next((c["label"] for c in all_cars if c["id"] == safe_car), None)),
        "unassigned_devices": unassigned,
        "figures": figures, "chart": chart, "chart_rows": chart_rows,
        "list_view": list_view, "list_columns": TOLL_SORT_COLUMNS, "toll_rows": list_view.rows,
        "tag_filter": tag_filter, "pending_statements": pending,
        "tag_link_base": "tolls?" + urlencode(base) + ("&" + acting_qs if acting_qs else ""),
    })
    return templates.TemplateResponse(request, "tolls.html", context)


# ------------------------------------------------------------------ compare

def _default_periods(mode: str, today: date) -> tuple[str, str]:
    if mode == "year":
        return str(today.year - 1), str(today.year)
    from datetime import timedelta
    return (today.replace(day=1) - timedelta(days=1)).isoformat()[:7], today.isoformat()[:7]


def _trend_query_parts(user_id: str, mode: str, period_keys: list[str], car_id) -> tuple[str, list, str]:
    """(where, params, period bucket expression) covering every period in period_keys."""
    to_range = year_range if mode == "year" else month_range
    where, params = _pass_where(user_id, to_range(period_keys[0])[0], to_range(period_keys[-1])[1], car_id)
    bucket = "substr(t.date, 1, 4)" if mode == "year" else "substr(t.date, 1, 7)"
    return where, params, bucket


def _trend_periods(mode: str, period_keys: list[str], sums: dict) -> list[dict]:
    return [{"label": p, "tip_label": month_label(p) if mode == "month" else f"Year {p}", "values": sums[p]}
            for p in period_keys]


def _tag_group_series(conn, user_id: str, mode: str, period_keys: list[str], car_id) -> tuple[list[dict], list[dict]]:
    """The Compare trend chart's data: one series per group (creation order — colour follows identity,
    never a period's rank), plus "Other tagged" (ungrouped tags and any 6th+ group) and "Not tagged",
    summed for every period in period_keys. Returns (series, periods) ready for
    charts.stacked_column_chart(). One grouped SQL query covers the whole range."""
    if len(period_keys) < 2:
        return [], []
    where, params, bucket = _trend_query_parts(user_id, mode, period_keys, car_id)
    rows = conn.execute(
        f"SELECT {bucket} AS period, tr.pattern_id AS pattern_id, p.group_id AS group_id, "
        f"COALESCE(SUM(t.amount), 0) AS cost {_PASS_FROM} WHERE {where} "
        f"GROUP BY period, tr.pattern_id IS NULL, p.group_id", params).fetchall()

    all_groups = _groups_for_user(conn, user_id)
    series: list[dict] = []
    slot_by_group: dict[int, str] = {}
    for i, g in enumerate(all_groups):
        if i >= len(_GROUP_CHART_CLASSES):
            break
        key = f"g{g['id']}"
        series.append({"key": key, "label": g["name"], "css_class": _GROUP_CHART_CLASSES[i]})
        slot_by_group[g["id"]] = key
    series.append({"key": "other", "label": "Other tagged", "css_class": _OTHER_TAGGED_CLASS})
    series.append({"key": "none", "label": "Not tagged", "css_class": _NOT_TAGGED_CLASS})

    sums = {p: {s["key"]: 0.0 for s in series} for p in period_keys}
    for r in rows:
        p = r["period"]
        if p not in sums:
            continue
        if r["pattern_id"] is None:
            key = "none"
        else:
            key = slot_by_group.get(r["group_id"], "other")
        sums[p][key] = round(sums[p][key] + r["cost"], 2)

    return series, _trend_periods(mode, period_keys, sums)


def _car_series(conn, user_id: str, mode: str, period_keys: list[str], car_id) -> tuple[list[dict], list[dict]]:
    """Compare's "By car over time" data: like _tag_group_series but per car (creation order), with
    "Not assigned" for devices without a car and a 6th+ car folded into "Other cars"."""
    if len(period_keys) < 2:
        return [], []
    where, params, bucket = _trend_query_parts(user_id, mode, period_keys, car_id)
    rows = conn.execute(
        f"SELECT {bucket} AS period, d.car_id AS car_id, CASE WHEN d.car_id IS NULL THEN d.id END AS dev, "
        f"COALESCE(SUM(t.amount), 0) AS cost {_PASS_FROM} WHERE {where} "
        f"GROUP BY period, d.car_id, dev", params).fetchall()

    all_cars = _cars_for_user(conn, user_id)
    series: list[dict] = []
    slot_by_car: dict[int, str] = {}
    for i, c in enumerate(all_cars):
        if i >= len(_GROUP_CHART_CLASSES):
            break
        key = f"c{c['id']}"
        series.append({"key": key, "label": c["name"], "css_class": _GROUP_CHART_CLASSES[i]})
        slot_by_car[c["id"]] = key
    if len(all_cars) > len(_GROUP_CHART_CLASSES):
        series.append({"key": "other", "label": "Other cars", "css_class": _OTHER_TAGGED_CLASS})
    series.append({"key": "none", "label": "Not assigned", "css_class": _NOT_TAGGED_CLASS})

    sums = {p: {s["key"]: 0.0 for s in series} for p in period_keys}
    for r in rows:
        p = r["period"]
        if p not in sums:
            continue
        if r["car_id"] is None:
            key = "none"
        else:
            key = slot_by_car.get(r["car_id"])
            if key is None:
                key = "other" if "other" in sums[p] else None
        if key is None:
            continue
        sums[p][key] = round(sums[p][key] + r["cost"], 2)

    return series, _trend_periods(mode, period_keys, sums)


@router.get("/toll-compare")
def toll_compare(
    request: Request,
    mode: str = "month",
    a: str = "",
    b: str = "",
    car_id: str = "",
    current: User = Depends(get_current_user),
    acting: User = Depends(get_acting_user),
):
    mode = "year" if mode == "year" else "month"
    valid = _YEAR_RE if mode == "year" else _MONTH_RE
    default_a, default_b = _default_periods(mode, date.today())
    a = a if valid.match(a) else default_a
    b = b if valid.match(b) else default_b
    label = (lambda p: p) if mode == "year" else month_label
    safe_car = _car_filter(car_id)

    period_keys = _period_range(mode, a, b)

    with get_db() as conn:
        months, years = available_periods(conn, acting.id)
        all_cars = _cars_for_user(conn, acting.id)
        has_unassigned = any(d["car_id"] is None and d["pass_count"] for d in _devices_for_user(conn, acting.id))
        trend_series, trend_periods = _tag_group_series(conn, acting.id, mode, period_keys, safe_car)
        car_series, car_periods = _car_series(conn, acting.id, mode, period_keys, safe_car)

    options = sorted(set(years if mode == "year" else months) | {a, b}, reverse=True)
    trend_chart = (stacked_column_chart(trend_periods, trend_series, prefix="$", label="Tolls by group")
                   if len(trend_periods) >= 2 else None)
    trend_rows = [{"period": p["label"], "tip_label": p["tip_label"], "values": p["values"]} for p in trend_periods]
    car_chart = (stacked_column_chart(car_periods, car_series, prefix="$", label="Tolls by car")
                 if len(car_periods) >= 2 else None)
    car_rows = [{"period": p["label"], "tip_label": p["tip_label"], "values": p["values"]} for p in car_periods]
    return templates.TemplateResponse(request, "toll_compare.html", {
        "user": acting, "mode": mode, "a": a, "b": b, "label_a": label(a), "label_b": label(b),
        "options": [(o, label(o)) for o in options], "all_cars": all_cars, "selected_car_id": safe_car, "has_unassigned": has_unassigned,
        "trend_chart": trend_chart, "trend_series": trend_series, "trend_rows": trend_rows,
        "car_chart": car_chart, "car_series": car_series, "car_rows": car_rows,
        "acting_qs": _acting_qs(current, acting), "acting_as_banner": _acting_banner(current, acting),
        "is_admin": current.is_admin, "active_page": "tolls",
    })


# ------------------------------------------------------------------ analyze

_TRIP_SORT_KEYS = {
    "when": lambda r: r["started_at"],
    "car": lambda r: _lower(r["car_name"]),
    "route": lambda r: r["route"].lower(),
    "passes": lambda r: r["pass_count"],
    "cost": lambda r: r["amount"],
}
TRIP_SORT_COLUMNS = [("when", "Started"), ("car", "Car"), ("route", "Route"), ("passes", "Passes"), ("cost", "Cost")]


@router.get("/toll-analyze")
def toll_analyze(
    request: Request,
    period: str = "all",
    error: str = "",
    current: User = Depends(get_current_user),
    acting: User = Depends(get_acting_user),
):
    """Find regular trips (the same route at about the same time on several days a week), tag them,
    and tag or exclude single trips by hand. Suggestions use every car together and only trips that
    have no tag and no manual choice yet."""
    safe_period, start, end, _label = resolve_period(period, date.today())
    acting_qs = _acting_qs(current, acting)
    with get_db() as conn:
        months, years = available_periods(conn, acting.id)
        params: list = [acting.id]
        date_sql = ""
        if start:
            date_sql = " AND substr(tr.started_at, 1, 10) >= ? AND substr(tr.started_at, 1, 10) <= ?"
            params += [start, end]
        trips = [dict(r) for r in conn.execute(
            "SELECT tr.*, o.id AS override_id, o.pattern_id AS override_pattern "
            "FROM toll_trips tr "
            "LEFT JOIN toll_trip_overrides o ON o.user_id = tr.user_id AND o.trip_key = tr.trip_key "
            f"WHERE tr.user_id = ?{date_sql}", params)]
        patterns = [dict(r) for r in conn.execute(
            "SELECT * FROM toll_patterns WHERE user_id = ? AND ignored = 0 ORDER BY created_at, id", (acting.id,))]
        ignored = [dict(r) for r in conn.execute(
            "SELECT signature, window_start_min, window_end_min FROM toll_patterns WHERE user_id = ? AND ignored = 1", (acting.id,))]
        vehicle_names = {f"c{c['id']}": c["label"] for c in _cars_for_user(conn, acting.id)}
        vehicle_names.update({f"d{d['id']}": vehicle_label(None, d) for d in _devices_for_user(conn, acting.id)})
        groups = _groups_for_user(conn, acting.id)

    for t in trips:
        t["car_name"] = vehicle_names.get(t["vehicle"], "")
        t["route"] = pretty_signature(t["signature"])

    suggestions = suggest_regular([t for t in trips if t["pattern_id"] is None and t["override_id"] is None], ignored)
    for s in suggestions:
        s["cars"] = ", ".join(vehicle_names.get(v, "") for v in s["vehicles"])

    group_names = {g["id"]: g["name"] for g in groups}
    for p in patterns:
        mine = [t for t in trips if t["pattern_id"] == p["id"]]
        p.update({"trip_count": len(mine), "cost": round(sum(t["amount"] for t in mine), 2), "route": pretty_signature(p["signature"]),
                  "group_name": group_names.get(p["group_id"])})

    untagged = [t for t in trips if t["pattern_id"] is None]
    for t in untagged:
        t["state"] = "Marked not regular" if t["override_id"] is not None else ""
    view = TableView(request, "toll-analyze", "tt_", untagged, _TRIP_SORT_KEYS, "when", extra_qs=acting_qs)
    return templates.TemplateResponse(request, "toll_analyze.html", {
        "user": acting, "period": safe_period, "months": months, "years": years,
        "month_labels": {m: month_label(m) for m in months},
        "suggestions": suggestions, "min_suggest_trips": toll_trips_module.MIN_TRIPS, "patterns": patterns, "trip_view": view, "trip_columns": TRIP_SORT_COLUMNS,
        "trips_shown": view.rows, "trip_total": len(trips), "error": error or None, "groups": groups,
        "acting_qs": acting_qs, "acting_as_banner": _acting_banner(current, acting),
        "is_admin": current.is_admin, "active_page": "tolls",
    })


def _analyze_redirect(current: User, acting: User, period: str, error: str = "") -> RedirectResponse:
    return _redirect("toll-analyze", current, acting, period=period if period != "all" else "", error=error)


@router.post("/toll-pattern-save")
def toll_pattern_save(
    request: Request,
    id: str = Form(""),
    name: str = Form(""),
    signature: str = Form(""),
    start: str = Form(""),
    end: str = Form(""),
    period: str = Form("all"),
    current: User = Depends(get_current_user),
    acting: User = Depends(get_acting_user),
):
    """Tag a suggestion (no id: a new tag from signature + window) or edit an existing tag's name and window."""
    lo, hi = parse_clock(start), parse_clock(end)
    name = name.strip()[:60]
    if lo is None or hi is None or lo > hi or not name:
        return _analyze_redirect(current, acting, period, "Give the tag a name and a start time that is not after its end time.")
    with get_db() as conn:
        tag_id = _int_or_none(id)
        if tag_id is not None:
            conn.execute("UPDATE toll_patterns SET name = ?, window_start_min = ?, window_end_min = ? "
                         "WHERE id = ? AND user_id = ? AND ignored = 0", (name, lo, hi, tag_id, acting.id))
        elif signature.strip():
            conn.execute("INSERT INTO toll_patterns (user_id, name, signature, window_start_min, window_end_min) VALUES (?, ?, ?, ?, ?)",
                         (acting.id, name, signature.strip(), lo, hi))
        conn.commit()
        rebuild_trips(conn, acting.id)
    return _analyze_redirect(current, acting, period)


@router.post("/toll-pattern-delete")
def toll_pattern_delete(
    request: Request,
    id: int = Form(...),
    period: str = Form("all"),
    current: User = Depends(get_current_user),
    acting: User = Depends(get_acting_user),
):
    """Its trips become untagged (manual choices forcing this tag go with it)."""
    with get_db() as conn:
        conn.execute("DELETE FROM toll_patterns WHERE id = ? AND user_id = ? AND ignored = 0", (id, acting.id))
        conn.commit()
        rebuild_trips(conn, acting.id)
    return _analyze_redirect(current, acting, period)


@router.post("/toll-pattern-ignore")
def toll_pattern_ignore(
    request: Request,
    signature: str = Form(...),
    start: str = Form(""),
    end: str = Form(""),
    period: str = Form("all"),
    current: User = Depends(get_current_user),
    acting: User = Depends(get_acting_user),
):
    """Dismiss a suggestion: stored as an ignored pattern (no name) that never tags anything."""
    lo, hi = parse_clock(start), parse_clock(end)
    if lo is not None and hi is not None and signature.strip():
        with get_db() as conn:
            conn.execute("INSERT INTO toll_patterns (user_id, name, signature, window_start_min, window_end_min, ignored) "
                         "VALUES (?, NULL, ?, ?, ?, 1)", (acting.id, signature.strip(), lo, hi))
    return _analyze_redirect(current, acting, period)


@router.post("/toll-trip-tag")
def toll_trip_tag(
    request: Request,
    trip_key: str = Form(...),
    choice: str = Form(""),
    period: str = Form("all"),
    current: User = Depends(get_current_user),
    acting: User = Depends(get_acting_user),
):
    """A manual choice for one trip: an existing tag, 'none' (not regular), or '' (remove my choice)."""
    with get_db() as conn:
        trip = conn.execute("SELECT trip_key FROM toll_trips WHERE user_id = ? AND trip_key = ?", (acting.id, trip_key)).fetchone()
        first = conn.execute(
            "SELECT t.dedupe_key FROM toll_transactions t JOIN toll_trips tr ON tr.id = t.trip_id "
            "WHERE tr.user_id = ? AND tr.trip_key = ? ORDER BY t.occurred_at, t.id LIMIT 1", (acting.id, trip_key)).fetchone()
        if trip is not None and first is not None:
            if choice in ("", "auto"):
                conn.execute("DELETE FROM toll_trip_overrides WHERE user_id = ? AND trip_key = ?", (acting.id, trip_key))
            else:
                pattern_id = None
                if choice != "none":
                    owned = conn.execute("SELECT id FROM toll_patterns WHERE id = ? AND user_id = ? AND ignored = 0",
                                         (_int_or_none(choice), acting.id)).fetchone()
                    if owned is None:
                        return _analyze_redirect(current, acting, period, "That tag no longer exists.")
                    pattern_id = owned["id"]
                conn.execute(
                    "INSERT INTO toll_trip_overrides (user_id, trip_key, first_dedupe_key, pattern_id) VALUES (?, ?, ?, ?) "
                    "ON CONFLICT(user_id, trip_key) DO UPDATE SET pattern_id = excluded.pattern_id",
                    (acting.id, trip_key, first["dedupe_key"], pattern_id))
            conn.commit()
            rebuild_trips(conn, acting.id)
    return _analyze_redirect(current, acting, period)


