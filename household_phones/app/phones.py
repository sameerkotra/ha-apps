"""The household's phones: found in Home Assistant's mobile_app devices, kept in the `phones` table, and described
for the pages (spec/SPEC.md §2, §5).

Discovery reads the registries (ha_source) every few minutes and when Home Assistant says they changed. What Home
Assistant says about a device right now — its entities and their latest states — is kept in memory (`LIVE`), updated
by the state events; the database keeps only what the app decides (kind, owner, settings, status, history).

The Last notification sensor's text is never kept: its state and attributes are blanked in `LIVE` (forwarding gets
each new notification from the event itself, forwarding.py).
"""
import logging
import re
import secrets
import threading

from . import config, db, settings
from .common import ha_notify, ha_people

logger = logging.getLogger("phones")

ID_RE = re.compile(r"^[a-z0-9]{1,16}$")
WEBHOOK_PREFIX = re.compile(r"^[0-9a-f]{20,}_")
NOTIFY_RE = re.compile(r"^[a-z0-9_]{1,120}$")
KINDS = ("phone", "tablet", "ignored")
TABLET_RE = re.compile(r"\b(ipad|tablet|tab ?[a-z0-9]*|sm-[tx]\d+\w*|galaxy tab|fire hd\w*|kindle|lenovo tb\w*|"
                       r"mediapad|matepad|pixel tablet)\b", re.I)
WATCH_RE = re.compile(r"\b(watch|wear ?os|sm-r\d+\w*)\b", re.I)
# Sensor keys (the entity's unique id without the registration's webhook prefix) by group, for the phone page.
GROUP_ORDER = ("battery", "network", "activity", "storage", "location", "restart", "notifications", "other")
GROUP_LABEL = {"battery": "🔋 Battery", "network": "📶 Network", "activity": "🙂 Activity", "storage": "💾 Storage",
               "location": "📍 Location zone", "restart": "🔄 Last reboot and updates", "notifications": "🔔 Notifications",
               "other": "Other"}
_GROUP_RULES = (
    ("battery", r"^(battery|is_charging|charger)"),
    ("network", r"^(wifi|network|mobile_|total_|public_ip|ipv6|sim_|connection_type|ssid|bssid|signal|data_|"
                r"cellular|carrier|bluetooth)"),
    ("activity", r"^(interactive|do_not_disturb|dnd|ringer|audio_mode|phone_state|keyguard|device_locked|screen_|"
                 r"detected_activity|activity|steps|doze|power_save|volume|media_session|next_alarm)"),
    ("storage", r"storage"),
    ("location", r"^(geocoded_location|device_tracker|beacon|high_accuracy|location)"),
    ("restart", r"^(last_reboot|last_update|app_version|os_version)"),
    ("notifications", r"notification"),
)
HIDDEN_KEYS = {"last_notification", "last_removed_notification"}     # their state is a message: never shown here
PLACE_KEYS = ("geocoded_location", "device_tracker")                  # only with the owner's Share my last place
# The sensors this app reads (the setup checklist asks for them).
NEEDED = (("battery_level", "Battery level"), ("battery_state", "Battery state"), ("is_charging", "Charging"),
          ("last_reboot", "Last reboot"))
ALWAYS_CHANGING = (("battery_power", "Battery power"), ("last_update", "Last update trigger"))


def group_of(key: str) -> str:
    for g, rx in _GROUP_RULES:
        if re.search(rx, key):
            return g
    return "other"


def sensor_key(entity: dict) -> str:
    """battery_level, wifi_connection, … from the unique id; device_tracker for the tracker."""
    eid = entity.get("entity_id") or ""
    if eid.startswith("device_tracker."):
        return "device_tracker"
    uid = str(entity.get("unique_id") or "")
    if uid:
        return WEBHOOK_PREFIX.sub("", uid)
    return eid.split(".", 1)[-1]


def guess_kind(name: str, model: str, manufacturer: str) -> str:
    text = " ".join(x for x in (manufacturer, model, name) if x)
    if WATCH_RE.search(text):
        return "ignored"
    if TABLET_RE.search(text):
        return "tablet"
    return "phone"


