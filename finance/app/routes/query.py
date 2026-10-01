"""Storage → Query tab: read-only SQL for the admin, on the acting user's data (SPEC.md section 19)."""
import asyncio
from datetime import date
from pathlib import Path

from fastapi import APIRouter, Depends, Request
from fastapi.responses import Response
from fastapi.templating import Jinja2Templates

from ..auth import User, get_acting_user, require_admin
from ..categorize import list_all_categories
from ..db import get_db
from ..version import APP_VERSION
from .. import ai_usage, long_runs, query_engine as qe, sql_assist, report_charts, report_core as rc, report_vars as rv, saved_reports
from .accounts import _acting_banner, _acting_qs

router = APIRouter()
templates = Jinja2Templates(directory=str(Path(__file__).parent.parent / "templates"))
templates.env.globals["app_version"] = APP_VERSION
templates.env.globals["ai_usage_view"] = ai_usage.view


def input_choices(acting: User) -> dict:
    """Accounts and categories for variable inputs: always the acting user's own."""
    with get_db() as conn:
        accounts = conn.execute(
            "SELECT id, name, account_number_last4 FROM accounts WHERE user_id = ? AND deleted_at IS NULL "
            "ORDER BY is_archived, name COLLATE NOCASE", (acting.id,)).fetchall()
        categories = list_all_categories(conn, acting.id)
    return {"accounts": [dict(a) for a in accounts], "categories": categories}


def _form_dict(form) -> dict:
    return {k: form.get(k) for k in form.keys()}


def _variables(form: dict, sql: str) -> tuple[list[rv.Variable], list[str]]:
    shown = [n for n in (form.get("var_names") or "").split(",") if n]
    known = rv.definitions_from_form(form, shown)
    report_id = form.get("report_id")
    if report_id and not shown:
        report = saved_reports.get(int(report_id)) if str(report_id).isdigit() else None
        if report:
            known = report["variables"]
    return rv.detect(sql, known)


def _context(request, current: User, acting: User, **extra) -> dict:
    return {"user": acting, "acting": acting, "is_admin": current.is_admin, "acting_qs": _acting_qs(current, acting),
            "acting_as_banner": _acting_banner(current, acting), "active_page": "admin_storage", **extra}


def query_tab(request: Request, current: User, acting: User, report_id: str = ""):
    """GET admin-storage?tab=query[&report=N] (admin only; wired from routes/admin_storage.py)."""
    report = saved_reports.get(int(report_id)) if report_id.isdigit() else None
    overview = qe.table_overview(acting.id)
    sql = report["sql"] if report else ""
    variables = report["variables"] if report else []
    ctx = _context(request, current, acting, tab="query", overview=overview, report=report, sql=sql,
                   variables=variables, values={}, choices=input_choices(acting), var_types=rv.TYPES,
                   type_labels=rv.TYPE_LABELS, presets=rv.PERIOD_PRESETS, page_sizes=rc.PAGE_SIZES,
                   unused=[], chart_types=saved_reports.CHART_TYPES)
    return templates.TemplateResponse(request, "storage_query.html", ctx)


@router.post("/query-vars")
async def query_vars(request: Request, current: User = Depends(require_admin), acting: User = Depends(get_acting_user)):
    form = _form_dict(await request.form())
    sql = form.get("sql") or ""
    (variables, unused), choices = await asyncio.to_thread(lambda: (_variables(form, sql), input_choices(acting)))
    values = {v.name: rv.form_value(form, v) for v in variables}
    return templates.TemplateResponse(request, "_query_vars.html", _context(
        request, current, acting, variables=variables, values=values, unused=unused, choices=choices,
        var_types=rv.TYPES, type_labels=rv.TYPE_LABELS, presets=rv.PERIOD_PRESETS, editing=True))


