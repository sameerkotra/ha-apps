"""Emergency access (SPEC §12.4).

You mark items in your Personal vault "Include in emergency access". The
server keeps your **Emergency vault** (random password, owned by you, hidden
from your own lists) in step with the marked items every time your Personal
vault is saved.

You choose **emergency contacts** (active people) and a waiting period. The
Emergency vault's password is sealed to each contact's public key at once,
but the server keeps that sealed copy to itself until the contact has asked
and the waiting period has passed without you denying it — then it goes into
their key ring and the Emergency vault appears for them, read-only. You can
also approve a request straight away, or remove a contact at any time (a
contact who already had access makes the Emergency vault get a new password).

Nothing here decrypts anything while you're away: the mirror is only rebuilt
while you're unlocked, and the release is copying one sealed box.
"""
import hashlib
import hmac
from datetime import timedelta

from fastapi import HTTPException

from . import alerts, config, db, items, kdbx, keyring, passwords, service, sessions, storage

FLAG = "HouseholdVault.Emergency"          # entry CustomData: "1" = include in emergency access
REQUEST_AGAIN_AFTER = timedelta(days=1)    # after a denial


# ---------------------------------------------------------------------------
# The Emergency vault
# ---------------------------------------------------------------------------

def vault_of(conn, owner_id: str):
    return conn.execute("SELECT * FROM vaults WHERE kind = 'emergency' AND owner_user_id = ?", (owner_id,)).fetchone()


def open_mine(conn, s: sessions.Session, create: bool = False) -> sessions.OpenVault | None:
    """The owner's Emergency vault, open (made on first use when `create`)."""
    v = vault_of(conn, s.user_id)
    if v is None:
        if not create:
            return None
        pw = passwords.random_vault_password()
        vid = storage.new_vault_row(conn, "emergency", "random", "Emergency", s.user_id)
        service.add_member(conn, vid, s.user_id, "owner", s.user_id)
        service.create_vault_file(conn, vid, "Emergency", pw, "random", s.user_id)
        ov = service.open_vault(conn, vid, pw)
        service.remember(conn, vid, s.user_id, pw, 1)
        sessions.attach(s, ov)
        db.audit(conn, "created", s.user_id, vid)
        return ov
    ov = sessions.get_open(v["id"])
    if ov is None:
        k = conn.execute("SELECT sealed FROM vault_keys WHERE vault_id = ? AND user_id = ?", (v["id"], s.user_id)).fetchone()
        if k is None or s.private_key is None:
            raise HTTPException(409, "Your emergency items can't be opened right now — lock and unlock again.")
        ov = service.open_vault(conn, v["id"], keyring.unseal(s.private_key, k["sealed"], v["id"]))
    sessions.attach(s, ov)
    return ov


def marked(d: kdbx.Database) -> list:
    return [e for e in items.all_live_entries(d) if d.custom_data(e).get(FLAG) == "1"]


def _fingerprint(key: str, d: kdbx.Database, entries: list) -> str:
    """Keyed with the Emergency vault's own (random) password, so the stored value says nothing."""
    import xml.etree.ElementTree as ET
    mac = hmac.new(key.encode(), digestmod=hashlib.sha256)
    for e in entries:
        mac.update("/".join(d.group_path(d.parent.get(e))).encode() + b"\0")
        clone = ET.fromstring(ET.tostring(e))
        for tag in ("History", "Times"):
            for x in clone.findall(tag):
                clone.remove(x)
        mac.update(ET.tostring(clone) + b"\0")
    return mac.hexdigest()


