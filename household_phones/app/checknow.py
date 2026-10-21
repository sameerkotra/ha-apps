"""Check now: a "please respond" on demand, with a live result (spec/SPEC.md §4).

`start()` records the check and sends the command (or joins a check started in the last 30 seconds); the page polls
`view()` every second or two; a check-in after the start makes it ✅ Online ("replied in 4 s"); after the wait it
is ⏳ No reply and the phone is watched as overdue (asked again, Offline if it stays silent); a request that couldn't
be sent is ⚠️ Can't ask. Every check is in the phone's history: who asked, when, and the result.

Who may: the owner, admins, and — with the App setting — people in the chat the phone forwards into, who see only
the result card (status, battery, network, last forward time).
"""
import json
import logging
import secrets

from fastapi import HTTPException

from . import config, db, monitor, phones, settings

logger = logging.getLogger("checknow")

JOIN_S = 30
VIA = ("app", "chat", "assistant", "home_assistant")


class TooMany(Exception):
    pass


def chat_member(p, user_id: str) -> bool:
    """Is this person in the chat the phone forwards into (from Chat's last answer)?"""
    if not p["fwd_on"] or not settings.get("forwarding_allowed"):
        return False
    if p["fwd_target"] == "my_room":
        return user_id == p["owner_id"]
    try:
        members = json.loads(p["fwd_members"] or "[]")
    except ValueError:
        members = []
    return user_id in members


def may_check(p, user: dict) -> bool:
    if user.get("is_admin") or (p["owner_id"] and p["owner_id"] == user["id"]):
        return True
    return bool(settings.get("check_by_chat_members")) and chat_member(p, user["id"])


def checkable(conn, user: dict) -> list:
    """Other people's phones this person may Check now because they're in its forwarding chat (My phone's cards)."""
    if not settings.get("check_by_chat_members") or not settings.get("forwarding_allowed"):
        return []
    rows = conn.execute("SELECT * FROM phones WHERE retired = 0 AND fwd_on = 1 AND fwd_target = 'chat' AND "
                        "(owner_id IS NULL OR owner_id != ?)", (user["id"],)).fetchall()
    return [p for p in rows if chat_member(p, user["id"])]


def start(pid: str, user: dict | None, *, via: str = "app", by_name: str | None = None, source=None,
          now: float | None = None) -> str:
    """Start a check (or join the one started in the last 30 s) → its id. HTTPException 404/403/409, TooMany. Blocking."""
    now = config.clock() if now is None else now
    with db.get_conn() as conn:
        p = phones.get(conn, pid)
        if p is None or (user is not None and not may_check(p, user)):
            raise HTTPException(404, "No such phone (or you can't check it).")
        if monitor.watching_paused(p, now) and not (user and user["id"] == p["owner_id"]):
            raise HTTPException(409, f"Paused by {phones.first_name(phones.owner_name(conn, p['owner_id']))} "
                                     f"{monitor.until_text(p)}.".replace("  ", " "))
        recent = conn.execute("SELECT id FROM checks WHERE phone_id = ? AND started > ? ORDER BY started DESC LIMIT 1",
                              (pid, now - JOIN_S)).fetchone()
        if recent:
            conn.execute("UPDATE checks SET joined = joined + 1 WHERE id = ?", (recent["id"],))
            return recent["id"]
        n = conn.execute("SELECT COUNT(*) FROM checks WHERE phone_id = ? AND started > ?", (pid, now - 3600)).fetchone()[0]
        if n >= settings.get("check_per_hour"):
            raise TooMany(f"This phone was checked {n} times in the last hour — the most allowed. Try again later.")
        cid = secrets.token_hex(6)
        who = by_name or (user["name"] if user else "Home Assistant")
        conn.execute("INSERT INTO checks (id, phone_id, started, by_user, by_name, via) VALUES (?,?,?,?,?,?)",
                     (cid, pid, now, user["id"] if user else None, who[:80], via if via in VIA else "app"))
        ios = phones.is_ios(p)
        if ios:
            conn.execute("UPDATE checks SET state = 'cant_ask', reason = 'iphone', finished = ? WHERE id = ?", (now, cid))
        phones.history(conn, pid, "check", f"Check now by {who}" + ({"chat": " (from Household Chat)",
                       "assistant": " (Household Assistant)", "home_assistant": " (Home Assistant)"}.get(via, "")) + ".",
                       by=user["id"] if user else None, battery=p["battery"], now=now)
    if ios:
        return cid
    monitor.mark_waiting(pid)
    res = monitor.send_request(pid, source, now, count=False)
    if not res.ok:
        with db.get_conn() as conn:
            conn.execute("UPDATE checks SET state = 'cant_ask', reason = ?, finished = ? WHERE id = ? AND state = 'waiting'",
                         (res.reason or res.kind, now, cid))
    return cid


