"""The daily challenge's routes (SPEC "Daily challenge"; the rules are in daily.py). While App settings → Show
daily challenges is off every route here is a 404."""
from fastapi import APIRouter, Depends, HTTPException, Query

from .. import config, daily, db, games, limits, settings
from ..auth import get_current_user

router = APIRouter(prefix="/api", tags=["daily"])


def _off():
    try:
        daily.require_enabled()
    except daily.DailyError as e:
        raise HTTPException(e.status, str(e))


@router.get("/daily")
def today(current: dict = Depends(get_current_user)):
    """Today's challenges, whether you've played each, and your days played this month."""
    _off()
    with db.get_conn() as conn:
        return daily.card(conn, current)


@router.get("/daily/board")
def board(game: str = Query(...), date: str | None = Query(default=None), current: dict = Depends(get_current_user)):
    """A challenge's top 10 for a day (today by default) and this month's days-played ranking."""
    _off()
    if not settings.get("leaderboard"):
        raise HTTPException(403, "The leaderboard is switched off on App settings.")
    names = "full"
    with db.get_conn() as conn:
        if current["is_child"]:
            names = limits.load_limits(conn, current["id"])["leaderboard"]
            if names == "hidden":
                raise HTTPException(403, "The leaderboard isn't shown for you.")
        day = date or config.today().isoformat()
        if not isinstance(day, str) or not daily._DATE_RE.match(day):
            raise HTTPException(422, "date must be a date (YYYY-MM-DD).")
        if not games.exists(game) or not settings.game_enabled(game):
            raise HTTPException(404, "Unknown game.")
        rows = daily.board(conn, game, day, current, names)
        days = daily.days_board(conn, current, names)
    return {"game": game, "date": day, "rows": rows, "days": days, "canDelete": current["is_admin"]}