def _results(request, current, acting, *, result=None, error=None, view_state=None, form=None):
    ctx = _context(request, current, acting, error=error, result=result, data_owner=acting.name)
    qs = _acting_qs(current, acting)
    if result is not None:
        st = view_state or {}
        view = rc.ResultView(result, page=st.get("page", 1), per_page=st.get("per_page", rc.DEFAULT_PAGE_SIZE),
                             sort=st.get("sort"), desc=st.get("dir") == "desc",
                             skip_totals=[s for s in (form or {}).get("no_total", "").split(",")],
                             totals_on=(form or {}).get("totals_off") != "1",
                             link=rc.htmx_link("query-view" + (f"?{qs}" if qs else ""), "#query-form", {"rid": result.id}))
        svg, note = report_charts.render(result, saved_reports.chart_from_form(form or {}))
        ctx.update(chart_svg=svg, chart_note=note, view=view, csv_href=f"query-csv?rid={result.id}&sort={view.state()['sort']}&dir={view.state()['dir']}"
                   + (f"&{qs}" if qs else ""), page_sizes=rc.PAGE_SIZES)
    return templates.TemplateResponse(request, "_query_results.html", ctx)


def _bound(acting: User, form: dict) -> tuple[str, dict]:
    sql = form.get("sql") or ""
    qe.check_sql(sql)
    variables, _unused = _variables(form, sql)
    with get_db() as conn:
        ids = {r[0] for r in conn.execute("SELECT id FROM accounts WHERE user_id = ? AND deleted_at IS NULL", (acting.id,))}
    params = rv.bind(variables, form, ids, date.today())
    return sql, {k: v for k, v in params.items() if k in set(qe.parameters(sql))}


async def _run(current: User, acting: User, form: dict) -> qe.Result:
    sql, params = await asyncio.to_thread(_bound, acting, form)
    result = await asyncio.to_thread(qe.execute, acting.id, sql, params)
    return qe.remember(result, current.id, acting.id, rv.query_key(sql, params, True))


def elapsed_text(seconds: float) -> str:
    s = int(seconds)
    return f"{s // 60} min {s % 60} s" if s >= 60 else f"{s} s"


def long_run_fragment(request, ctx: dict, *, run=None, busy=None, user_id="", poll_url="", cancelled="", poll_form=""):
    ctx = dict(ctx, run=run, busy=busy, poll_url=poll_url, cancelled=cancelled, poll_form=poll_form,
               busy_mine=bool(busy and busy.runner_id == user_id),
               elapsed_text=elapsed_text(run.elapsed) if run else "",
               busy_elapsed=elapsed_text(busy.elapsed) if busy else "")
    return templates.TemplateResponse(request, "_long_run.html", ctx)


@router.post("/query-run")
async def query_run(request: Request, current: User = Depends(require_admin), acting: User = Depends(get_acting_user)):
    form = _form_dict(await request.form())
    try:
        if form.get("no_time_limit") == "1":
            sql, params = await asyncio.to_thread(_bound, acting, form)
            run, busy = long_runs.start(current.id, acting.id, sql, params, rv.query_key(sql, params, False),
                                        "Query tab")
            qs = _acting_qs(current, acting)
            poll = "query-long-status?" + "&".join(x for x in (f"run={run.id}" if run else "", qs) if x)
            return long_run_fragment(request, _context(request, current, acting), run=run, busy=busy,
                                     user_id=current.id, poll_url=poll, poll_form="#query-form")
        result = await _run(current, acting, form)
    except qe.QueryError as err:
        return _results(request, current, acting, error=str(err))
    return _results(request, current, acting, result=result, form=form,
                    view_state={"per_page": form.get("per_page")})


@router.post("/query-long-status")
async def query_long_status(request: Request, run: str = "", current: User = Depends(require_admin),
                            acting: User = Depends(get_acting_user)):
    form = _form_dict(await request.form())
    lr = long_runs.get(run)
    if lr is None or lr.runner_id != current.id:
        return _results(request, current, acting, error="That run is no longer available. Run the query again.")
    if lr.running:
        qs = _acting_qs(current, acting)
        return long_run_fragment(request, _context(request, current, acting), run=lr, user_id=current.id,
                                 poll_url=f"query-long-status?run={lr.id}" + (f"&{qs}" if qs else ""), poll_form="#query-form")
    if lr.error:
        return _results(request, current, acting, error=lr.error)
    result = qe.recall(lr.result_id, current.id, acting.id)
    if result is None:
        return _results(request, current, acting, error="That result has expired. Run the query again.")
    return _results(request, current, acting, result=result, form=form, view_state={"per_page": form.get("per_page")})


