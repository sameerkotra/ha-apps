"""AI usage (tokens, time, cost): per request for the debug pages, and the running total on
Admin → AI usage (SPEC.md section 21).

Every call to the model reports what it used to whatever is recording on the current thread:
a background job, a CSV import or a debug re-send wraps its work in `recording()` and stores the
list on its row (ai_usage JSON) — that row keeps only its last run. Every call is also added to
the ai_usage_log table, the cumulative total, which a re-run or a delete never lowers. Tokens are
as each provider reports them (Ollama prompt_eval_count / eval_count, OpenAI
usage.prompt_tokens / completion_tokens, Claude usage.input_tokens / output_tokens).
"""
from __future__ import annotations

import json
import logging
import sqlite3
import threading
import time
from contextlib import contextmanager

from . import db, settings

logger = logging.getLogger(__name__)
_local = threading.local()

# Requests not yet written to ai_usage_log. add() never writes from the caller's thread: the caller
# may itself be in the middle of a database transaction (a CSV import categorizes before it commits),
# so a short-lived background thread writes them as soon as the database is free. One that can't be
# written yet (an imported backup whose migrations haven't run) is retried with the next request or
# when Admin → AI usage is opened.
_pending: list[tuple] = []
_pending_lock = threading.Lock()      # guards _pending
_write_lock = threading.Lock()        # one writer at a time, so nothing is written twice
_writer: threading.Thread | None = None
WRITE_TIMEOUT = 30.0                  # seconds the background writer waits for a busy database
WRITE_ATTEMPTS = 20
RETRY_PAUSE = 3.0


@contextmanager
def recording():
    previous = getattr(_local, "calls", None)
    _local.calls = []
    try:
        yield _local.calls
    finally:
        _local.calls = previous


def add(purpose: str, model: str, input_tokens: int | None, output_tokens: int | None, seconds: float,
        error: str = "", provider: str = "") -> None:
    """Record one request (ai_client.generate calls this for every provider): in the running total
    always, and on whatever is recording on this thread."""
    entry = {
        "purpose": purpose, "model": model, "input_tokens": input_tokens, "output_tokens": output_tokens,
        "seconds": round(seconds or 0, 2), "error": (error or "")[:200],
    }
    with _pending_lock:
        _pending.append((purpose or "", provider or "", model or "", input_tokens, output_tokens,
                         entry["seconds"], entry["error"]))
    _start_writer()
    calls = getattr(_local, "calls", None)
    if calls is not None:
        calls.append(entry)


def _start_writer() -> None:
    global _writer
    with _pending_lock:
        if _writer is not None and _writer.is_alive():
            return
        _writer = threading.Thread(target=_write_in_background, name="ai-usage-log", daemon=True)
        _writer.start()


def _write_in_background() -> None:
    for _ in range(WRITE_ATTEMPTS):
        if flush(WRITE_TIMEOUT):
            return
        time.sleep(RETRY_PAUSE)


def flush(timeout: float = 2.0) -> bool:
    """Write the pending requests to ai_usage_log. Never raises: on a failure they stay pending.
    Returns True when nothing is left pending."""
    if not _write_lock.acquire(timeout=timeout + 1):
        return False
    try:
        with _pending_lock:
            rows = list(_pending)
        if not rows:
            return True
        try:
            conn = sqlite3.connect(db.DB_PATH, timeout=timeout)
            try:
                with conn:
                    conn.executemany(
                        "INSERT INTO ai_usage_log (purpose, provider, model, input_tokens, output_tokens, seconds, error) "
                        "VALUES (?, ?, ?, ?, ?, ?, ?)", rows)
            finally:
                conn.close()
        except sqlite3.Error as e:
            logger.info("AI usage not added to the running total yet (%s); will retry", e)
            with _pending_lock:
                del _pending[:-1000]      # never grows without bound
            return False
        with _pending_lock:
            del _pending[:len(rows)]
            return not _pending
    finally:
        _write_lock.release()


def _purpose_group(purpose: str) -> str:
    """"Extraction (attempt 2)" counts with "Extraction"."""
    return (purpose or "Other").split(" (attempt", 1)[0]


