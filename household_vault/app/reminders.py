"""Expiry reminders.

Items can have an expiry date (KeePass's own Times/ExpiryTime, so KeePassXC
shows it too) and "remind me N days before" (default 30). Reminders must go
out while nobody is unlocked, so whenever a vault is opened or saved the
server copies just enough into the `reminders` table: the item's id, the
date, N and (unless an admin turned it off) its title — nothing else from the
vault, and nothing at all for items set to "don't remind me". A background pass
(every half hour) sends "expires in N days" and then "expires today" /
"expired" to the vault's members who can be notified (their phone in Home
Assistant, or an extra notify service — see alerts.py) and haven't turned
expiry reminders off.
"""
import datetime

from . import alerts, items, settings

LONG_EXPIRED = 30          # days: things that expired long ago when first seen are not announced


def refresh(conn, vault_id: str, d) -> None:
    """Bring the vault's rows in line with its items (call with the vault's lock held)."""
    kind = conn.execute("SELECT kind FROM vaults WHERE id = ?", (vault_id,)).fetchone()
    if kind is None or kind["kind"] == "emergency":          # a mirror: the Personal vault reminds
        return
    named = settings.all_values(conn)["reminder_titles"]
    want = {}
    for e in items.all_live_entries(d):
        day = items.expires_on(d, e)
        if day is None:
            continue
        days = items.remind_days(d, e)
        if not days:                                  # "don't remind me": nothing is kept about it
            continue
        title = (d.get_field(e, "Title") or "(no name)")[:200] if named else ""
        want[items.to_id(e.findtext("UUID"))] = (title, day.isoformat(), days)
    have = {r["item_id"]: r for r in conn.execute("SELECT * FROM reminders WHERE vault_id = ?", (vault_id,))}
    today = datetime.date.today()
    for iid, (title, day, days) in want.items():
        old = have.get(iid)
        if old is not None and old["expires_on"] == day and old["remind_days"] == days:
            if old["title"] != title:
                conn.execute("UPDATE reminders SET title = ? WHERE vault_id = ? AND item_id = ?", (title, vault_id, iid))
            continue
        stage = 2 if (datetime.date.fromisoformat(day) - today).days < -LONG_EXPIRED else 0
        conn.execute("INSERT INTO reminders (vault_id, item_id, title, expires_on, remind_days, stage) VALUES (?, ?, ?, ?, ?, ?) "
                     "ON CONFLICT(vault_id, item_id) DO UPDATE SET title = excluded.title, expires_on = excluded.expires_on, "
                     "remind_days = excluded.remind_days, stage = excluded.stage", (vault_id, iid, title, day, days, stage))
    for iid in set(have) - set(want):
        conn.execute("DELETE FROM reminders WHERE vault_id = ? AND item_id = ?", (vault_id, iid))


def _when(day: datetime.date) -> str:
    return f"{day.day} {day:%b %Y}"


def run(conn, today: datetime.date | None = None) -> int:
    """Send what's due. Returns how many reminders went out."""
    today = today or datetime.date.today()
    sent = 0
    for r in conn.execute("SELECT * FROM reminders WHERE stage < 2").fetchall():
        day = datetime.date.fromisoformat(r["expires_on"])
        left = (day - today).days
        if left <= 0:
            stage = 2
            text = "expires today" if left == 0 else f"expired on {_when(day)}"
        elif r["remind_days"] and left <= r["remind_days"] and r["stage"] < 1:
            stage = 1
            text = f"expires in {left} day{'' if left == 1 else 's'}, on {_when(day)}"
        else:
            continue
        members = [m["user_id"] for m in conn.execute("SELECT user_id FROM vault_members WHERE vault_id = ?", (r["vault_id"],))]
        what = r["title"] or "An item"
        alerts.send(conn, members, f"🗓 {what} ({alerts.vault_label(conn, r['vault_id'])}) {text}.", kind="expiry")
        sent += 1
        conn.execute("UPDATE reminders SET stage = ? WHERE vault_id = ? AND item_id = ?", (stage, r["vault_id"], r["item_id"]))
    return sent
