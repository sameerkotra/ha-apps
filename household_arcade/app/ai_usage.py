"""AI usage (SPEC §11.10): every request to the model is written down — when, why (a level build or a connection
test), which game, provider and model, whether it worked, the tokens it used and the levels it gave — and
Admin → AI usage adds them up: today, 7 days, 30 days and all time; by day, by game and by model; the latest
requests. With prices per million tokens set on App settings the totals also show an estimated cost.

Nothing about the people in the house is stored here. Rows older than KEEP_DAYS are removed by housekeeping.
"""
from __future__ import annotations

from datetime import timedelta

from . import config, db, games, settings

KEEP_DAYS = 400


def record(*, purpose: str, provider: str, model: str, ok: bool, error: str | None = None, game: str | None = None,
           build_id: str | None = None, tokens_in: int | None = 0, tokens_out: int | None = 0, ms: int = 0,
           made: int = 0, rejected: int = 0) -> None:
    """Write down one request to the model. Never raises (usage notes must not break a build)."""
    try:
        with db.get_conn() as conn:
            conn.execute("INSERT INTO ai_calls (id, at, purpose, game, build_id, provider, model, ok, error, tokens_in, "
                         "tokens_out, ms, levels_made, levels_rejected) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                         (db.new_id(), config.now_iso(), purpose, game, build_id, provider or "", model or "",
                          1 if ok else 0, (error or None) and str(error)[:300], int(tokens_in or 0), int(tokens_out or 0),
                          int(ms or 0), int(made or 0), int(rejected or 0)))
    except Exception:  # pragma: no cover - logged by the caller's logger at most
        pass


def prune(conn) -> int:
    cut, _ = config.local_day_bounds_utc(config.today() - timedelta(days=KEEP_DAYS))
    return conn.execute("DELETE FROM ai_calls WHERE at < ?", (cut,)).rowcount


def _cost(tin: int, tout: int, price_in: float, price_out: float) -> float | None:
    if not price_in and not price_out:
        return None
    return round(tin / 1e6 * price_in + tout / 1e6 * price_out, 4)


def _sum(rows, price_in, price_out) -> dict:
    calls = sum(r["calls"] for r in rows)
    tin = sum(r["tin"] or 0 for r in rows)
    tout = sum(r["tout"] or 0 for r in rows)
    return {"calls": calls, "failed": sum(r["failed"] or 0 for r in rows), "tokensIn": tin, "tokensOut": tout,
            "levels": sum(r["made"] or 0 for r in rows), "rejected": sum(r["rejected"] or 0 for r in rows),
            "cost": _cost(tin, tout, price_in, price_out)}


_AGG = ("COUNT(*) AS calls, SUM(1 - ok) AS failed, SUM(tokens_in) AS tin, SUM(tokens_out) AS tout, "
        "SUM(levels_made) AS made, SUM(levels_rejected) AS rejected")


def report(conn, days: int = 30) -> dict:
    """Everything Admin → AI usage shows."""
    v = settings.all()
    pin, pout = float(v["ai_price_in"] or 0), float(v["ai_price_out"] or 0)
    today = config.today()

    def since(n_days: int):
        start, _ = config.local_day_bounds_utc(today - timedelta(days=n_days - 1))
        return start

    def total(start=None):
        q = f"SELECT {_AGG} FROM ai_calls" + (" WHERE at >= ?" if start else "")
        return _sum(conn.execute(q, (start,) if start else ()).fetchall(), pin, pout)

    by_day = []
    for i in range(days - 1, -1, -1):
        d = today - timedelta(days=i)
        start, end = config.local_day_bounds_utc(d)
        r = conn.execute(f"SELECT {_AGG} FROM ai_calls WHERE at >= ? AND at < ?", (start, end)).fetchall()
        by_day.append(dict(_sum(r, pin, pout), date=d.isoformat()))
    start30 = since(days)
    by_game = []
    for r in conn.execute(f"SELECT game, {_AGG} FROM ai_calls WHERE at >= ? AND purpose = 'build' "
                          "GROUP BY game ORDER BY tin + tout DESC", (start30,)):
        by_game.append(dict(_sum([r], pin, pout), game=r["game"], name=games.name(r["game"]) if r["game"] else "—"))
    by_model = []
    for r in conn.execute(f"SELECT provider, model, {_AGG} FROM ai_calls WHERE at >= ? GROUP BY provider, model "
                          "ORDER BY tin + tout DESC", (start30,)):
        by_model.append(dict(_sum([r], pin, pout), provider=r["provider"], model=r["model"]))
    recent = [{"at": r["at"], "purpose": r["purpose"], "game": r["game"],
               "name": games.name(r["game"]) if r["game"] else None, "provider": r["provider"], "model": r["model"],
               "ok": bool(r["ok"]), "error": r["error"], "tokensIn": r["tokens_in"], "tokensOut": r["tokens_out"],
               "ms": r["ms"], "made": r["levels_made"], "rejected": r["levels_rejected"],
               "cost": _cost(r["tokens_in"], r["tokens_out"], pin, pout)}
              for r in conn.execute("SELECT * FROM ai_calls ORDER BY at DESC, rowid DESC LIMIT 25")]
    return {
        "totals": {"today": total(since(1)), "week": total(since(7)), "month": total(since(30)), "all": total()},
        "byDay": by_day, "byGame": by_game, "byModel": by_model, "recent": recent, "days": days,
        "prices": {"in": pin, "out": pout},
        "dailyLimit": v["ai_levels_daily_limit"],
    }
