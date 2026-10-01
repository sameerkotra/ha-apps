"""Reports page: everyone runs saved reports on their own data; only the admin changes them (SPEC.md section 19.7)."""
import asyncio
import logging
from datetime import date
from pathlib import Path
from urllib.parse import urlencode

from fastapi import APIRouter, Depends, Request
from fastapi.responses import RedirectResponse, Response
from fastapi.templating import Jinja2Templates
from markupsafe import escape

from ..auth import User, get_acting_user, get_current_user, require_admin
from ..db import get_db
from ..version import APP_VERSION
from .. import long_runs, query_engine as qe, report_charts, report_core as rc, report_vars as rv, saved_reports
from .accounts import _acting_banner, _acting_qs
from .query import input_choices, long_run_fragment

logger = logging.getLogger(__name__)
router = APIRouter()
templates = Jinja2Templates(directory=str(Path(__file__).parent.parent / "templates"))
templates.env.globals["app_version"] = APP_VERSION

FAILED_FOR_USERS = "This report couldn't run — ask the admin to check it."


def _ctx(current: User, acting: User, **extra) -> dict:
    return {"user": acting, "acting_qs": _acting_qs(current, acting), "acting_as_banner": _acting_banner(current, acting),
            "is_admin": current.is_admin, "active_page": "reports", **extra}


EXPIRED = "That result has expired. Press Run to run the report again."


def _account_ids(user_id: str) -> set[int]:
    with get_db() as conn:
        return {r[0] for r in conn.execute("SELECT id FROM accounts WHERE user_id = ? AND deleted_at IS NULL", (user_id,))}


def _load(report_id: str, acting: User, with_choices: bool = True):
    """Blocking: the report, the acting user's input choices and account ids (run in a thread)."""
    report = saved_reports.get(int(report_id)) if report_id and report_id.isdigit() else None
    if report is None:
        return None, None, None
    return report, (input_choices(acting) if with_choices else None), _account_ids(acting.id)


def _not_found(request, acting):
    return templates.TemplateResponse(request, "not_found.html", {"user": acting}, status_code=404)


def _key(report: dict, params: dict) -> str:
    return rv.query_key(f"report:{report['id']}:{report['updated_at']}", params, not report["no_time_limit"])


def _params(report: dict, form, account_ids: set[int]) -> dict:
    params = rv.bind(report["variables"], form, account_ids, date.today())
    return {k: v for k, v in params.items() if k in set(qe.parameters(report["sql"]))}


def _message(err: qe.QueryError, current: User, report: dict, acting: User) -> str:
    """Admins see the real error; others see input problems but never SQLite details."""
    if current.is_admin or isinstance(err, rv.InputError) or str(err) == EXPIRED:
        return str(err)
    logger.warning("Report %s failed for %s: %s", report["id"], acting.id, err)
    return FAILED_FOR_USERS


async def run_report(report: dict, current: User, acting: User, form, account_ids: set[int], *,
                     reuse: str = "") -> qe.Result:
    """The kept result for this report and these values, or a fresh run. A no-time-limit report
    never runs here (it only runs in the background slot); its expired result raises EXPIRED."""
    params = _params(report, form, account_ids)
    key = _key(report, params)
    kept = qe.recall(reuse, current.id, acting.id, key)
    if kept is not None:
        return kept
    if report["no_time_limit"]:
        raise qe.QueryError(EXPIRED)
    result = await asyncio.to_thread(qe.execute, acting.id, report["sql"], params)
    await asyncio.to_thread(saved_reports.mark_run, report["id"])
    return qe.remember(result, current.id, acting.id, key)


def _link_base(report: dict, form, acting_qs: str) -> dict:
    base = {"id": report["id"]}
    base.update({k: v for k, v in form.items() if k.startswith("v_")})
    if acting_qs:
        base["as_user"] = acting_qs.split("=", 1)[1]
    return base


@router.get("/reports")
def reports_list(request: Request, current: User = Depends(get_current_user), acting: User = Depends(get_acting_user)):
    return templates.TemplateResponse(request, "reports_list.html", _ctx(current, acting, reports=saved_reports.all_reports()))


@router.get("/report")
async def report_page(request: Request, id: str = "", run: str = "", page: str = "1", per_page: str = "",
                      sort: str = "", dir: str = "asc", rid: str = "",
                      current: User = Depends(get_current_user), acting: User = Depends(get_acting_user)):
    report, choices, account_ids = await asyncio.to_thread(_load, id, acting)
    if report is None:
        return _not_found(request, acting)
    form = dict(request.query_params)
    acting_qs = _acting_qs(current, acting)
    values = {v.name: rv.form_value(form, v) for v in report["variables"]}
    ctx = _ctx(current, acting, report=report, values=values, choices=choices,
               presets=rv.PERIOD_PRESETS, page_sizes=rc.PAGE_SIZES, data_owner=acting.name)
    render = lambda: templates.TemplateResponse(request, "report_run.html", ctx)
    # A normal report runs as soon as it opens (with its defaults). A no-time-limit one waits for Run,
    # then runs in the background slot while this page polls; a link from another site never starts one.
    if report["no_time_limit"]:
        try:
            params = _params(report, form, account_ids)
        except qe.QueryError as err:
            ctx["error"] = _message(err, current, report, acting)
            return render()
        kept = qe.recall(rid, current.id, acting.id, _key(report, params))
        if kept is None:
            if run == "1" and request.headers.get("sec-fetch-site") != "cross-site":
                lr, busy = long_runs.start(current.id, acting.id, report["sql"], params, _key(report, params),
                                           report["name"], on_done=lambda: saved_reports.mark_run(report["id"]))
                back = "report?" + _urlencode({**_link_base(report, form, acting_qs), "run": "1", "per_page": per_page})
                ctx["long_run"] = long_run_fragment(
                    request, ctx, run=lr, busy=busy, user_id=current.id,
                    poll_url="report-long-status?" + _urlencode({"run": lr.id if lr else "", "back": back})).body.decode()
            elif rid:
                ctx["error"] = EXPIRED
            return render()
    try:
        result = await run_report(report, current, acting, form, account_ids, reuse=rid)
    except qe.QueryError as err:
        ctx["error"] = _message(err, current, report, acting)
        return render()
    base = _link_base(report, form, acting_qs)
    view = rc.ResultView(result, page=page, per_page=per_page or rc.DEFAULT_PAGE_SIZE, sort=sort,
                         desc=dir == "desc", skip_totals=report["totals"]["skip"],
                         totals_on=report["totals"]["on"],
                         link=rc.get_link("report", {**base, "rid": result.id}))
    svg, note = report_charts.render(result, report["chart"])
    st = view.state()
    ctx.update(view=view, chart_svg=svg, chart_note=note, csv_href="report-csv?" + _urlencode(
        {**base, "rid": result.id, "sort": st["sort"], "dir": st["dir"]}))
    return render()


