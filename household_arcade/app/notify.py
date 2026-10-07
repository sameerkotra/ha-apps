"""Phone notifications (SPEC §7), each behind its own App settings switch.

- **Notify new records** (`notify_records`): when someone sets a new household
  record, everyone else who is switched on and hasn't opted out (Settings →
  Receive notifications) gets "Asha set a new Snake record: 1,240". A child
  whose leaderboard is hidden isn't told.
- **Limit warnings to parents** (`limit_warnings`): when a child has 5 minutes
  of play time left today, the admins picked in App settings (every admin if
  none is picked) get a notice with an "Add 15 minutes" action that opens the
  app on that child's card on Admin → Users. Sent at most once per child per
  day (notify_log), so a restart doesn't repeat it.

Delivery is the shared ha_notify.py (phones from Home Assistant's People plus
extra services from Admin → Users). Blocking: run from a background task, and
never while holding a DB connection.
"""
import logging

from . import auth, config, db, games, settings
from .common import ha_notify

logger = logging.getLogger("notify")

TITLE = "Household Arcade"


def _link(path: str = "") -> dict | None:
    """Where a notification opens: the app's sidebar page with the app's own route as a sub-path
    ("/local_household_arcade/leaderboard"; panel.py says why not "#/leaderboard"). None without a sidebar page."""
    if not config.INGRESS_PANEL:
        return None
    url = config.INGRESS_PANEL + path
    return {"url": url, "clickAction": url}


def _send(user_ids: list[str], message: str, data: dict | None) -> int:
    sent = 0
    for uid in user_ids:
        with db.get_conn() as conn:
            row = conn.execute("SELECT id, name, username FROM users WHERE id = ?", (uid,)).fetchone()
            if not row:
                continue
            services = ha_notify.services_for(dict(row), conn)
        # the connection is closed before Home Assistant is called
        results = ha_notify.send_to_services(services, TITLE, message, data)
        sent += sum(1 for ok in results.values() if ok)
    return sent


def record_blocking(scorer_id: str, game: str, mode: str, score: int) -> int:
    if not settings.get("notify_records"):
        return 0
    with db.get_conn() as conn:
        scorer = conn.execute("SELECT name FROM users WHERE id = ?", (scorer_id,)).fetchone()
        rows = conn.execute(
            "SELECT u.id, u.username, u.is_child, l.leaderboard FROM users u "
            "LEFT JOIN child_limits l ON l.user_id = u.id "
            "WHERE u.disabled = 0 AND u.receive_notifications = 1 AND u.id != ?", (scorer_id,)).fetchall()
    if not scorer:
        return 0
    targets = [r["id"] for r in rows
               if not (r["is_child"] and not auth.is_admin_identity(r["id"], r["username"])
                       and r["leaderboard"] == "hidden")]
    message = f"{scorer['name']} set a new {games.name(game)} record: {score:,} ({games.mode_label(game, mode)})."
    return _send(targets, message, _link("/leaderboard"))


def warning_admins(conn) -> list[str]:
    chosen = settings.get("limit_warning_admins")
    rows = conn.execute("SELECT id, username FROM users WHERE disabled = 0").fetchall()
    admins = [r["id"] for r in rows if auth.is_admin_identity(r["id"], r["username"])]
    picked = [a for a in admins if a in chosen]
    return picked or admins


def limit_warning_blocking(child_id: str) -> int:
    """'5 minutes left' to the parents, once per child per day."""
    if not settings.get("limit_warnings"):
        return 0
    today = config.today().isoformat()
    with db.get_conn() as conn:
        child = conn.execute("SELECT name FROM users WHERE id = ?", (child_id,)).fetchone()
        if not child:
            return 0
        n = conn.execute("INSERT OR IGNORE INTO notify_log (kind, user_id, sent_on, created_at) VALUES "
                         "('limit5', ?, ?, ?)", (child_id, today, config.now_iso())).rowcount
        targets = warning_admins(conn) if n else []
    if not targets:
        return 0
    link = _link(f"/admin/users/{child_id}")
    data = dict(link) if link else {}
    if link:
        data["actions"] = [{"action": "URI", "title": "Add 15 minutes", "uri": link["url"]}]
    message = f"{child['name']} has 5 minutes of play time left today."
    return _send(targets, message, data or None)


def warned_today(conn) -> set[str]:
    today = config.today().isoformat()
    return {r["user_id"] for r in conn.execute(
        "SELECT user_id FROM notify_log WHERE kind = 'limit5' AND sent_on = ?", (today,))}
