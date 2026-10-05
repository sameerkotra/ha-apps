"""AI usage (SPEC §17.6, as in the other household apps with AI): every request to the model is written down — when,
what for (reading text, a summary, a checklist, a sheet, a question about a folder, a connection test), the
provider and model, whether it worked, the tokens and the time — and Admin → AI usage adds them up: today, 7
days, 30 days and all time; by day, by action and by model; the latest requests; an estimated cost when prices
are set. The monthly limit (`ai_monthly_tokens`) is checked against this month's rows before each request.

Nothing about the people in the house or their documents is stored here. Rows older than KEEP_DAYS are removed by
housekeeping.
"""
from __future__ import annotations

from datetime import datetime, timedelta

from . import config, db, settings

KEEP_DAYS = 400
PURPOSES = {"filing": "Name and folder for a scan", "ocr": "Read text", "summarise": "Summary", "checklist": "Checklist from text", "sheet": "Sheet from a table",
            "ask": "Question about a folder", "test": "Connection test"}


def record(*, purpose: str, provider: str, model: str, ok: bool, error: str | None = None, tokens_in: int | None = 0,
           tokens_out: int | None = 0, ms: int = 0) -> None:
    """Never raises (usage notes must not break an answer)."""
    try:
        with db.get_conn() as conn:
            conn.execute("INSERT INTO ai_calls (id, at, purpose, provider, model, ok, error, tokens_in, tokens_out, ms) "
                         "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                         (db.new_id(), config.now_iso(), purpose, provider or "", model or "", 1 if ok else 0,
                          (error or None) and str(error)[:300], int(tokens_in or 0), int(tokens_out or 0), int(ms or 0)))
    except Exception:  # pragma: no cover
        pass


def _day_start_utc(d) -> str:
    local = config.ZONE.now(config.utcnow())
    start = datetime(d.year, d.month, d.day, tzinfo=local.tzinfo)
    return start.astimezone(config.utcnow().tzinfo).isoformat(timespec="seconds")


def month_start() -> str:
    today = config.now().date()
    return _day_start_utc(today.replace(day=1))


def month_tokens(conn) -> int:
    r = conn.execute("SELECT COALESCE(SUM(tokens_in + tokens_out), 0) FROM ai_calls WHERE at >= ?",
                     (month_start(),)).fetchone()
    return int(r[0] or 0)


def limit_left(conn) -> int | None:
    """Tokens left this month (None = no limit)."""
    lim = int(settings.get("ai_monthly_tokens", conn))
    if not lim:
        return None
    return max(0, lim - month_tokens(conn))


def prune(conn) -> int:
    return conn.execute("DELETE FROM ai_calls WHERE at < ?", (config.ago_iso(days=KEEP_DAYS),)).rowcount


def _cost(tin: int, tout: int, pin: float, pout: float):
    if not pin and not pout:
        return None
    return round(tin / 1e6 * pin + tout / 1e6 * pout, 4)


_AGG = "COUNT(*) AS calls, SUM(1 - ok) AS failed, SUM(tokens_in) AS tin, SUM(tokens_out) AS tout"


def _sum(rows, pin, pout) -> dict:
    tin = sum(r["tin"] or 0 for r in rows)
    tout = sum(r["tout"] or 0 for r in rows)
    return {"calls": sum(r["calls"] or 0 for r in rows), "failed": sum(r["failed"] or 0 for r in rows),
            "tokensIn": tin, "tokensOut": tout, "cost": _cost(tin, tout, pin, pout)}


def report(conn, days: int = 30) -> dict:
    v = settings.all_values(conn)
    pin, pout = float(v["ai_price_in"] or 0), float(v["ai_price_out"] or 0)
    today = config.now().date()

    def since(n: int) -> str:
        return _day_start_utc(today - timedelta(days=n - 1))

    def total(start=None):
        q = f"SELECT {_AGG} FROM ai_calls" + (" WHERE at >= ?" if start else "")
        return _sum(conn.execute(q, (start,) if start else ()).fetchall(), pin, pout)

    by_day = []
    for i in range(days - 1, -1, -1):
        d = today - timedelta(days=i)
        start, end = _day_start_utc(d), _day_start_utc(d + timedelta(days=1))
        by_day.append(dict(_sum(conn.execute(f"SELECT {_AGG} FROM ai_calls WHERE at >= ? AND at < ?", (start, end)).fetchall(),
                                pin, pout), date=d.isoformat()))
    start = since(days)
    by_purpose = [dict(_sum([r], pin, pout), purpose=r["purpose"], label=PURPOSES.get(r["purpose"], r["purpose"]))
                  for r in conn.execute(f"SELECT purpose, {_AGG} FROM ai_calls WHERE at >= ? GROUP BY purpose "
                                        "ORDER BY tin + tout DESC", (start,))]
    by_model = [dict(_sum([r], pin, pout), provider=r["provider"], model=r["model"])
                for r in conn.execute(f"SELECT provider, model, {_AGG} FROM ai_calls WHERE at >= ? GROUP BY provider, model "
                                      "ORDER BY tin + tout DESC", (start,))]
    recent = [{"at": r["at"], "purpose": r["purpose"], "label": PURPOSES.get(r["purpose"], r["purpose"]),
               "provider": r["provider"], "model": r["model"], "ok": bool(r["ok"]), "error": r["error"],
               "tokensIn": r["tokens_in"], "tokensOut": r["tokens_out"], "ms": r["ms"],
               "cost": _cost(r["tokens_in"], r["tokens_out"], pin, pout)}
              for r in conn.execute("SELECT * FROM ai_calls ORDER BY at DESC, rowid DESC LIMIT 25")]
    lim = int(v["ai_monthly_tokens"])
    return {"totals": {"today": total(since(1)), "week": total(since(7)), "month": total(since(30)), "all": total()},
            "byDay": by_day, "byPurpose": by_purpose, "byModel": by_model, "recent": recent, "days": days,
            "prices": {"in": pin, "out": pout},
            "monthly": {"limit": lim, "used": month_tokens(conn)}, "enabled": bool(v["ai_enabled"])}