def _urlencode(params: dict) -> str:
    return urlencode({k: v for k, v in params.items() if v not in ("", None)})


@router.get("/report-csv")
async def report_csv(request: Request, id: str = "", rid: str = "", sort: str = "", dir: str = "asc",
                     current: User = Depends(get_current_user), acting: User = Depends(get_acting_user)):
    report, _choices, account_ids = await asyncio.to_thread(_load, id, acting, False)
    if report is None:
        return _not_found(request, acting)
    try:
        result = await run_report(report, current, acting, dict(request.query_params), account_ids, reuse=rid)
    except qe.QueryError as err:
        status = 410 if str(err) == EXPIRED else 400
        return Response(_message(err, current, report, acting), status_code=status, media_type="text/plain")
    return Response(rc.csv_bytes(result, sort, dir == "desc"), media_type="text/csv", headers={
        "Content-Disposition": f'attachment; filename="{rc.csv_filename(report["name"], date.today().isoformat())}"'})


def _back(current, acting, **extra) -> RedirectResponse:
    qs = _acting_qs(current, acting)
    params = {**({"as_user": qs.split("=", 1)[1]} if qs else {}), **extra}
    return RedirectResponse("reports" + ("?" + _urlencode(params) if params else ""), status_code=303)


@router.post("/report-duplicate")
def report_duplicate(id: str = "", current: User = Depends(require_admin), acting: User = Depends(get_acting_user)):
    new_id = saved_reports.duplicate(int(id)) if id.isdigit() else None
    qs = _acting_qs(current, acting)
    if new_id is None:
        return _back(current, acting)
    return RedirectResponse(f"admin-storage?tab=query&report={new_id}" + (f"&{qs}" if qs else ""), status_code=303)


@router.post("/report-delete")
def report_delete(id: str = "", current: User = Depends(require_admin), acting: User = Depends(get_acting_user)):
    if id.isdigit():
        saved_reports.delete(int(id))
    return _back(current, acting, deleted="1")


@router.post("/reports-check")
async def reports_check(request: Request, current: User = Depends(require_admin), acting: User = Depends(get_acting_user)):
    """Runs every report with its default values on the acting user's data and lists any that fail."""
    rows = []
    reports, account_ids = await asyncio.to_thread(lambda: (saved_reports.all_reports(), _account_ids(acting.id)))
    for report in reports:
        try:
            if report["no_time_limit"]:
                rows.append({"report": report, "ok": None, "message": "Skipped: no time limit (run it by hand)."})
                continue
            params = _params(report, {}, account_ids)
            result = await asyncio.to_thread(qe.execute, acting.id, report["sql"], params)
            rows.append({"report": report, "ok": True, "message": f"{len(result.rows):,} rows"})
        except qe.QueryError as err:
            rows.append({"report": report, "ok": False, "message": str(err)})
    return templates.TemplateResponse(request, "reports_check.html", _ctx(current, acting, rows=rows))


@router.get("/report-long-status")
def report_long_status(request: Request, run: str = "", back: str = "",
                       current: User = Depends(get_current_user), acting: User = Depends(get_acting_user)):
    lr = long_runs.get(run)
    if not back.startswith("report?"):
        back = "reports"
    if lr is None or lr.runner_id != current.id:
        return Response('<div class="q-long-run">That run is no longer available. Press Run again.</div>', media_type="text/html")
    if lr.running:
        return long_run_fragment(request, _ctx(current, acting), run=lr, user_id=current.id,
                                 poll_url="report-long-status?" + _urlencode({"run": lr.id, "back": back}))
    if lr.error:
        message = lr.error if current.is_admin or lr.cancelled else FAILED_FOR_USERS
        return Response(f'<div class="q-error" role="alert">{escape(message)}</div>', media_type="text/html")
    return Response("", headers={"HX-Redirect": back + "&rid=" + lr.result_id})


@router.post("/long-run-cancel")
def long_run_cancel(request: Request, run: str = "", current: User = Depends(get_current_user),
                    acting: User = Depends(get_acting_user)):
    ok = long_runs.cancel(run, current.id, current.is_admin)
    return long_run_fragment(request, _ctx(current, acting), user_id=current.id,
                             cancelled="Cancelled." if ok else "That run had already finished.")