@router.post("/query-view")
async def query_view(request: Request, current: User = Depends(require_admin), acting: User = Depends(get_acting_user)):
    """Page, page size or sort of a kept result; re-runs only if the result has expired."""
    form = _form_dict(await request.form())
    result = qe.recall(form.get("rid", ""), current.id, acting.id)
    if result is None and form.get("no_time_limit") == "1":
        return _results(request, current, acting, error="That result has expired. Press Run to run the query again.")
    if result is None:
        try:
            result = await _run(current, acting, form)
        except qe.QueryError as err:
            return _results(request, current, acting, error=str(err))
    return _results(request, current, acting, result=result, form=form,
                    view_state={k: form.get(k) for k in ("page", "per_page", "sort", "dir")})


@router.get("/query-csv")
def query_csv(rid: str = "", sort: str = "", dir: str = "asc",
              current: User = Depends(require_admin), acting: User = Depends(get_acting_user)):
    result = qe.recall(rid, current.id, acting.id)
    if result is None:
        return Response("That result has expired. Run the query again, then download.", status_code=410,
                        media_type="text/plain")
    return Response(rc.csv_bytes(result, sort, dir == "desc"), media_type="text/csv", headers={
        "Content-Disposition": f'attachment; filename="{rc.csv_filename("query", date.today().isoformat())}"'})


@router.post("/query-save")
async def query_save(request: Request, current: User = Depends(require_admin), acting: User = Depends(get_acting_user)):
    """Save the editor as a report (new, or update report_id)."""
    form = _form_dict(await request.form())
    sql = form.get("sql") or ""
    try:
        qe.check_sql(sql)
        variables, _unused = await asyncio.to_thread(_variables, form, sql)
        report_id = await asyncio.to_thread(lambda: saved_reports.save(
            report_id=int(form["report_id"]) if str(form.get("report_id") or "").isdigit() else None,
            name=form.get("name") or "", description=form.get("description") or "", sql=sql,
            variables=variables, totals={"on": form.get("totals_off") != "1",
                                         "skip": [s.strip() for s in (form.get("no_total") or "").split(",") if s.strip()]},
            chart=saved_reports.chart_from_form(form), no_time_limit=form.get("no_time_limit") == "1"))
    except (qe.QueryError, saved_reports.ReportError) as err:
        return templates.TemplateResponse(request, "_query_save_result.html", _context(
            request, current, acting, ok=False, message=str(err)))
    qs = _acting_qs(current, acting)
    return templates.TemplateResponse(request, "_query_save_result.html", _context(
        request, current, acting, ok=True, report_id=report_id, message="Saved.",
        report_href=f"report?id={report_id}" + (f"&{qs}" if qs else "")))



# ---- plain English → SQL (sql_assist.py) ------------------------------------------------------------

def _assist(request, current, acting, **extra):
    job = extra.get("job")
    return templates.TemplateResponse(request, "_sql_assist.html", _context(
        request, current, acting, elapsed_text=elapsed_text(job.elapsed) if job else "", **extra))


@router.post("/sql-assist-start")
async def sql_assist_start(request: Request, current: User = Depends(require_admin), acting: User = Depends(get_acting_user)):
    form = _form_dict(await request.form())
    job, why = sql_assist.start(current.id, acting.id, form.get("ai_question") or "", form.get("ai_vars") == "1")
    if job is None:
        return _assist(request, current, acting, message=why, retry=bool(form.get("ai_question")))
    return _assist(request, current, acting, job=job)


@router.get("/sql-assist-status")
def sql_assist_status(request: Request, job: str = "", current: User = Depends(require_admin),
                      acting: User = Depends(get_acting_user)):
    found = sql_assist.get(job)
    if found is None or found.runner_id != current.id:
        return _assist(request, current, acting, message="That request is no longer available.", retry=True)
    return _assist(request, current, acting, job=found)