def sync(conn, owner_id: str, actor: str | None) -> bool:
    """Rebuild the owner's Emergency vault from the marked Personal items, if it changed.
    Needs both open (the owner is unlocked). Returns True when it saved a new version."""
    v = vault_of(conn, owner_id)
    personal = service.personal_of(conn, owner_id)
    if v is None or personal is None:
        return False
    pov, eov = sessions.get_open(personal["id"]), sessions.get_open(v["id"])
    if pov is None or eov is None:
        return False
    with pov.lock:
        entries = marked(pov.db)
        fp = _fingerprint(eov.password, pov.db, entries)
        if f"{fp}:{v['version']}" == (v["mirror_fp"] or ""):     # same items, and nobody else wrote the mirror
            return False
        fresh = kdbx.Database.create("Emergency")
        for e in entries:
            target = fresh.root_group
            for name in pov.db.group_path(pov.db.parent.get(e)):
                found = next((g for g in target if g.tag == "Group" and g.findtext("Name") == name), None)
                target = found if found is not None else fresh.add_group(target, name)
            clone = pov.db.copy_entry_into(fresh, e, target, new_id=False)
            h = clone.find("History")
            if h is not None:
                for x in list(h):
                    h.remove(x)
            fresh.set_custom_data(clone, FLAG, None)
        fresh.reindex()
    with eov.lock:
        fresh.kdf = eov.db.kdf
        fresh.transformed = None
        eov.db = fresh
        service.save(conn, eov, actor)
    version = conn.execute("SELECT version FROM vaults WHERE id = ?", (v["id"],)).fetchone()["version"]
    conn.execute("UPDATE vaults SET mirror_fp = ? WHERE id = ?", (f"{fp}:{version}", v["id"]))
    return True


def after_save(conn, vault_id: str, actor: str | None) -> None:
    """Called after every vault save: a Personal vault's owner may have emergency items."""
    v = service.vault_row(conn, vault_id)
    if v is not None and v["kind"] == "personal" and vault_of(conn, v["owner_user_id"]) is not None:
        sync(conn, v["owner_user_id"], actor)


def set_flag(conn, s: sessions.Session, vault_id: str, item_id: str, include: bool) -> None:
    personal = service.personal_of(conn, s.user_id)
    if personal is None or personal["id"] != vault_id:
        raise HTTPException(409, "Only items in your own Personal vault can be included in emergency access.")
    pov = sessions.get_open(vault_id)
    open_mine(conn, s, create=include)
    with pov.lock:
        e = items.find_entry(pov.db, item_id)
        if pov.db.in_recycle_bin(e):
            raise HTTPException(409, "That item is in the Trash.")
        pov.db.set_custom_data(e, FLAG, "1" if include else None)
        service.save(conn, pov, s.user_id)          # → after_save → sync


# ---------------------------------------------------------------------------
# Contacts
# ---------------------------------------------------------------------------

def _row(conn, owner_id: str, contact_id: str):
    return conn.execute("SELECT * FROM emergency_contacts WHERE owner_id = ? AND contact_id = ?",
                        (owner_id, contact_id)).fetchone()


def release_at(row) -> str | None:
    t = config.parse_iso(row["requested_at"])
    if row["status"] != "requested" or t is None:
        return None
    return (t + timedelta(days=row["wait_days"])).strftime("%Y-%m-%dT%H:%M:%S.000Z")


def add_contact(conn, s: sessions.Session, contact_id: str, wait_days: int) -> None:
    if contact_id == s.user_id:
        raise HTTPException(422, "Choose someone else.")
    c = service.user_row(conn, contact_id)
    if not c or c["disabled"] or c["status"] != "active" or not c["public_key"]:
        raise HTTPException(409, "They need to be set up (with their own master password) first.")
    ov = open_mine(conn, s, create=True)
    existing = _row(conn, s.user_id, contact_id)
    if existing:
        conn.execute("UPDATE emergency_contacts SET wait_days = ? WHERE owner_id = ? AND contact_id = ?",
                     (wait_days, s.user_id, contact_id))
        return
    n = conn.execute("SELECT COUNT(*) FROM emergency_contacts WHERE owner_id = ?", (s.user_id,)).fetchone()[0]
    if n >= 10:
        raise HTTPException(409, "You can have at most 10 emergency contacts.")
    conn.execute("INSERT INTO emergency_contacts (owner_id, contact_id, emergency_vault_id, wait_days, sealed, key_epoch, "
                 "status, created_at) VALUES (?, ?, ?, ?, ?, ?, 'ready', ?)",
                 (s.user_id, contact_id, ov.vault_id, wait_days, keyring.seal(c["public_key"], ov.password, ov.vault_id),
                  ov.key_epoch, config.now_iso()))
    db.audit(conn, "emergency_added", contact_id, ov.vault_id, s.user_id)
    sync(conn, s.user_id, s.user_id)
    owner = alerts.name_of(conn, s.user_id)
    alerts.send(conn, [contact_id], f"{owner} made you an emergency contact in Household Vault. If something happens "
                f"to {owner}, you can ask for their emergency items (Settings → Emergency access).", kind="emergency")