def guess_os(manufacturer: str, model: str, data: dict) -> str:
    os_name = str(data.get("os_name") or "")
    if os_name:
        return "iOS" if os_name.lower() in ("ios", "ipados", "iphone os") else os_name
    if (manufacturer or "").lower() == "apple" or re.match(r"^(iphone|ipad)", (model or "").lower()):
        return "iOS"
    return "Android"


# --------------------------------------------------------------------------------------------- what HA says now

class Live:
    """Home Assistant's view right now (memory only): devices found, each phone's entities, their latest states."""

    def __init__(self):
        self.lock = threading.RLock()
        self.devices: dict = {}          # device_id -> discovered dict
        self.entity_phone: dict = {}     # entity_id -> phone id
        self.phone_entities: dict = {}   # phone id -> [{entity_id, key, disabled, name}]
        self.states: dict = {}           # entity_id -> {state, attributes, last_reported, last_updated}
        self.default_service: dict = {}  # phone id -> mobile_app_<…>
        self.loaded_at = None

    def reset(self):
        with self.lock:
            self.__init__()


LIVE = Live()


def clean_state(entity_id: str, st: dict | None) -> dict | None:
    """A state as kept in memory: a notification's text is never kept."""
    if not isinstance(st, dict):
        return None
    out = {"state": st.get("state"), "attributes": dict(st.get("attributes") or {}),
           "last_reported": st.get("last_reported"), "last_updated": st.get("last_updated"),
           "last_changed": st.get("last_changed")}
    if entity_id.endswith(("_last_notification", "_last_removed_notification")) or "notification" in entity_id and \
            "count" not in entity_id:
        out["state"] = "(not kept)"
        out["attributes"] = {}
    if entity_id.startswith("device_tracker."):
        out["attributes"] = {k: v for k, v in out["attributes"].items()
                             if k not in ("latitude", "longitude", "gps_accuracy", "altitude", "course", "speed",
                                          "vertical_accuracy")}
    return out


def discover(reg: dict, people: list, notify_actions: set | None) -> list[dict]:
    """The Companion-app devices in Home Assistant's registries, with their entities and owner."""
    entries = {e.get("entry_id"): e for e in (reg.get("entries") or [])
               if isinstance(e, dict) and e.get("domain", "mobile_app") == "mobile_app"}
    tracker_owner = {}
    for p in people or []:
        for ph in p.get("phones") or []:
            if ph.get("tracker") and p.get("userId"):
                tracker_owner[ph["tracker"]] = p["userId"]
    by_device: dict = {}
    for e in reg.get("entities") or []:
        if isinstance(e, dict) and e.get("device_id") and e.get("platform") == "mobile_app":
            by_device.setdefault(e["device_id"], []).append(e)
    out = []
    for d in reg.get("devices") or []:
        if not isinstance(d, dict) or not d.get("id"):
            continue
        ids = [tuple(i) for i in d.get("identifiers") or [] if isinstance(i, (list, tuple)) and len(i) == 2]
        mine = [eid for eid in d.get("config_entries") or [] if eid in entries]
        if not mine and not any(i[0] == "mobile_app" for i in ids):
            continue
        entry = entries.get(mine[0]) if mine else {}
        data = entry.get("data") if isinstance(entry.get("data"), dict) else {}
        name = str(data.get("device_name") or d.get("name") or "Phone")
        ents = []
        owner = data.get("user_id") if isinstance(data.get("user_id"), str) else None
        for e in by_device.get(d["id"], []):
            ents.append({"entity_id": e["entity_id"], "key": sensor_key(e),
                         "disabled": bool(e.get("disabled_by")),
                         "name": e.get("name") or e.get("original_name")})
            if owner is None and e["entity_id"] in tracker_owner:
                owner = tracker_owner[e["entity_id"]]
        manufacturer, model = str(d.get("manufacturer") or ""), str(d.get("model") or "")
        out.append({"device_id": d["id"], "ha_name": str(d.get("name_by_user") or d.get("name") or name)[:80],
                    "device_name": name, "manufacturer": manufacturer[:60], "model": model[:60],
                    "os_name": guess_os(manufacturer, model, data), "os_version": str(d.get("sw_version") or "")[:40] or None,
                    "app_version": str(data.get("app_version") or "")[:40] or None, "owner_id": owner,
                    "notify": ha_people._notify_name(name, notify_actions), "entities": ents})
    return out