def totals() -> dict | None:
    """The running total for Admin → AI usage: overall, by month, by kind of request and by model,
    with cost at today's prices. None while the database has no ai_usage_log yet (an imported
    backup before its migrations ran)."""
    flush()
    try:
        conn = db.get_db()
        try:
            rows = conn.execute(
                "SELECT substr(at, 1, 7) AS month, purpose, model, earlier, COUNT(*) AS n, "
                "SUM(error != '') AS failed, COALESCE(SUM(input_tokens), 0) AS input_tokens, "
                "COALESCE(SUM(output_tokens), 0) AS output_tokens, COALESCE(SUM(seconds), 0) AS seconds, "
                "MIN(at) AS first_at, MAX(at) AS last_at "
                "FROM ai_usage_log GROUP BY month, purpose, model, earlier").fetchall()
        finally:
            conn.close()
    except sqlite3.OperationalError:
        return None
    price_in = settings.get_price("price_input_per_million")
    price_out = settings.get_price("price_output_per_million")
    priced = price_in > 0 or price_out > 0

    def bucket():
        return {"requests": 0, "failed": 0, "input_tokens": 0, "output_tokens": 0, "seconds": 0.0}

    def finish(b):
        b["total_tokens"] = b["input_tokens"] + b["output_tokens"]
        b["cost"] = (b["input_tokens"] * price_in + b["output_tokens"] * price_out) / 1e6 if priced else None
        b["seconds"] = round(b["seconds"], 1)
        return b

    overall, earlier = bucket(), bucket()
    months: dict[str, dict] = {}
    purposes: dict[str, dict] = {}
    models: dict[str, dict] = {}
    first_at = last_at = live_since = None
    for r in rows:
        targets = [overall, months.setdefault(r["month"] or "", bucket()),
                   purposes.setdefault(_purpose_group(r["purpose"]), bucket()),
                   models.setdefault(r["model"] or "—", bucket())]
        if r["earlier"]:
            targets.append(earlier)
        elif live_since is None or r["first_at"] < live_since:
            live_since = r["first_at"]
        for b in targets:
            b["requests"] += r["n"]
            b["failed"] += r["failed"] or 0
            b["input_tokens"] += r["input_tokens"]
            b["output_tokens"] += r["output_tokens"]
            b["seconds"] += r["seconds"]
        first_at = r["first_at"] if first_at is None or r["first_at"] < first_at else first_at
        last_at = r["last_at"] if last_at is None or r["last_at"] > last_at else last_at

    def ordered(d, key):
        return [dict(finish(b), name=name) for name, b in sorted(d.items(), key=key)]

    return {
        "overall": finish(overall), "earlier": finish(earlier) if earlier["requests"] else None,
        "months": ordered(months, lambda kv: kv[0])[::-1],
        "purposes": ordered(purposes, lambda kv: -kv[1]["requests"]),
        "models": ordered(models, lambda kv: -kv[1]["requests"]),
        "first_at": first_at, "last_at": last_at, "live_since": live_since, "priced": priced,
    }


def to_json(calls: list[dict]) -> str | None:
    return json.dumps(calls) if calls else None


def summary(raw) -> dict | None:
    """The debug-page view of a stored ai_usage value, with totals and cost at today's prices."""
    try:
        calls = json.loads(raw) if isinstance(raw, str) else (raw or [])
    except ValueError:
        calls = []
    if not calls:
        return None
    price_in = settings.get_price("price_input_per_million")
    price_out = settings.get_price("price_output_per_million")
    priced = price_in > 0 or price_out > 0

    def cost(c):
        return ((c.get("input_tokens") or 0) * price_in + (c.get("output_tokens") or 0) * price_out) / 1e6

    rows = [dict(c, cost=cost(c) if priced else None) for c in calls]
    return {
        "rows": rows,
        "input_tokens": sum(c.get("input_tokens") or 0 for c in calls),
        "output_tokens": sum(c.get("output_tokens") or 0 for c in calls),
        "seconds": round(sum(c.get("seconds") or 0 for c in calls), 2),
        "cost": sum(r["cost"] for r in rows) if priced else None,
        "priced": priced,
    }


def view(raw) -> dict | None:
    """summary(), or None when "Show AI token usage" is off on Admin → App settings. Used by templates."""
    return summary(raw) if settings.get_bool("show_ai_usage") else None