def remove_contact(conn, owner_id: str, contact_id: str, actor: str | None, notify: bool = True) -> None:
    """Revoke: a contact who already had access is removed from the Emergency vault, which gets a new password."""
    row = _row(conn, owner_id, contact_id)
    if row is None:
        raise HTTPException(404, "They aren't one of your emergency contacts.")
    conn.execute("DELETE FROM emergency_contacts WHERE owner_id = ? AND contact_id = ?", (owner_id, contact_id))
    if row["status"] == "granted":
        service.remove_member(conn, row["emergency_vault_id"], contact_id, actor, audit_action=None)
    db.audit(conn, "emergency_revoked", contact_id, row["emergency_vault_id"], actor)
    if notify:
        alerts.send(conn, [contact_id], f"{alerts.name_of(conn, owner_id)} removed you as an emergency contact "
                    "in Household Vault.", kind="emergency")


def request(conn, contact_id: str, owner_id: str) -> dict:
    row = _row(conn, owner_id, contact_id)
    if row is None:
        raise HTTPException(404, "They haven't made you an emergency contact.")
    if row["status"] in ("requested", "granted"):
        return dict(row)
    if row["status"] == "denied":
        decided = config.parse_iso(row["decided_at"])
        if decided and config.utcnow() - decided < REQUEST_AGAIN_AFTER:
            raise HTTPException(429, "Your last request was denied — you can ask again tomorrow.")
    conn.execute("UPDATE emergency_contacts SET status = 'requested', requested_at = ?, decided_at = NULL "
                 "WHERE owner_id = ? AND contact_id = ?", (config.now_iso(), owner_id, contact_id))
    db.audit(conn, "emergency_requested", owner_id, row["emergency_vault_id"], contact_id)
    row = _row(conn, owner_id, contact_id)
    when = config.parse_iso(release_at(row))
    alerts.send(conn, [owner_id], f"{alerts.name_of(conn, contact_id)} asked for emergency access to your Household "
                f"Vault. Unless you deny it, they get your emergency items on {when:%d %b %Y at %H:%M} UTC "
                "(Settings → Emergency access).", kind="emergency")
    return dict(row)


def deny(conn, owner_id: str, contact_id: str) -> None:
    row = _row(conn, owner_id, contact_id)
    if row is None or row["status"] != "requested":
        raise HTTPException(409, "There's no request to deny.")
    conn.execute("UPDATE emergency_contacts SET status = 'denied', decided_at = ? WHERE owner_id = ? AND contact_id = ?",
                 (config.now_iso(), owner_id, contact_id))
    db.audit(conn, "emergency_denied", contact_id, row["emergency_vault_id"], owner_id)
    alerts.send(conn, [contact_id], f"{alerts.name_of(conn, owner_id)} denied your request for emergency access.",
                kind="emergency")


def approve(conn, owner_id: str, contact_id: str) -> None:
    row = _row(conn, owner_id, contact_id)
    if row is None or row["status"] != "requested":
        raise HTTPException(409, "There's no request to approve.")
    grant(conn, row, owner_id)


def grant(conn, row, actor: str | None) -> bool:
    """Release: the sealed password goes into the contact's key ring; they get the Emergency vault read-only.
    Only from 'requested' (a denial that got there first wins). Returns whether it granted."""
    vid = row["emergency_vault_id"]
    if not conn.execute("UPDATE emergency_contacts SET status = 'granted', decided_at = ? WHERE owner_id = ? AND "
                        "contact_id = ? AND status = 'requested'",
                        (config.now_iso(), row["owner_id"], row["contact_id"])).rowcount:
        return False
    row = _row(conn, row["owner_id"], row["contact_id"])
    service.add_member(conn, vid, row["contact_id"], "viewer", actor)
    conn.execute("INSERT INTO vault_keys (vault_id, user_id, sealed, key_epoch, created_at) VALUES (?, ?, ?, ?, ?) "
                 "ON CONFLICT(vault_id, user_id) DO UPDATE SET sealed = excluded.sealed, key_epoch = excluded.key_epoch",
                 (vid, row["contact_id"], row["sealed"], row["key_epoch"], config.now_iso()))
    db.audit(conn, "emergency_granted", row["contact_id"], vid, actor or row["owner_id"])
    owner = alerts.name_of(conn, row["owner_id"])
    alerts.send(conn, [row["contact_id"]], f"You now have {owner}'s emergency items in Household Vault "
                "(read-only). Unlock to see them.", kind="emergency")
    alerts.send(conn, [row["owner_id"]], f"{alerts.name_of(conn, row['contact_id'])} now has your emergency items "
                "in Household Vault. Remove them in Settings → Emergency access if that's wrong.", kind="emergency")
    # already unlocked somewhere: open it for them now
    for us in sessions.user_sessions(row["contact_id"]):
        if us.private_key is None:
            continue
        try:
            ov = service.open_vault(conn, vid, keyring.unseal(us.private_key, row["sealed"], vid))
            sessions.attach(us, ov)
        except Exception:
            pass                                  # it opens at their next unlock
    return True