def finish_due(now: float | None = None) -> int:
    """Checks with no reply after the wait: ⏳ No reply, and the phone is watched as overdue (§4). Blocking."""
    now = config.clock() if now is None else now
    wait = settings.get("check_wait_s")
    done = 0
    with db.get_conn() as conn:
        for c in conn.execute("SELECT * FROM checks WHERE state = 'waiting' AND started <= ?", (now - wait,)).fetchall():
            conn.execute("UPDATE checks SET state = 'no_reply', finished = ? WHERE id = ?", (now, c["id"]))
            conn.execute("UPDATE phones SET overdue_from = COALESCE(overdue_from, ?), requests_sent = MAX(requests_sent, 1), "
                         "last_request_at = ? WHERE id = ?", (c["started"], c["started"], c["phone_id"]))
            done += 1
    if done:
        monitor.WAKE.set()
        _announce_results()
    return done


RESULT_TEXT = {"online": "✅ Online", "no_reply": "⏳ No reply yet", "cant_ask": "⚠️ Can't ask", "waiting": "Asking…"}


def view(cid: str, user: dict | None, now: float | None = None) -> dict:
    """The result card. HTTPException 404 for a check this person may not see."""
    now = config.clock() if now is None else now
    finish_due(now)
    with db.get_conn() as conn:
        c = conn.execute("SELECT * FROM checks WHERE id = ?", (cid[:20],)).fetchone()
        p = conn.execute("SELECT * FROM phones WHERE id = ?", (c["phone_id"],)).fetchone() if c else None
        if c is None or p is None or (user is not None and not may_check(p, user)):
            raise HTTPException(404, "No such check.")
        name = phones.display_name(conn, p)
        owner = phones.owner_name(conn, p["owner_id"])
        fwd = conn.execute("SELECT at, chat_name FROM forwards WHERE phone_id = ? ORDER BY at DESC LIMIT 1",
                           (p["id"],)).fetchone()
        since = conn.execute("SELECT at, chat_name FROM forwards WHERE phone_id = ? AND at >= ? ORDER BY at DESC LIMIT 1",
                             (p["id"], c["started"])).fetchone()
    return card(c, p, name, owner, fwd, since, now)


def card(c, p, name: str, owner: str | None, fwd, since, now: float) -> dict:
    pid = p["id"]
    level, chg = phones.battery(pid), phones.charging(pid)
    if level is None:
        level, chg = p["battery"], (None if p["charging"] is None else bool(p["charging"]))
    out = {"id": c["id"], "phoneId": pid, "phone": name, "owner": owner, "state": c["state"],
           "title": RESULT_TEXT.get(c["state"], c["state"]), "started": c["started"], "byName": c["by_name"],
           "waitS": settings.get("check_wait_s"), "elapsed": round(now - c["started"]),
           "battery": level, "charging": chg, "network": phones.network(pid), "lastReboot": phones.last_reboot(pid),
           "lastSeen": p["last_checkin"], "lastSeenText": config.hhmm(p["last_checkin"]) if p["last_checkin"] else None,
           "ios": phones.is_ios(p), "text": None, "forward": None, "forwardNow": None}
    if c["state"] == "online":
        out["text"] = f"replied in {max(0, round((c['replied'] or now) - c['started']))} s"
    elif c["state"] == "no_reply":
        bits = [f"last seen {config.hhmm(p['last_checkin'])}" if p["last_checkin"] else "never seen"]
        if out["network"]:
            bits.append(f"on {out['network']}")
        if level is not None:
            bits.append(f"battery {level}%")
        out["text"] = "no reply; " + ", ".join(bits)
    elif c["state"] == "cant_ask":
        if c["reason"] == "iphone":
            out["text"] = f"Can't ask an iPhone — last check-in {config.hhmm(p['last_checkin']) or 'never'}"
        else:
            out["text"] = (c["reason"] or "The request couldn't be sent.") + \
                (f" Last check-in {config.hhmm(p['last_checkin'])}." if p["last_checkin"] else "")
    if p["fwd_on"]:
        if fwd and now - fwd["at"] < 24 * 3600:
            out["forward"] = f"last message forwarded {config.hhmm(fwd['at'])} into {fwd['chat_name']}"
        else:
            out["forward"] = "nothing forwarded in the last 24 h"
        if since:
            out["forwardNow"] = f"a message was just forwarded into {since['chat_name']}"
    return out


def _announce_results() -> None:
    """With Sensors in Home Assistant on: a household_phones_check_result event per finished check (automations)."""
    if not settings.get("ha_sensors"):
        return
    from . import ha_source
    with db.get_conn() as conn:
        rows = conn.execute("SELECT c.id, c.state, p.id AS pid FROM checks c JOIN phones p ON p.id = c.phone_id "
                            "WHERE c.via = 'home_assistant' AND c.state != 'waiting' AND c.finished >= ?",
                            (config.clock() - 60,)).fetchall()
    for r in rows:
        ha_source.SOURCE.fire_event("household_phones_check_result", {"phone": r["pid"], "check": r["id"],
                                                                      "result": r["state"]})


def log(conn, pid: str, limit: int = 50) -> list[dict]:
    return [{"at": r["started"], "atText": config.local(r["started"]).strftime("%a %d %b %H:%M"), "by": r["by_name"],
             "via": r["via"], "result": RESULT_TEXT.get(r["state"], r["state"])}
            for r in conn.execute("SELECT * FROM checks WHERE phone_id = ? ORDER BY started DESC LIMIT ?", (pid, limit))]
