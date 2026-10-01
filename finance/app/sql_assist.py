"""Plain English → SQL with the AI model (App settings), for the Query tab (SPEC.md section 19.7).

One request at a time. Before anything is sent the app checks the model isn't reading an
uploaded PDF (and holds OLLAMA_LOCK for the whole request, so no PDF starts meanwhile),
then (Ollama only) says "hi" to make sure it answers and loads the model, then sends the question
with a description of the acting user's tables. The SQL that comes back is checked by
preparing it against that user's copy; if SQLite rejects it, the error goes back to the
model once for a fix. Nothing is run: the SQL is put in the editor for the admin to review.
"""
from __future__ import annotations

import json
import logging
import secrets
import threading
import time
from dataclasses import dataclass, field

from . import ai_client, ai_usage, jobs, query_engine as qe, settings
from .categorize import list_all_categories
from .db import get_db

logger = logging.getLogger(__name__)

HI_TIMEOUT = 120          # the first call may have to load the model
SQL_TIMEOUT = 300
KEEP_SECONDS = 1800
MAX_PROMPT_CHARS = 2000


class AssistError(Exception):
    pass


@dataclass
class AssistJob:
    runner_id: str
    acting_id: str
    question: str
    use_variables: bool
    id: str = field(default_factory=lambda: secrets.token_urlsafe(10))
    step: str = "Starting"
    started: float = field(default_factory=time.monotonic)
    finished: float | None = None
    sql: str = ""
    explanation: str = ""
    warning: str = ""
    error: str = ""
    raw: str = ""
    usage: list = field(default_factory=list)

    @property
    def running(self) -> bool:
        return self.finished is None

    @property
    def elapsed(self) -> float:
        return (self.finished or time.monotonic()) - self.started


_lock = threading.Lock()
_jobs: dict[str, AssistJob] = {}


def get(job_id: str) -> AssistJob | None:
    with _lock:
        now = time.monotonic()
        for k in [k for k, j in _jobs.items() if j.finished and now - j.finished > KEEP_SECONDS]:
            del _jobs[k]
        return _jobs.get(job_id)


def start(runner_id: str, acting_id: str, question: str, use_variables: bool) -> tuple[AssistJob | None, str]:
    """(job, "") or (None, why not). The work runs in its own thread."""
    question = " ".join(question.split())[:MAX_PROMPT_CHARS]
    if not question:
        return None, "Describe what you want first."
    if not settings.ai_configured():
        return None, "AI isn't set up: add the provider, address and model on Admin → App settings."
    with _lock:
        if any(j.running for j in _jobs.values()):
            return None, "Another SQL request is already being written. Wait for it to finish."
        job = AssistJob(runner_id=runner_id, acting_id=acting_id, question=question, use_variables=use_variables)
        _jobs[job.id] = job
    threading.Thread(target=_work, args=(job,), name="sql-assist", daemon=True).start()
    return job, ""


# ---- talking to the AI ------------------------------------------------------------------------------

def _generate(prompt: str, *, timeout: int, as_json: bool = False, num_predict: int | None = None,
              purpose: str = "Write SQL") -> str:
    """One request through ai_client, using the text model when App settings names one."""
    try:
        return ai_client.generate(prompt, model=settings.ai_text_model() or settings.ai_model(), want_json=as_json,
                                  timeout=timeout, max_tokens=num_predict, purpose=purpose).text
    except ai_client.AIError as err:
        raise AssistError(str(err)) from None


def _parse(text: str) -> tuple[str, str]:
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end == -1:
        raise AssistError("The model's answer didn't contain the JSON it was asked for.")
    try:
        data = json.loads(text[start:end + 1])
    except ValueError:
        raise AssistError("The model's answer wasn't valid JSON.") from None
    sql = str(data.get("sql") or "").strip()
    if sql.startswith("```"):
        sql = sql.strip("`").removeprefix("sql").strip()
    if not sql:
        raise AssistError("The model didn't return any SQL.")
    return sql, str(data.get("explanation") or "").strip()[:1000]


# ---- what the model is told ------------------------------------------------------------------------

def schema_text(acting_id: str) -> str:
    """The tables, columns, formats and conventions, from the same registry the Query tab uses."""
    overview = qe.table_overview(acting_id)
    with get_db() as conn:
        categories = list_all_categories(conn, acting_id)
        accounts = conn.execute("SELECT id, name, type FROM accounts WHERE user_id = ? AND deleted_at IS NULL "
                                "ORDER BY name COLLATE NOCASE", (acting_id,)).fetchall()
    lines = ["TABLES (SQLite). Every table holds only this person's live rows: never filter by user and never "
             "use deleted_at or user_id (those columns don't exist here)."]
    for t in overview:
        kind = "view" if t["is_view"] else "table"
        cols = ", ".join(f"{name} {typ}".strip() for name, typ in t["columns"])
        lines.append(f"- {t['name']} ({kind}, {t['count']} rows): {t['description']}\n  columns: {cols}")
    lines += [
        "",
        "CONVENTIONS",
        "- transactions.amount is signed: negative = money out (spending), positive = money in. "
        "For spending as a positive number use ROUND(-SUM(amount), 2).",
        "- Like the dashboard, leave out transfers and excluded rows: txn_type != 'transfer' AND is_excluded = 0.",
        "- txn_type is one of 'expense', 'income', 'transfer'.",
        "- Dates: transactions.date, post_date, utility period_start_date/period_end_date/due_date and "
        "toll_transactions.date are TEXT 'YYYY-MM-DD'; statements.statement_period is 'YYYY-MM'; "
        "toll_transactions.occurred_at and toll_trips.started_at/ended_at are 'YYYY-MM-DD HH:MM:SS' local time; "
        "created_at/uploaded_at/processed_at are UTC. Group by month with strftime('%Y-%m', date).",
        "- utility_bills.cost is positive; only rows with status IN ('complete', 'pending_review') are finished bills.",
        "- toll_transactions.amount is positive.",
        f"- Categories in use: {', '.join(categories)}." if categories else "",
        ("- Accounts: " + "; ".join(f"id {a['id']} = {a['name']} ({a['type']})" for a in accounts) + ".") if accounts else "",
    ]
    return "\n".join(line for line in lines if line is not None)