def on_hold(conn, owner_id: str) -> bool:
    """While an admin has turned the owner's access off, nothing is released: they couldn't deny it (§12.4)."""
    u = service.user_row(conn, owner_id)
    return u is None or bool(u["disabled"]) or u["status"] != "active"


def release_due(conn) -> int:
    """Grant every request whose waiting period has passed (every minute, and when someone looks)."""
    n = 0
    now = config.utcnow()
    for row in conn.execute("SELECT * FROM emergency_contacts WHERE status = 'requested'").fetchall():
        at = config.parse_iso(release_at(row))
        if at is not None and at <= now and not on_hold(conn, row["owner_id"]):
            if grant(conn, row, None):
                n += 1
    return n


def reseal(conn, ov: sessions.OpenVault) -> None:
    """After the Emergency vault gets a new password: every contact's sealed copy follows."""
    for row in conn.execute("SELECT e.contact_id, u.public_key FROM emergency_contacts e JOIN users u ON u.id = e.contact_id "
                            "WHERE e.emergency_vault_id = ?", (ov.vault_id,)).fetchall():
        if row["public_key"]:
            conn.execute("UPDATE emergency_contacts SET sealed = ?, key_epoch = ? WHERE emergency_vault_id = ? AND contact_id = ?",
                         (keyring.seal(row["public_key"], ov.password, ov.vault_id), ov.key_epoch, ov.vault_id,
                          row["contact_id"]))


def forget_person(conn, user_id: str, as_owner: bool, actor: str | None) -> None:
    """Disabled or reset: as a contact they're revoked everywhere; a reset owner's Emergency vault goes."""
    for row in conn.execute("SELECT owner_id FROM emergency_contacts WHERE contact_id = ?", (user_id,)).fetchall():
        remove_contact(conn, row["owner_id"], user_id, actor, notify=False)
    if as_owner:
        v = vault_of(conn, user_id)
        if v is not None:
            sessions.close(v["id"])
            conn.execute("DELETE FROM vaults WHERE id = ?", (v["id"],))
            storage.delete_vault_files(v["id"])


# ---------------------------------------------------------------------------
# What the Settings page shows
# ---------------------------------------------------------------------------

def overview(conn, s: sessions.Session) -> dict:
    uid = s.user_id
    release_due(conn)
    v = vault_of(conn, uid)
    personal = service.personal_of(conn, uid)
    pov = sessions.get_open(personal["id"]) if personal else None
    count = None
    if pov is not None:
        with pov.lock:
            count = len(marked(pov.db))
    contacts = []
    for r in conn.execute("SELECT e.*, u.name FROM emergency_contacts e JOIN users u ON u.id = e.contact_id "
                          "WHERE e.owner_id = ? ORDER BY u.name COLLATE NOCASE", (uid,)):
        contacts.append({"id": r["contact_id"], "name": r["name"], "waitDays": r["wait_days"], "status": r["status"],
                         "requestedAt": r["requested_at"], "decidedAt": r["decided_at"], "releaseAt": release_at(r)})
    trusted = []
    for r in conn.execute("SELECT e.*, u.name FROM emergency_contacts e JOIN users u ON u.id = e.owner_id "
                          "WHERE e.contact_id = ? ORDER BY u.name COLLATE NOCASE", (uid,)):
        trusted.append({"ownerId": r["owner_id"], "name": r["name"], "waitDays": r["wait_days"], "status": r["status"],
                        "onHold": on_hold(conn, r["owner_id"]),
                        "requestedAt": r["requested_at"], "decidedAt": r["decided_at"], "releaseAt": release_at(r),
                        "vaultId": r["emergency_vault_id"] if r["status"] == "granted" else None})
    return {"mine": {"vaultId": v["id"] if v else None, "itemCount": count, "contacts": contacts},
            "trustedBy": trusted}
