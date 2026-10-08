"""Usage per caller per model per day, and the callers seen (SPEC.md §4 "Logging").

Per request: time, model, input/output tokens, seconds, queue wait — never prompts, images or answers. Kept
30 days (housekeeping deletes older days).
"""
from datetime import timedelta

from . import config, db

KEEP_DAYS = 30


def record(caller: str, model: str, address: str | None, input_tokens: int, output_tokens: int,
           seconds: float, waited: float) -> None:
    day = config.today().isoformat()
    now = config.utcnow().isoformat(timespec="seconds")
    with db.get_conn() as conn:
        conn.execute(
            "INSERT INTO model_usage (day, caller, model, requests, input_tokens, output_tokens, seconds, waited) "
            "VALUES (?, ?, ?, 1, ?, ?, ?, ?) ON CONFLICT(day, caller, model) DO UPDATE SET "
            "requests = requests + 1, input_tokens = input_tokens + excluded.input_tokens, "
            "output_tokens = output_tokens + excluded.output_tokens, seconds = seconds + excluded.seconds, "
            "waited = waited + excluded.waited",
            (day, caller, model, int(input_tokens or 0), int(output_tokens or 0), round(seconds, 3),
             round(waited, 3)))
        conn.execute(
            "INSERT INTO model_callers (caller, address, first_seen_at, last_used_at) VALUES (?, ?, ?, ?) "
            "ON CONFLICT(caller) DO UPDATE SET address = excluded.address, last_used_at = excluded.last_used_at",
            (caller, address, now, now))


def callers() -> list[dict]:
    """Every caller seen, with this month's totals (the page's "Use it in other apps" rows)."""
    month = config.today().replace(day=1).isoformat()
    with db.get_conn() as conn:
        rows = conn.execute("SELECT caller, address, last_used_at FROM model_callers ORDER BY last_used_at DESC")\
            .fetchall()
        totals = {r[0]: r[1:] for r in conn.execute(
            "SELECT caller, SUM(requests), SUM(input_tokens) + SUM(output_tokens) FROM model_usage WHERE day >= ? "
            "GROUP BY caller", (month,)).fetchall()}
    return [{"caller": r[0], "address": r[1], "lastUsedAt": r[2],
             "requests": (totals.get(r[0]) or (0, 0))[0] or 0, "tokens": (totals.get(r[0]) or (0, 0))[1] or 0}
            for r in rows]


def days() -> list[dict]:
    since = (config.today() - timedelta(days=KEEP_DAYS)).isoformat()
    with db.get_conn() as conn:
        rows = conn.execute("SELECT day, caller, model, requests, input_tokens, output_tokens, seconds, waited "
                            "FROM model_usage WHERE day >= ? ORDER BY day DESC, caller, model", (since,)).fetchall()
    return [{"day": r[0], "caller": r[1], "model": r[2], "requests": r[3], "inputTokens": r[4],
             "outputTokens": r[5], "seconds": round(r[6], 1), "waited": round(r[7], 1)} for r in rows]


def requests_today(model: str) -> int:
    with db.get_conn() as conn:
        r = conn.execute("SELECT SUM(requests) FROM model_usage WHERE day = ? AND model = ?",
                         (config.today().isoformat(), model)).fetchone()
    return int(r[0] or 0)


def housekeeping() -> None:
    cutoff = (config.today() - timedelta(days=KEEP_DAYS)).isoformat()
    with db.get_conn() as conn:
        conn.execute("DELETE FROM model_usage WHERE day < ?", (cutoff,))
        conn.execute("DELETE FROM model_callers WHERE last_used_at < ?", (cutoff,))
