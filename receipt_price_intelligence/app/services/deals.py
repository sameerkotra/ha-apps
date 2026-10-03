"""The daily best-price check.

Once a day (and once at start-up for anything out of date) every home's recent price
observations are checked: for each item, where it was cheapest lately and how that compares
with where it is usually bought. The result is stored as one snapshot per home so the page
opens instantly and shows the same numbers all day.

The maths is in ``app/services/analytics.py``; this module loads rows, stores the result,
and never lets one home's failure stop the others.
"""

import json
import time
from datetime import date, datetime, timedelta, timezone
from typing import Any

from sqlalchemy.orm import Session

from app.config import get_settings
from app.db import get_db_session
from app.db.models import Home, RecommendationSnapshot
from app.logging_config import get_logger
from app.services import alerts, analytics
from app.services.analysis_data import iso, load_observations

logger = get_logger("deals")

STALE_AFTER_HOURS = 23


def _now() -> datetime:
    """Naive UTC, matching how timestamps are stored."""
    return datetime.now(timezone.utc).replace(tzinfo=None)


def build_snapshot(db: Session, home_id: str, today: date | None = None) -> dict[str, Any]:
    """Check every item bought in this home within the recommendation window."""
    settings = get_settings()
    today = today or date.today()
    window = settings.recommendation_window_days
    observations = load_observations(db, home_id, iso(today - timedelta(days=window)))

    recs = analytics.recommendations(observations, today, window, settings.minimum_savings_percent)
    best = analytics.cheapest_by_item(observations)
    return {
        "window_days": window,
        "minimum_savings_percent": settings.minimum_savings_percent,
        "summary": {
            "items_checked": len(best),
            "items_compared": sum(1 for b in best if b["compared"]),
            "cheaper_elsewhere": len(recs["switch_store"]),
            "est_monthly_savings": recs["est_monthly_savings"],
        },
        "best_prices": best,
        "switch_store": recs["switch_store"],
        "price_alerts": recs["price_alerts"],
        "restock": recs["restock"],
        "store_scorecard": recs["store_scorecard"],
    }


def refresh_home(db: Session, home_id: str) -> bool:
    """Recompute and store one home's snapshot. On failure the previous results are kept."""
    started = time.monotonic()
    try:
        payload = build_snapshot(db, home_id)
        row = db.get(RecommendationSnapshot, home_id) or RecommendationSnapshot(home_id=home_id)
        first_check = not row.payload
        row.generated_at = _now()
        row.duration_ms = int((time.monotonic() - started) * 1000)
        row.status = "OK"
        row.error = None
        row.payload = json.dumps(payload)
        db.add(row)
        db.commit()
        logger.info("Best prices for home %s: %d items checked in %d ms", home_id, payload["summary"]["items_checked"], row.duration_ms)
        try:
            alerts.process_home(db, home_id, payload, announce=not first_check)
        except Exception:  # noqa: BLE001 - alerts and sensors must never break the check itself
            db.rollback()
            logger.exception("Alerts and sensors failed for home %s", home_id)
        return True
    except Exception as e:  # noqa: BLE001 - one home must never stop the run
        db.rollback()
        logger.exception("Best-price check failed for home %s", home_id)
        try:
            row = db.get(RecommendationSnapshot, home_id)
            if row is None:
                row = RecommendationSnapshot(home_id=home_id, generated_at=_now(), payload=None)
                db.add(row)
            row.status = "FAILED"
            row.error = str(e)[:500]
            db.commit()
        except Exception:  # noqa: BLE001
            db.rollback()
        return False


def refresh_home_id(home_id: str) -> bool:
    """Same as ``refresh_home`` with its own database session (for background threads)."""
    with get_db_session()() as db:
        return refresh_home(db, home_id)


def _home_ids(only_stale: bool) -> list[str]:
    with get_db_session()() as db:
        ids = [h.id for h in db.query(Home).all()]
        if not only_stale:
            return ids
        cutoff = _now() - timedelta(hours=STALE_AFTER_HOURS)
        fresh = {
            s.home_id for s in db.query(RecommendationSnapshot).all()
            if s.status == "OK" and s.generated_at and s.generated_at > cutoff
        }
        return [i for i in ids if i not in fresh]


def refresh_all(only_stale: bool = False) -> dict[str, int]:
    """Check every home (or only those without a recent snapshot). Returns counts."""
    ok = failed = 0
    for home_id in _home_ids(only_stale):
        if refresh_home_id(home_id):
            ok += 1
        else:
            failed += 1
    logger.info("Best-price run finished: %d updated, %d failed", ok, failed)
    return {"updated": ok, "failed": failed}


def get_snapshot(db: Session, home_id: str) -> dict[str, Any]:
    """The stored snapshot with its metadata. ``status`` is PENDING before the first run."""
    row = db.get(RecommendationSnapshot, home_id)
    if row is None:
        return {"status": "PENDING", "generated_at": None, "error": None}
    payload = json.loads(row.payload) if row.payload else {}
    return {
        "status": row.status,
        "generated_at": row.generated_at.isoformat() + "Z" if row.generated_at else None,
        "duration_ms": row.duration_ms,
        "error": row.error,
        **payload,
    }