def new_id(conn) -> str:
    while True:
        pid = secrets.token_hex(4)
        if not conn.execute("SELECT 1 FROM phones WHERE id = ?", (pid,)).fetchone():
            return pid


def sync(conn, found: list[dict], now: float) -> list[str]:
    """Bring the phones table in line with what Home Assistant has; the ids of devices seen for the first time."""
    guess = bool(settings.get("guess_kind"))
    new, seen = [], set()
    for d in found:
        row = conn.execute("SELECT * FROM phones WHERE device_id = ?", (d["device_id"],)).fetchone()
        if row is None:
            kind = guess_kind(d["device_name"], d["model"], d["manufacturer"]) if guess else "phone"
            pid = new_id(conn)
            # not monitored: it waits under Available devices until an admin adds it (§2); no history until then
            conn.execute("INSERT INTO phones (id, device_id, ha_name, manufacturer, model, os_name, os_version, "
                         "app_version, kind, monitored, owner_id, first_seen, status_reason) "
                         "VALUES (?,?,?,?,?,?,?,?,?,0,?,?,'not_monitored')",
                         (pid, d["device_id"], d["ha_name"], d["manufacturer"], d["model"], d["os_name"],
                          d["os_version"], d["app_version"], kind, d["owner_id"], now))
            new.append(pid)
        else:
            pid = row["id"]
            for col, label in (("os_version", "System"), ("app_version", "Companion app")):
                if row["monitored"] and d[col] and row[col] and d[col] != row[col]:
                    history(conn, pid, "update", f"{label} updated to {d[col]}.", now=now)
            conn.execute("UPDATE phones SET ha_name = ?, manufacturer = ?, model = ?, os_name = ?, "
                         "os_version = COALESCE(?, os_version), app_version = COALESCE(?, app_version), in_registry = 1, "
                         "owner_id = CASE WHEN owner_by_admin = 1 OR ? IS NULL THEN owner_id ELSE ? END WHERE id = ?",
                         (d["ha_name"], d["manufacturer"], d["model"], d["os_name"], d["os_version"], d["app_version"],
                          d["owner_id"], d["owner_id"], pid))
        d["phone_id"] = pid
        seen.add(d["device_id"])
    for row in conn.execute("SELECT id, device_id FROM phones WHERE retired = 0 AND in_registry = 1").fetchall():
        if row["device_id"] not in seen:
            conn.execute("UPDATE phones SET in_registry = 0 WHERE id = ?", (row["id"],))
    return new


def apply_live(found: list[dict], states: list | None) -> None:
    """Remember the entities (and, when read, the states) of the phones just synced."""
    with LIVE.lock:
        LIVE.devices = {d["device_id"]: d for d in found}
        LIVE.entity_phone = {e["entity_id"]: d["phone_id"] for d in found for e in d["entities"]}
        LIVE.phone_entities = {d["phone_id"]: d["entities"] for d in found}
        LIVE.default_service = {d["phone_id"]: d["notify"] for d in found if d.get("notify")}
        if states is not None:
            LIVE.states = {s["entity_id"]: clean_state(s["entity_id"], s) for s in states
                           if isinstance(s, dict) and s.get("entity_id") in LIVE.entity_phone}
        LIVE.loaded_at = config.clock()


