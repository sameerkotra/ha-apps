"""Children's bedtime (spec/SPEC.md §15.4): admins set bedtime windows on a child's phone (days plus a start and an
end, like do-not-monitor hours, dnm.py) and the parents to tell. When the screen is in use during bedtime — the
Interactive sensor on for longer than the App setting (5 minutes) — the parents get one quiet alert per night per
phone. Optionally Do Not Disturb goes on at bedtime and off at its end. The child sees all of this on My phone.

Only phones whose owner is marked Child (Admin → People); a phone whose owner isn't a child keeps its windows but they
don't apply.
"""
import json
import logging

from . import actions, alerts, auth, config, db, dnm, monitor, phones, settings

logger = logging.getLogger("bedtime")

INTERACTIVE_KEYS = ("interactive", "is_interactive")
DND_MODE = "priority_only"


def windows_of(p) -> list[dict]:
    return dnm.parse(p["bed_windows"])


def parents_of(p) -> list[str]:
    try:
        v = json.loads(p["bed_parents"] or "[]")
    except ValueError:
        return []
    return [x for x in v if isinstance(x, str)] if isinstance(v, list) else []


def active(p, now: float) -> dict | None:
    """Bedtime now? None, or {"minutes": until it ends}."""
    windows = windows_of(p)
    if not windows:
        return None
    d = config.local(now)
    return dnm.state_at(windows, d.weekday() * 1440 + d.hour * 60 + d.minute)


def night_of(now: float) -> str:
    """The night a time belongs to (bedtime 21:00–07:00 is one night): the date 12 hours earlier."""
    return config.local(now - 12 * 3600).date().isoformat()


def summary(windows: list[dict]) -> str | None:
    if not windows:
        return None
    return "Bedtime " + " · ".join(f"{dnm.days_text(w['days'])} {w['start']}–{w['end']}" for w in windows)


def set_bedtime(conn, p, admin: dict, body: dict, now: float) -> None:
    """{windows: [...], parents: [user ids], dnd: bool} on a child's phone (admins). Raises actions.Refused."""
    if not isinstance(body, dict) or set(body) - {"windows", "parents", "dnd"}:
        raise actions.Refused(422, "Send windows, parents and dnd.")
    if not actions.owner_is_child(conn, p):
        raise actions.Refused(409, "Bedtime is for children's phones: mark the owner as a child in Admin → People.")
    sets = {}
    if "windows" in body:
        try:
            windows = [dict(w, pauseForwarding=False) if isinstance(w, dict) else w for w in body["windows"]] \
                if isinstance(body["windows"], list) else body["windows"]
            windows = dnm.clean(windows)
        except dnm.Invalid as e:
            raise actions.Refused(422, str(e))
        sets["bed_windows"] = json.dumps(windows) if windows else None
    if "parents" in body:
        ids = body["parents"]
        if not isinstance(ids, list) or len(ids) > 10 or not all(isinstance(x, str) for x in ids):
            raise actions.Refused(422, "Choose the parents to tell.")
        for uid in ids:
            if not conn.execute("SELECT 1 FROM users WHERE id = ?", (uid,)).fetchone():
                raise actions.Refused(422, "Choose people who have opened the app.")
        sets["bed_parents"] = json.dumps(list(dict.fromkeys(ids)))
    if "dnd" in body:
        if not isinstance(body["dnd"], bool):
            raise actions.Refused(422, "dnd is true or false.")
        sets["bed_dnd"] = int(body["dnd"])
    if sets:
        conn.execute("UPDATE phones SET " + ", ".join(f"{k} = ?" for k in sets) + " WHERE id = ?", (*sets.values(), p["id"]))
        p2 = conn.execute("SELECT * FROM phones WHERE id = ?", (p["id"],)).fetchone()
        text = summary(windows_of(p2)) or "No bedtime"
        phones.history(conn, p["id"], "owner", f"{text} (set by {admin['name']})" + (
            "; Do Not Disturb at bedtime" if p2["bed_dnd"] else "") + ".", by=admin["id"], now=now)


