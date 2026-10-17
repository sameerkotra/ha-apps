"""Guest Wi-Fi page (opt-in).

An editor of Household can pick one Wi-Fi login and publish it to the app's
own **📶 Guest Wi-Fi** page: a QR code guests scan to join, shown to every
active person without unlocking. Only items made as (or marked) Wi-Fi items
can be published; the QR code always contains the password.

This is the one place where a password leaves the encrypted vault on
purpose: the network name and password are kept unencrypted in the app's
database (so the page works while everyone is locked). Nothing is sent to
Home Assistant: the former `sensor.household_vault_guest_wifi` was dropped
(security review 2026-10 — a state attribute lands in the recorder, the
history, every state-exporting integration and HA backups, and stays there
after *Stop showing*). A sensor an older version left behind is removed once
at start-up (`remove_old_sensor_blocking`). Publishing asks first and tells
the other Household members. It follows the item: edits update it; deleting
the item or *Stop showing* removes it.
"""
import base64
import logging

from fastapi import HTTPException

from . import alerts, config, db, ha_client, items, qrcode, sessions

logger = logging.getLogger("guest_wifi")
OLD_ENTITY = "sensor.household_vault_guest_wifi"       # kept only to remove what an older version left in HA


def current(conn):
    return conn.execute("SELECT * FROM guest_wifi WHERE id = 1").fetchone()


def picture(row) -> str:
    svg = qrcode.svg(qrcode.wifi_text(row["ssid"], row["password"], row["security"]))
    return "data:image/svg+xml;base64," + base64.b64encode(svg.encode()).decode()


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
                    f"{user['name']} put the guest Wi-Fi ({ssid}) on the Guest Wi-Fi page.")


def unpublish(conn, actor: str | None) -> None:
    if current(conn) is None:
        return
    conn.execute("DELETE FROM guest_wifi WHERE id = 1")
    db.audit(conn, "guest_wifi_removed", actor, None)


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


# ---------- Home Assistant (clean-up only) ----------
def remove_old_sensor_blocking() -> bool:
    """Once at start-up: delete the sensor an older version kept in Home Assistant (the app no longer sends the
    guest Wi-Fi anywhere). True when it's gone (or never was)."""
    if not ha_client.has_token():
        return False
    status, _ = ha_client.request("GET", f"/states/{OLD_ENTITY}")
    if status != 200:
        return status == 404
    status, _ = ha_client.request("DELETE", f"/states/{OLD_ENTITY}")
    if status in (200, 404):
        logger.info("Removed the old %s from Home Assistant.", OLD_ENTITY)
        return True
    logger.warning("Couldn't remove the old %s (HTTP %s); delete it in Home Assistant yourself.", OLD_ENTITY, status)
    return False