def refresh(source=None) -> dict:
    """Read the registries and states and sync the table. Blocking: from a thread. {"ok", "new"}."""
    from . import ha_source
    src = source or ha_source.SOURCE
    reg = src.registries()
    if reg is None:
        return {"ok": False, "new": []}
    try:
        people = ha_people.refresh_blocking()
    except Exception:                                          # noqa: BLE001 — the owner stays as it was
        logger.exception("Reading the people failed")
        people = ha_people.people()
    known = ha_notify.available_notify_targets() if src.available() else None
    found = discover(reg, people or ha_people.people(), known[0] if known else None)
    states = src.states(max_age=0)
    now = config.clock()
    with db.get_conn() as conn:
        new = sync(conn, found, now)
    apply_live(found, states)
    return {"ok": True, "new": new}


# --------------------------------------------------------------------------------------------- reading a phone

def entities(pid: str) -> list[dict]:
    with LIVE.lock:
        return list(LIVE.phone_entities.get(pid, []))


def state_of(pid: str, key: str) -> dict | None:
    with LIVE.lock:
        for e in LIVE.phone_entities.get(pid, []):
            if e["key"] == key and not e["disabled"]:
                return LIVE.states.get(e["entity_id"])
    return None


def value(pid: str, key: str):
    st = state_of(pid, key)
    v = st.get("state") if st else None
    return None if v in (None, "unknown", "unavailable", "") else v


def state_any(pid: str, keys) -> tuple[str | None, dict | None]:
    """The first enabled sensor among `keys` (its key, or an entity id ending in _<key>: the Companion app's unique
    ids differ between versions) → (key, state) or (None, None)."""
    with LIVE.lock:
        ents = [e for e in LIVE.phone_entities.get(pid, []) if not e["disabled"]]
        for k in keys:
            for e in ents:
                if e["key"] == k or e["entity_id"].endswith("_" + k):
                    return k, LIVE.states.get(e["entity_id"])
    return None, None


def value_any(pid: str, keys):
    _, st = state_any(pid, keys)
    v = st.get("state") if st else None
    return None if v in (None, "unknown", "unavailable", "") else v


def enabled_any(pid: str, keys) -> bool | None:
    """True: one of these sensors is on; False: the phone has one but it's off; None: it has none."""
    seen = None
    with LIVE.lock:
        for e in LIVE.phone_entities.get(pid, []):
            if any(e["key"] == k or e["entity_id"].endswith("_" + k) for k in keys):
                if not e["disabled"]:
                    return True
                seen = False
    return seen


def entity_id(pid: str, key: str) -> str | None:
    with LIVE.lock:
        for e in LIVE.phone_entities.get(pid, []):
            if e["key"] == key:
                return e["entity_id"]
    return None


def sensor_enabled(pid: str, key: str) -> bool | None:
    """True / False (off on the phone) / None (the phone has no such sensor)."""
    with LIVE.lock:
        for e in LIVE.phone_entities.get(pid, []):
            if e["key"] == key:
                return not e["disabled"]
    return None


def battery(pid: str):
    v = value(pid, "battery_level")
    try:
        return int(float(v)) if v is not None else None
    except ValueError:
        return None


def charging(pid: str) -> bool | None:
    v = value(pid, "is_charging")
    if v is not None:
        return v == "on" or v is True
    st = value(pid, "battery_state")
    if st is None:
        return None
    return str(st).lower() in ("charging", "full")


def network(pid: str) -> str | None:
    """"Wi-Fi Home", "mobile data", or None."""
    ssid = value(pid, "wifi_connection") or value(pid, "ssid")
    if ssid and ssid not in ("<not connected>", "Not Connected"):
        return f"Wi-Fi {ssid}"
    kind = (value(pid, "network_type") or value(pid, "connection_type") or "").lower()
    if kind in ("cellular", "mobile", "cell"):
        return "mobile data"
    if kind == "wifi":
        return "Wi-Fi"
    if kind:
        return kind
    return None


def last_reboot(pid: str) -> str | None:
    return value(pid, "last_reboot")


def zone(pid: str) -> str | None:
    with LIVE.lock:
        for e in LIVE.phone_entities.get(pid, []):
            if e["entity_id"].startswith("device_tracker."):
                st = LIVE.states.get(e["entity_id"])
                if st and st.get("state") not in (None, "unknown", "unavailable"):
                    return {"home": "Home", "not_home": "Away"}.get(st["state"], str(st["state"]))
    return None


