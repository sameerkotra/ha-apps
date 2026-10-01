"""Small shared helpers for the E-470 toll feature (SPEC.md section 18): how a device and a car are
named, how a pass is identified for duplicate detection, and finding or adding a device.

Two different things, on purpose ("tag" in the trips/Analyze sense means a named trip pattern, so the
transponder is always called a *device* in the UI):

* a **device** (toll_devices row) is what a statement prints as "Device # ... Plate # ...". Devices are
  added automatically as they are found while a statement is read; the plate stored on a device follows
  the newest statement. A heading with no device at all is identified by its plate and state instead.
* a **car** (toll_cars row) is created by the person, who then assigns devices to it. A car can have
  several devices over time and a device can be moved to another car. Until a device is assigned, its
  passes are shown under the device itself ("not assigned").
"""
import hashlib
import sqlite3


def plate_text(plate: str, state: str) -> str:
    return f"{plate}-{state}" if plate and state else (plate or state or "no plate")


def device_label(device) -> str:
    """'device 1234567 · PLATE-CO' — the device as a statement prints it."""
    tag = f"device {device['device_id']}" if device["device_id"] else ""
    plate = plate_text(device["plate"], device["plate_state"]) if (device["plate"] or device["plate_state"]) else ""
    return " · ".join(part for part in (tag, plate) if part) or "unknown device"


def vehicle_label(car_name: str | None, device) -> str:
    """What a pass, trip or total is shown under: the car's name, or the bare device while it has no car."""
    return car_name if car_name else f"{device_label(device)} (not assigned)"


def car_identity(device_id: str, plate: str, state: str) -> str:
    """What makes two headings the same device: the device id, or (no device) the plate and state."""
    device_id = (device_id or "").strip()
    return device_id if device_id else f"{(plate or '').strip().upper()}|{(state or '').strip().upper()}"


def dedupe_key(device_id: str, plate: str, state: str, occurred_at: str, road: str, plaza: str,
               lane: str, direction: str, amount: float) -> str:
    """Identity of one toll pass: the device (never the plate), the moment, the place and the amount. A pass
    that is already imported (an overlapping statement, a re-download, even under a changed plate) has
    the same key."""
    parts = [car_identity(device_id, plate, state), occurred_at, (road or "").upper(), (plaza or "").upper(),
             str(lane or ""), (direction or "").title(), f"{amount:.2f}"]
    return hashlib.sha1("|".join(parts).encode("utf-8")).hexdigest()


def ensure_device(conn: sqlite3.Connection, user_id: str, device_id: str, plate: str, state: str,
                  seen_on: str | None = None) -> tuple[int, bool, tuple[str, str] | None]:
    """(device row id, created, plate change). Values are stored trimmed and upper-case.

    A device is found by its id alone. If the statement shows a different plate, the stored plate and state
    are updated and `plate change` is (old text, new text) — unless the stored plate was already seen on
    a later date (`seen_on` is the date of the latest pass of this device on the statement), so uploading an
    old statement late can't bring back an old plate. A blank plate never overwrites a known one. With no
    device at all the row is found by plate and state. Which car a device belongs to is never touched here."""
    device_id, plate, state = (device_id or "").strip(), (plate or "").strip().upper(), (state or "").strip().upper()
    if device_id:
        row = conn.execute("SELECT * FROM toll_devices WHERE user_id = ? AND device_id = ?", (user_id, device_id)).fetchone()
    else:
        row = conn.execute(
            "SELECT * FROM toll_devices WHERE user_id = ? AND device_id = '' AND plate = ? AND plate_state = ?",
            (user_id, plate, state)).fetchone()

    if row is None:
        cur = conn.execute(
            "INSERT INTO toll_devices (user_id, device_id, plate, plate_state, plate_as_of) VALUES (?, ?, ?, ?, ?)",
            (user_id, device_id, plate, state, seen_on))
        return cur.lastrowid, True, None

    if not device_id or not plate:
        return row["id"], False, None
    as_of = row["plate_as_of"]
    newer = seen_on is None or as_of is None or seen_on >= as_of
    if (row["plate"], row["plate_state"]) != (plate, state):
        if newer:
            conn.execute("UPDATE toll_devices SET plate = ?, plate_state = ?, plate_as_of = ? WHERE id = ?",
                         (plate, state, seen_on or as_of, row["id"]))
            return row["id"], False, (plate_text(row["plate"], row["plate_state"]), plate_text(plate, state))
    elif seen_on and (as_of is None or seen_on > as_of):
        conn.execute("UPDATE toll_devices SET plate_as_of = ? WHERE id = ?", (seen_on, row["id"]))
    return row["id"], False, None


def resolve_devices(conn: sqlite3.Connection, user_id: str, seen: dict[tuple, str | None]) -> tuple[dict, list[str], list[tuple]]:
    """Find or add the device for every (device, plate, state) heading in `seen` ({heading: date of its
    latest pass}). Headings with the same device share one row; the plate used is the one seen most
    recently. Returns ({heading: device row id}, labels of devices added, plate changes as (device, old, new))."""
    groups: dict[str, list[tuple]] = {}
    for key, date in seen.items():
        groups.setdefault(car_identity(*key), []).append((key, date))

    ids: dict[tuple, int] = {}
    added: list[str] = []
    changes: list[tuple] = []
    for items in groups.values():
        (device, plate, state), latest = max(items, key=lambda it: it[1] or "")
        row_id, created, change = ensure_device(conn, user_id, device, plate, state, latest)
        if created:
            added.append(device_label(conn.execute("SELECT * FROM toll_devices WHERE id = ?", (row_id,)).fetchone()))
        if change:
            changes.append((device, change[0], change[1]))
        for key, _ in items:
            ids[key] = row_id
    return ids, added, changes


def notes_text(new_devices: list[str], plate_changes: list[tuple]) -> str | None:
    """The statement's note about devices: which were found for the first time and which plates changed."""
    parts = []
    if new_devices:
        parts.append("New device found: " + "; ".join(new_devices) + " — assign it to a car on the Upload tab")
    if plate_changes:
        parts.append("Plate updated: " + "; ".join(f"device {device} {old} → {new}" for device, old, new in plate_changes))
    return " · ".join(parts) or None
