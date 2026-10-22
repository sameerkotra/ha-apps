"""Everything this app asks Home Assistant, in one place (spec/SPEC.md §2–§4, §12).

- **Registries** (WebSocket commands, which the REST API doesn't have): `config_entries/get` (the `mobile_app`
  entries), `config/device_registry/list` and `config/entity_registry/list`. One short-lived connection per read
  (the shared ha_ws.MiniWS), every few minutes and when Home Assistant says a registry changed.
- **States**: GET /api/states (the shared ha_client), for the sensors, `last_reported` (the fallback when
  `state_reported` events don't come) and `last_updated`.
- **"Please report"**: POST /api/services/notify/<mobile_app_…> with `{"message": "command_update_sensors"}`; the
  answer is classified (sent / can't check / signed out).
- **Phone actions** (§15): POST /api/services/notify/<mobile_app_…> with the Companion app's notification commands
  (`command_screen_on`, `command_volume_level`, …), the alarm-stream notification of Find my phone and Lost mode,
  `request_location_update` and `clear_notification` (`notify`); answered like the "please report".
- **Home Assistant notifications** (persistent_notification.create) and **events** (the check-now result).

The tests replace `SOURCE` with a fake that answers from memory (tests/fakes.py). Every call is blocking and best
effort: run it from a thread, never while holding a database connection.
"""
import json
import logging

from . import config
from .common import ha_client, ha_ws

logger = logging.getLogger("ha_source")

COMMAND = "command_update_sensors"


class CommandResult:
    """What happened to a "please report" request: ok, or kind ('cant_check' / 'signed_out') with a reason."""

    def __init__(self, ok: bool, kind: str | None = None, reason: str | None = None):
        self.ok, self.kind, self.reason = ok, kind, reason

    def __repr__(self):
        return f"CommandResult({self.ok}, {self.kind!r}, {self.reason!r})"


SIGNED_OUT_WORDS = ("not registered", "unregistered", "notregistered", "registration", "invalid token",
                    "invalid push", "push_token", "not connected to local push")


def classify(status, body: bytes) -> CommandResult:
    """The notify call's answer → CommandResult. A missing service or a registration error: the Companion app is
    signed out (or the service was renamed); anything else that isn't a 2xx: Home Assistant couldn't send it."""
    if status is not None and 200 <= status < 300:
        return CommandResult(True)
    try:
        msg = json.loads(body or b"{}").get("message") or ""
    except (ValueError, AttributeError):
        msg = (body or b"").decode("utf-8", "replace")[:200]
    msg = str(msg)[:300]
    low = msg.lower()
    if status in (400, 404) and "not found" in low:
        return CommandResult(False, "signed_out", "Home Assistant has no notify service for this phone (the Companion "
                                                  "app signed out or removed, or the device renamed).")
    if any(w in low for w in SIGNED_OUT_WORDS):
        return CommandResult(False, "signed_out", f"The phone's push registration was refused: {msg}")
    if status is None:
        return CommandResult(False, "cant_check", "Home Assistant didn't answer.")
    if "rate limit" in low or status == 429:
        return CommandResult(False, "cant_check", "The push service's rate limit was reached.")
    return CommandResult(False, "cant_check", "Home Assistant couldn't send the request — check remote access / "
                                              "Nabu Casa." + (f" ({msg})" if msg else ""))


def ws_commands(commands: list[dict], *, url: str | None = None, token: str | None = None,
                timeout: float = 20) -> list:
    """Run WebSocket commands on one short connection → their results in order (None for a failed one).
    Raises OSError / ha_ws.WSClosed when Home Assistant can't be reached."""
    ws = ha_ws.MiniWS(url or config.SUPERVISOR_CORE_WS, timeout=timeout)
    try:
        ws.sock.settimeout(timeout)

        def nxt():
            while True:
                m = ws.recv()
                if m is not None:
                    return m
        if nxt().get("type") != "auth_required":
            raise ha_ws.WSClosed("unexpected greeting")
        ws.send({"type": "auth", "access_token": token if token is not None else config.SUPERVISOR_TOKEN})
        if nxt().get("type") != "auth_ok":
            raise ha_ws.WSClosed("auth refused")
        for i, c in enumerate(commands, 1):
            ws.send({"id": i, **c})
        results: dict = {}
        while len(results) < len(commands):
            m = nxt()
            if m.get("type") == "result" and isinstance(m.get("id"), int):
                results[m["id"]] = m.get("result") if m.get("success") else None
        return [results.get(i) for i in range(1, len(commands) + 1)]
    finally:
        ws.close()


class HASource:
    """The real Home Assistant (through the Supervisor)."""

    def available(self) -> bool:
        return bool(config.SUPERVISOR_TOKEN)

    def registries(self) -> dict | None:
        """{"entries": [...], "devices": [...], "entities": [...]} or None when Home Assistant can't be asked."""
        if not self.available():
            return None
        try:
            entries, devices, entities = ws_commands([
                {"type": "config_entries/get", "domain": "mobile_app"},
                {"type": "config/device_registry/list"},
                {"type": "config/entity_registry/list"}])
        except Exception as e:                                   # noqa: BLE001 — retried on the next round
            logger.warning("Couldn't read Home Assistant's registries (%s).", type(e).__name__)
            return None
        if devices is None or entities is None:
            logger.warning("Home Assistant refused to list its registries.")
            return None
        return {"entries": entries or [], "devices": devices, "entities": entities}

    def states(self, max_age: float = 20) -> list | None:
        return ha_client.fetch_states_blocking(max_age=max_age, what="states")

    def send_command(self, service: str) -> CommandResult:
        if not self.available():
            return CommandResult(False, "cant_check", "This app isn't running inside Home Assistant.")
        status, body = ha_client.request("POST", f"/services/notify/{service}", {"message": COMMAND}, timeout=15)
        return classify(status, body)

    def notify(self, service: str, payload: dict) -> CommandResult:
        """One notify call to a phone ({"message", "title"?, "data"?}): a command or a notification."""
        if not self.available():
            return CommandResult(False, "cant_check", "This app isn't running inside Home Assistant.")
        status, body = ha_client.request("POST", f"/services/notify/{service}", payload, timeout=15)
        return classify(status, body)

    def ha_notification(self, title: str, message: str, notification_id: str) -> bool:
        if not self.available():
            return False
        status, _ = ha_client.request("POST", "/services/persistent_notification/create",
                                      {"title": title, "message": message, "notification_id": notification_id})
        return status is not None and 200 <= status < 300

    def fire_event(self, event_type: str, data: dict) -> bool:
        if not self.available():
            return False
        status, _ = ha_client.request("POST", f"/events/{event_type}", data)
        return status is not None and 200 <= status < 300


SOURCE = HASource()
