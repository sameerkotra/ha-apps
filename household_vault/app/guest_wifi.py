"""Guest Wi-Fi on the Home Assistant dashboard (opt-in).

An editor of Household can pick one Wi-Fi login and publish it: the app
then keeps a sensor in Home Assistant — `sensor.household_vault_guest_wifi`,
state = the network name, `entity_picture` = a QR code guests scan to join
(a Picture entity card shows it). Only items made as (or marked) Wi-Fi items
can be published; the QR code always contains the password. The same code is on the app's own
"Guest Wi-Fi" page, which works without unlocking.

This is the one place where a password leaves the encrypted vault on
purpose: the network name and password are kept unencrypted in the app's
database (so the sensor survives restarts while everyone is locked) and are
visible to anyone who can see the dashboard — which is the point of a guest
network. Publishing asks first and tells the other Household members. It
follows the item: edits update it; deleting the item or *Stop showing*
removes it (and the sensor).
"""
import base64
import json
import logging
import threading

from fastapi import HTTPException

from . import alerts, config, db, ha_client, items, qrcode, sessions

logger = logging.getLogger("guest_wifi")
ENTITY = "sensor.household_vault_guest_wifi"


def current(conn):
    return conn.execute("SELECT * FROM guest_wifi WHERE id = 1").fetchone()


def picture(row) -> str:
    svg = qrcode.svg(qrcode.wifi_text(row["ssid"], row["password"], row["security"]))
    return "data:image/svg+xml;base64," + base64.b64encode(svg.encode()).decode()


def payload(row) -> dict:
    attrs = {"friendly_name": "Guest Wi-Fi", "icon": "mdi:wifi", "entity_picture": picture(row),
             "ssid": row["ssid"], "security": row["security"]}
    if row["show_password"] and row["security"] != "nopass":
        attrs["password"] = row["password"]
    return {"state": row["ssid"][:255], "attributes": attrs}


def _from_entry(d, e, security: str) -> tuple[str, str]:
    ssid = (d.get_field(e, "UserName") or d.get_field(e, "Title") or "").strip()
    pw = d.get_field(e, "Password") or ""
    if not ssid:
        raise HTTPException(422, "Put the network name in the Username field first.")
    if security != "nopass" and not pw:
        raise HTTPException(422, "Put the Wi-Fi password in the Password field first.")
    return ssid, pw


def publish(conn, user: dict, vault_id: str, item_id: str, security: str, show_password: bool) -> None:
    v = conn.execute("SELECT kind FROM vaults WHERE id = ?", (vault_id,)).fetchone()
    if v is None or v["kind"] != "household":
        raise HTTPException(409, "Only a Wi-Fi item in Household can go on the dashboard.")
    if security not in items.WIFI_SECURITY:
        raise HTTPException(422, "Wi-Fi security must be WPA, WEP or nopass.")
    ov = sessions.get_open(vault_id)
    with ov.lock:
        e = items.find_entry(ov.db, item_id)
        if ov.db.in_recycle_bin(e):
            raise HTTPException(409, "That item is in the Trash.")
        if items.template_of(ov.db, e) != "wifi":
            raise HTTPException(409, "Only a Wi-Fi item can go on the dashboard — make it a Wi-Fi item first.")
        ssid, pw = _from_entry(ov.db, e, security)
    before = current(conn)
    conn.execute("INSERT INTO guest_wifi (id, vault_id, item_id, ssid, password, security, show_password, published_by, updated_at) "
                 "VALUES (1, ?, ?, ?, ?, ?, ?, ?, ?) ON CONFLICT(id) DO UPDATE SET vault_id = excluded.vault_id, "
                 "item_id = excluded.item_id, ssid = excluded.ssid, password = excluded.password, security = excluded.security, "
                 "show_password = excluded.show_password, published_by = excluded.published_by, updated_at = excluded.updated_at",
                 (vault_id, item_id, ssid, pw if security != "nopass" else "", security, 1 if show_password else 0,
                  user["id"], config.now_iso()))
    db.audit(conn, "guest_wifi_published", user["id"], vault_id)
    if before is None or before["item_id"] != item_id:
        alerts.send(conn, alerts.others_in(conn, vault_id, user["id"]),
                    f"{user['name']} put the guest Wi-Fi ({ssid}) on the Home Assistant dashboard.")
    push_async()


def unpublish(conn, actor: str | None) -> None:
    if current(conn) is None:
        return
    conn.execute("DELETE FROM guest_wifi WHERE id = 1")
    db.audit(conn, "guest_wifi_removed", actor, None)
    push_async()


def after_save(conn, vault_id: str, d) -> None:
    """The published item changed: follow it (or stop showing it if it's gone)."""
    row = current(conn)
    if row is None or row["vault_id"] != vault_id:
        return
    try:
        e = items.find_entry(d, row["item_id"])
    except HTTPException:
        e = None
    if e is None or d.in_recycle_bin(e) or items.template_of(d, e) != "wifi":
        unpublish(conn, None)
        return
    try:
        ssid, pw = _from_entry(d, e, row["security"])
    except HTTPException:
        unpublish(conn, None)
        return
    if row["security"] == "nopass":
        pw = ""
    if (ssid, pw) != (row["ssid"], row["password"]):
        conn.execute("UPDATE guest_wifi SET ssid = ?, password = ?, updated_at = ? WHERE id = 1", (ssid, pw, config.now_iso()))
        push_async()


# ---------- Home Assistant ----------
def push_blocking() -> bool:
    """Make Home Assistant's sensor match the table (create, update or remove)."""
    if not ha_client.has_token():
        ha_client.warn_no_token_once("the guest Wi-Fi sensor")
        return False
    with db.get_conn() as conn:
        row = current(conn)
        body = payload(row) if row else None
    if body is None:
        status, _ = ha_client.request("DELETE", f"/states/{ENTITY}")
        return status in (200, 404)
    status, _ = ha_client.request("POST", f"/states/{ENTITY}", body)
    if status not in (200, 201):
        logger.warning("Couldn't update %s (HTTP %s).", ENTITY, status)
    return status in (200, 201)


def ensure_blocking() -> None:
    """Every few minutes: Home Assistant forgets states set this way when it restarts, so put it back."""
    with db.get_conn() as conn:
        row = current(conn)
    if row is None or not ha_client.has_token():
        return
    status, body = ha_client.request("GET", f"/states/{ENTITY}")
    if status == 200:
        try:
            if json.loads(body).get("state") == row["ssid"][:255]:
                return
        except ValueError:
            pass
    push_blocking()


def push_async(delay: float = 1.0) -> None:
    """Soon, from a thread — after the caller's transaction has committed."""
    t = threading.Timer(delay, lambda: _safe(push_blocking))
    t.daemon = True
    t.start()


def _safe(fn):
    try:
        fn()
    except Exception:
        logger.exception("Guest Wi-Fi sensor update failed")