_VARIABLES = """VARIABLES
Where the question mentions an account, a period/date range or a category (or where a report would
naturally ask for one), use these named parameters, written so NULL means "all":
  (:account IS NULL OR account_id = :account)
  (:period_start IS NULL OR date >= :period_start) AND (:period_end IS NULL OR date <= :period_end)
  (:category IS NULL OR category = :category)
Other values the person should type in can be named parameters too, e.g. :merchant or :min_amount,
written the same NULL-tolerant way. Don't use date('now'); use the period parameters instead."""

_NO_VARIABLES = """Don't use named parameters (no :name placeholders); write literal values.
For "this month", "last year" etc. you may use date('now', ...) (it is UTC)."""


def build_prompt(schema: str, question: str, use_variables: bool) -> str:
    return (
        "You write one SQLite SELECT query for a personal-finance app.\n"
        "Rules: exactly one statement; SELECT or WITH only; SQLite syntax; use only the tables and columns "
        "listed below; give columns clear names with AS; round money to 2 decimals; add ORDER BY where it helps; "
        "no LIMIT unless the question asks for a top N.\n\n"
        f"{schema}\n\n{_VARIABLES if use_variables else _NO_VARIABLES}\n\n"
        f"QUESTION: {question}\n\n"
        'Answer with only a JSON object: {"sql": "<the query>", "explanation": "<one or two plain sentences>"}'
    )


def _fix_prompt(original: str, sql: str, error: str) -> str:
    return (f"{original}\n\nYour previous answer was:\n{sql}\n\nSQLite rejected it with this error:\n{error}\n\n"
            'Fix the query. Answer with only the JSON object {"sql": "...", "explanation": "..."}.')


def _check(job: AssistJob, sql: str) -> str | None:
    """None if SQLite accepts the SQL on this person's data (all parameters NULL), else the error."""
    try:
        qe.check_sql(sql)
        params = {name: None for name in qe.parameters(sql)}
        body = qe.strip_comments(sql).strip().rstrip(";")
        qe.execute(job.acting_id, f"SELECT * FROM ({body}) LIMIT 0", params)
        return None
    except qe.QueryError as err:
        return str(err)


# ---- the work ----------------------------------------------------------------------------------

def _work(job: AssistJob) -> None:
    with ai_usage.recording() as calls:
        job.usage = calls
        _work_inner(job)


def _work_inner(job: AssistJob) -> None:
    try:
        job.step = "Checking the AI isn't busy with a PDF"
        busy = jobs.busy_reason()
        if busy or not jobs.OLLAMA_LOCK.acquire(blocking=False):
            raise AssistError(f"The AI is busy — {busy or 'it is reading an uploaded PDF'}. "
                              "Try again when that finishes.")
        try:
            if settings.ai_provider() == "ollama":
                # Only Ollama has a model to load; a cloud provider would just charge for the hi.
                job.step = "Saying hi to Ollama (the first call can take a minute while the model loads)"
                try:
                    _generate("hi", timeout=HI_TIMEOUT, num_predict=8, purpose="Hi")
                except AssistError as err:
                    raise AssistError(f"Ollama didn't answer “hi” ({err}). Check it's running, then try again.") from None
            job.step = "Describing your tables"
            prompt = build_prompt(schema_text(job.acting_id), job.question, job.use_variables)
            job.step = "Writing SQL"
            job.raw = _generate(prompt, timeout=SQL_TIMEOUT, as_json=True)
            sql, explanation = _parse(job.raw)
            job.step = "Checking the SQL"
            problem = _check(job, sql)
            if problem:
                job.step = "Fixing an error in the SQL"
                job.raw = _generate(_fix_prompt(prompt, sql, problem), timeout=SQL_TIMEOUT, as_json=True, purpose="Fix SQL")
                sql2, explanation2 = _parse(job.raw)
                problem2 = _check(job, sql2)
                if problem2 is None or problem2 != problem:
                    sql, explanation, problem = sql2, explanation2 or explanation, problem2
                if problem:
                    job.warning = f"SQLite still reports a problem: {problem}"
            job.sql, job.explanation = sql, explanation
        finally:
            jobs.OLLAMA_LOCK.release()
    except AssistError as err:
        job.error = str(err)
    except Exception:
        logger.exception("SQL assist failed")
        job.error = "Something went wrong writing the SQL. Try again."
    finally:
        job.finished = time.monotonic()


def reset() -> None:
    """Tests only."""
    with _lock:
        _jobs.clear()
