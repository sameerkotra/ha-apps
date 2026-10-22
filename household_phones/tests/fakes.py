"""A fake Home Assistant for the app's tests: the registries, states and the notify command answered from memory
(it replaces ha_source.SOURCE), plus events shaped as Home Assistant sends them."""
import secrets

from app import ha_source

PIXEL_SENSORS = ("battery_level", "battery_state", "is_charging", "last_reboot", "last_update", "wifi_connection",
                 "network_type", "interactive", "internal_storage", "last_notification", "geocoded_location")


class FakeSource:
    def __init__(self):
        self.entries, self.devices, self.entities = [], [], []
        self.state = {}               # entity_id -> state dict
        self.commands = []            # notify services asked to report
        self.notified = []            # (service, payload) of every other notify call (phone actions)
        self.notify_result = None     # a CommandResult to answer them with (None: sent)
        self.result = None            # a CommandResult to answer with (None: sent)
        self.notes = []               # Home Assistant notifications
        self.events = []
        self.fail_registries = False

    def available(self):
        return True

    def registries(self):
        if self.fail_registries:
            return None
        return {"entries": list(self.entries), "devices": list(self.devices), "entities": list(self.entities)}

    def states(self, max_age=20):
        return [{"entity_id": k, **v} for k, v in self.state.items()]

    def send_command(self, service):
        self.commands.append(service)
        return self.result or ha_source.CommandResult(True)

    def notify(self, service, payload):
        self.notified.append((service, payload))
        return self.notify_result or ha_source.CommandResult(True)

    def messages(self):
        """The `message` of each notify call so far (commands and notifications), in order."""
        return [p.get("message") for _, p in self.notified]

    def ha_notification(self, title, message, notification_id):
        self.notes.append((title, message))
        return True

    def fire_event(self, event_type, data):
        self.events.append((event_type, data))
        return True

    # -- building a household
    def add_device(self, name, *, model="Pixel 8", manufacturer="Google", user_id=None, sensors=PIXEL_SENSORS,
                   disabled=(), os_name=None, tracker=True, sw="15") -> str:
        dev_id = secrets.token_hex(8)
        entry_id = secrets.token_hex(8)
        webhook = secrets.token_hex(32)
        data = {"device_name": name, "app_version": "2026.10.1-full"}
        if user_id:
            data["user_id"] = user_id
        if os_name:
            data["os_name"] = os_name
        self.entries.append({"entry_id": entry_id, "domain": "mobile_app", "title": name, "data": data})
        self.devices.append({"id": dev_id, "config_entries": [entry_id], "identifiers": [["mobile_app", dev_id]],
                             "manufacturer": manufacturer, "model": model, "name": name, "name_by_user": None,
                             "sw_version": sw})
        slug = name.lower().replace(" ", "_").replace("'", "")
        for key in sensors:
            eid = f"sensor.{slug}_{key}"
            self.entities.append({"entity_id": eid, "device_id": dev_id, "platform": "mobile_app",
                                  "unique_id": f"{webhook}_{key}", "disabled_by": "user" if key in disabled else None,
                                  "name": None, "original_name": key.replace("_", " ").title()})
            self.state[eid] = {"state": "unknown", "attributes": {}, "last_updated": "2026-01-01T00:00:00+00:00",
                               "last_reported": "2026-01-01T00:00:00+00:00"}
        if tracker:
            eid = f"device_tracker.{slug}"
            self.entities.append({"entity_id": eid, "device_id": dev_id, "platform": "mobile_app", "unique_id": webhook,
                                  "disabled_by": None})
            self.state[eid] = {"state": "home", "attributes": {"latitude": 1.5, "longitude": 2.5}}
        return dev_id

    def eid(self, name, key):
        return f"sensor.{name.lower().replace(' ', '_').replace(chr(39), '')}_{key}"


def iso(ts: float) -> str:
    from datetime import datetime, timezone
    return datetime.fromtimestamp(ts, timezone.utc).isoformat()


PREVIOUS = object()


def state_event(entity_id, state, attributes=None, *, at: float, etype="state_changed", old=PREVIOUS, stamps=True):
    """A state event as the phone's report makes it: by default a state_changed has an old state (a different value a
    minute earlier); old=None is an entity added or restored; stamps=False leaves out the timestamps."""
    new = {"entity_id": entity_id, "state": state, "attributes": attributes or {}}
    if stamps:
        new.update(last_updated=iso(at), last_reported=iso(at), last_changed=iso(at))
    data = {"entity_id": entity_id, "new_state": new}
    if etype == "state_changed":
        if old is PREVIOUS:
            old = {"entity_id": entity_id, "state": f"{state}-before", "attributes": {}, "last_updated": iso(at - 60),
                   "last_reported": iso(at - 60), "last_changed": iso(at - 60)}
        data["old_state"] = old
    return {"event_type": etype, "data": data}
