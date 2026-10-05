"""Notifications (SPEC §11): "Alex shared 'Trip 2026' with you" (and "Alex gave you 'Trip 2026'" after a
transfer) to the person's phones from Home Assistant (Settings → People → Track device) plus any extra notify
services an admin added on Admin → People. Each person can turn them off in Settings (`notifyShares`).
Never any document text; Everyone shares don't notify the whole household. Blocking — run after the response
(BackgroundTasks), never while a database connection is open."""
import json
import logging

from . import config, db
from .common import ha_notify

logger = logging.getLogger("notify")


def prefs_of(row) -> dict:
    try:
        p = json.loads(row["prefs"] or "{}")
    except (TypeError, ValueError):
        p = {}
    return p if isinstance(p, dict) else {}


def wants_shares(row) -> bool:
    return prefs_of(row).get("notifyShares", True) is not False


def send_shared(target_id: str, actor_name: str, item_name: str, *, transfer: bool = False) -> bool:
    with db.get_conn() as conn:
        row = conn.execute("SELECT * FROM users WHERE id = ?", (target_id,)).fetchone()
        if row is None or row["disabled"] or not wants_shares(row):
            return False
        services = ha_notify.services_for({"id": target_id}, conn)
    if not services:
        return False
    title = config.APP_TITLE
    message = (f"{actor_name} gave you “{item_name}”" if transfer
               else f"{actor_name} shared “{item_name}” with you")
    sent = ha_notify.send_to_services(services, title, message, {"url": config.INGRESS_URL, "clickAction": config.INGRESS_URL})
    return any(sent.values())


def send_moved(user_ids) -> int:
    """§5.7: "Documents moved to the new location" to everyone with access (their phones, if linked)."""
    n = 0
    for uid in user_ids or []:
        try:
            with db.get_conn() as conn:
                row = conn.execute("SELECT * FROM users WHERE id = ?", (uid,)).fetchone()
                if row is None or row["disabled"]:
                    continue
                services = ha_notify.services_for({"id": uid}, conn)
            if services:
                sent = ha_notify.send_to_services(services, config.APP_TITLE, "Documents moved to the new location — "
                                                  "everything works as before.", {"url": config.INGRESS_URL,
                                                                                  "clickAction": config.INGRESS_URL})
                n += any(sent.values())
        except Exception:
            logger.exception("Telling someone about the move failed")
    return n


def child_status(conn, user_id: str) -> dict | None:
    """What a person is told about Kids' space (§17.20): {isChild, parents: [names], since}, None when not a child."""
    row = conn.execute("SELECT is_child, child_since FROM users WHERE id = ?", (user_id,)).fetchone()
    if row is None or not row["is_child"]:
        return None
    names = [r["name"] for r in conn.execute("SELECT u.name FROM kid_parents kp JOIN users u ON u.id = kp.parent_id "
                                             "WHERE kp.child_id = ? ORDER BY u.name COLLATE NOCASE", (user_id,))]
    return {"isChild": True, "parents": names, "since": row["child_since"]}


def child_message(status: dict | None) -> str:
    if status is None:
        return "An admin turned Kids' space off for you in Household Docs."
    who = ", ".join(status["parents"])
    if who:
        return (f"An admin marked you as a child in Household Docs. {who} can view what you make in My docs from now on "
                "(not what you had before).")
    return "An admin marked you as a child in Household Docs (Kids' space). Nobody else can view your My docs."


def send_child_status(user_id: str) -> bool:
    """Tell a person they were marked as a child (or not any more) and who may view their My docs — always, whatever
    their share-notification setting says (it's about who can read their documents)."""
    try:
        with db.get_conn() as conn:
            row = conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
            if row is None:
                return False
            message = child_message(child_status(conn, user_id))
            services = ha_notify.services_for({"id": user_id}, conn)
        if not services:
            return False
        sent = ha_notify.send_to_services(services, config.APP_TITLE, message,
                                          {"url": config.INGRESS_URL, "clickAction": config.INGRESS_URL})
        return any(sent.values())
    except Exception:
        logger.exception("Telling someone about Kids' space failed")
        return False