def view(conn, p, now: float) -> dict:
    """What the child and the admins see about bedtime."""
    names = []
    for uid in parents_of(p):
        r = conn.execute("SELECT name FROM users WHERE id = ?", (uid,)).fetchone()
        if r:
            names.append(r["name"])
    windows = windows_of(p)
    on = active(p, now) if windows else None
    return {"applies": actions.owner_is_child(conn, p), "windows": windows, "text": summary(windows),
            "parents": parents_of(p), "parentNames": names, "dnd": bool(p["bed_dnd"]), "now": bool(on),
            "afterMin": settings.get("bedtime_after_min"),
            "interactive": phones.enabled_any(p["id"], INTERACTIVE_KEYS)}


def _quiet_alert(p, name: str, minutes: int, since: float, now: float) -> dict:
    with db.get_conn() as conn:
        people = []
        for uid in parents_of(p):
            r = conn.execute("SELECT * FROM users WHERE id = ?", (uid,)).fetchone()
            if r:
                people.append(auth.user_dict(r))
    if not people:
        return {"sent": [], "skipped": "no parents chosen"}
    text = f"{name} has been in use for {minutes} minutes at bedtime (since {config.hhmm(since)})."
    return alerts.send("bedtime", p["id"], text, now=now, only=people,
                       data={"importance": "low", "push": {"interruption-level": "passive"}})


def tick(now: float | None = None, source=None) -> dict:
    """Each child's phone with bedtime: Do Not Disturb at its start and end (when chosen), and the screen-in-use
    alert once per night."""
    now = config.clock() if now is None else now
    out = {"alerts": 0, "dnd": 0}
    after = settings.get("bedtime_after_min") * 60
    with db.get_conn() as conn:
        rows = conn.execute("SELECT * FROM phones WHERE retired = 0 AND monitored = 1 AND (bed_windows IS NOT NULL "
                            "OR bed_dnd_set = 1 OR bed_screen_since IS NOT NULL)").fetchall()
        rows = [(p, actions.owner_is_child(conn, p), phones.display_name(conn, p)) for p in rows]
    for p, child, name in rows:
        pid = p["id"]
        on = active(p, now) if child else None
        sets, send = {}, None
        if on and p["bed_dnd"] and not p["bed_dnd_set"] and not phones.is_ios(p):
            send = ({"message": "command_dnd", "data": {"command": DND_MODE}}, " on", 1)
        elif (not on or not p["bed_dnd"]) and p["bed_dnd_set"]:
            send = ({"message": "command_dnd", "data": {"command": "off"}}, " off", 0)
        if send is not None and settings.get("actions_allowed"):
            res = actions.send(p, [send[0]], source)
            sets["bed_dnd_set"] = send[2]
            out["dnd"] += 1
            with db.get_conn() as conn:
                actions.log(conn, pid, "bedtime_dnd", send[1], None, "Household Phones", "bedtime", res.ok,
                            "sent" if res.ok else (res.reason or "failed"), now)
        if on and not monitor.watching_paused(p, now):
            _, st = phones.state_any(pid, INTERACTIVE_KEYS)
            screen_on = bool(st) and str(st.get("state")).lower() in ("on", "true")
            if screen_on:
                since = p["bed_screen_since"] or now
                changed = config.parse_iso(st.get("last_changed")) if st else None
                if changed and since < changed <= now:
                    since = changed                   # it went off and on again between two rounds
                sets["bed_screen_since"] = since
                night = night_of(now)
                if now - since >= after and p["bed_alerted_on"] != night and not monitor.alerts_paused(p, now):
                    sets["bed_alerted_on"] = night
                    r = _quiet_alert(p, name, int((now - since) // 60), since, now)
                    out["alerts"] += 1 if r.get("skipped") is None else 0
            elif p["bed_screen_since"] is not None:
                sets["bed_screen_since"] = None
        elif p["bed_screen_since"] is not None:
            sets["bed_screen_since"] = None
        if sets:
            with db.get_conn() as conn:
                conn.execute("UPDATE phones SET " + ", ".join(f"{k} = ?" for k in sets) + " WHERE id = ?",
                             (*sets.values(), pid))
    return out