def sensors(pid: str, share_place: bool) -> dict:
    """Every sensor of the phone for the phone page: {"groups": [{id, label, sensors: [{name, value, unit}]}],
    "off": [names]} — states only (no attributes), never a notification's text, the place only when shared."""
    groups: dict = {g: [] for g in GROUP_ORDER}
    off = []
    with LIVE.lock:
        ents = list(LIVE.phone_entities.get(pid, []))
        states = {e["entity_id"]: LIVE.states.get(e["entity_id"]) for e in ents}
    for e in sorted(ents, key=lambda x: (x.get("name") or x["key"])):
        name = e.get("name") or (states.get(e["entity_id"]) or {}).get("attributes", {}).get("friendly_name") \
            or e["key"].replace("_", " ").capitalize()
        if e["disabled"]:
            off.append(name)
            continue
        g = group_of(e["key"])
        st = states.get(e["entity_id"]) or {}
        if e["key"] in HIDDEN_KEYS:
            val = "not shown (it holds a message)"
        elif g == "location" and not share_place:
            val = "not shared by the owner"
        else:
            val = st.get("state")
        unit = (st.get("attributes") or {}).get("unit_of_measurement") if e["key"] not in HIDDEN_KEYS else None
        groups[g].append({"name": str(name)[:80], "value": None if val is None else str(val)[:120],
                          "unit": unit if isinstance(unit, str) else None})
    return {"groups": [{"id": g, "label": GROUP_LABEL[g], "sensors": groups[g]} for g in GROUP_ORDER if groups[g]],
            "off": off}


def notify_service(row) -> str | None:
    if row["notify_service"]:
        return row["notify_service"]
    with LIVE.lock:
        return LIVE.default_service.get(row["id"])


def is_ios(row) -> bool:
    return (row["os_name"] or "").lower() == "ios"


# --------------------------------------------------------------------------------------------- names and history

def owner_name(conn, owner_id: str | None) -> str | None:
    if not owner_id:
        return None
    r = conn.execute("SELECT name FROM users WHERE id = ?", (owner_id,)).fetchone()
    if r:
        return r["name"]
    p = ha_people.person_for(owner_id)
    return p["name"] if p else None


def first_name(name: str | None) -> str:
    return (name or "Someone").split()[0]


def display_name(conn, row) -> str:
    if row["name"]:
        return row["name"]
    owner = owner_name(conn, row["owner_id"])
    what = row["model"] or row["ha_name"] or "phone"
    if owner:
        return f"{first_name(owner)}'s {what}"[:80]
    return (row["ha_name"] or what)[:80]


def history(conn, pid: str, kind: str, text: str, *, by: str | None = None, battery: int | None = None,
            now: float | None = None) -> None:
    conn.execute("INSERT INTO history (phone_id, at, kind, text, by_user, battery) VALUES (?,?,?,?,?,?)",
                 (pid, config.clock() if now is None else now, kind, text[:300], by, battery))


def get(conn, pid: str):
    if not isinstance(pid, str) or not ID_RE.match(pid):
        return None
    return conn.execute("SELECT * FROM phones WHERE id = ? AND retired = 0", (pid,)).fetchone()


def mine(conn, user_id: str) -> list:
    """This person's monitored phones (the only ones with a My phone page)."""
    return conn.execute("SELECT * FROM phones WHERE owner_id = ? AND retired = 0 AND monitored = 1 "
                        "ORDER BY first_seen", (user_id,)).fetchall()


def mine_unmonitored(conn, user_id: str) -> list:
    """This person's devices no admin has added (My phone says only that they aren't monitored)."""
    return conn.execute("SELECT * FROM phones WHERE owner_id = ? AND retired = 0 AND monitored = 0 AND "
                        "kind != 'ignored' AND in_registry = 1 ORDER BY first_seen", (user_id,)).fetchall()
