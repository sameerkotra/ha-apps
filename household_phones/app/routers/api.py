"""The page's API for everyone (spec/SPEC.md §12): who you are, My phone, your phone's settings (pauses, quiet times,
forwarding, Replaces), Check now and its live result."""
import json
import re

from fastapi import APIRouter, Body, Depends, HTTPException, Request
from starlette.concurrency import run_in_threadpool

from .. import app_messages, auth, checknow, config, db, monitor, phones, settings, sidebar, views
from ..auth import get_current_user
from ..common import auth_core
from ..common import whoami as whoami_core

router = APIRouter(prefix="/api")
HHMM = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")


def _starting(now: float) -> bool:
    s = monitor.snapshot()
    return now - monitor.started_at() < s["interval_min"] * 60 + s["startup_grace_min"] * 60


@router.get("/me")
def me(user: dict = Depends(get_current_user)):
    now = config.clock()
    with db.get_conn() as conn:
        lost = db.state_get(conn, "lost_contact")
    return {"user": {"id": user["id"], "name": user["name"], "isAdmin": user["is_admin"], "isChild": user["is_child"]},
            "noAdmin": auth.no_admins(), "version": config.APP_VERSION, "page": sidebar.PAGE,
            "starting": _starting(now), "lostContact": lost}


@router.get("/whoami")
def whoami(request: Request, user: dict = Depends(get_current_user)):
    """"How the app sees you": what Home Assistant sent and whether it matched admin_users (never the list)."""
    return whoami_core.build(
        request, user_id=user["id"], username=user["username"], display_name=user["name"],
        is_admin=user["is_admin"], admin_entries=len(config.ADMIN_USERS),
        display_name_only=(not user["is_admin"]) and auth_core.display_name_listed(user["name"], config.ADMIN_USERS))


@router.get("/my")
def my(user: dict = Depends(get_current_user)):
    """My phone: your phones, and the phones that forward into your chats (Check <owner>'s phone)."""
    now = config.clock()
    with db.get_conn() as conn:
        mine = [views.summary(conn, p, now, viewer=user) for p in phones.mine(conn, user["id"])]
        others = [{"id": p["id"], "name": phones.display_name(conn, p),
                   "owner": phones.first_name(phones.owner_name(conn, p["owner_id"])),
                   "chat": p["fwd_chat_name"]} for p in checknow.checkable(conn, user)]
        row = conn.execute("SELECT quiet_start, quiet_end FROM users WHERE id = ?", (user["id"],)).fetchone()
    return {"phones": mine, "others": others, "starting": _starting(now),
            "quietStart": row["quiet_start"] if row else "", "quietEnd": row["quiet_end"] if row else "",
            "adultsMayPause": bool(settings.get("adults_may_pause")) and not user["is_child"],
            "pauseMaxHours": settings.get("pause_max_hours"), "forwardingAllowed": bool(settings.get("forwarding_allowed"))}


class _Quiet:
    @staticmethod
    def clean(body: dict, a: str, b: str) -> tuple[str, str]:
        qs, qe = (body.get(a) or "").strip(), (body.get(b) or "").strip()
        for v in (qs, qe):
            if v and not HHMM.match(v):
                raise HTTPException(422, "Use times like 23:00 (24-hour).")
        if bool(qs) != bool(qe):
            raise HTTPException(422, "Give both times, or neither.")
        return qs, qe


@router.put("/me/quiet")
def my_quiet(body: dict = Body(...), user: dict = Depends(get_current_user)):
    """Your own quiet hours for alerts you receive ("" = the household's)."""
    qs, qe = _Quiet.clean(body if isinstance(body, dict) else {}, "quietStart", "quietEnd")
    with db.get_conn() as conn:
        conn.execute("UPDATE users SET quiet_start = ?, quiet_end = ? WHERE id = ?", (qs, qe, user["id"]))
    return {"quietStart": qs, "quietEnd": qe}


def _phone(conn, pid: str, user: dict, *, owner: bool = False, admin_ok: bool = True):
    p = phones.get(conn, pid)
    if p is None:
        raise HTTPException(404, "No such phone.")
    is_owner = p["owner_id"] is not None and p["owner_id"] == user["id"]
    if owner and not is_owner:
        raise HTTPException(403, "Only the phone's owner can do this.")
    if not (is_owner or (admin_ok and user["is_admin"])):
        raise HTTPException(404, "No such phone.")
    return p


@router.get("/phones/{pid}")
def phone(pid: str, user: dict = Depends(get_current_user)):
    """Your own phone's page, or (admins) any phone's page."""
    now = config.clock()
    with db.get_conn() as conn:
        p = _phone(conn, pid, user)
        if p["owner_id"] == user["id"]:
            out = views.owner_page(conn, p, now, user)
            out["canPauseWatching"] = bool(settings.get("adults_may_pause")) and not user["is_child"]
            out["pauseMaxHours"] = settings.get("pause_max_hours")
            if user["is_admin"]:
                out["admin"] = views.admin_page(conn, p, now, user)
            return out
        return {"admin": views.admin_page(conn, p, now, user), **views.summary(conn, p, now, viewer=user)}


@router.put("/phones/{pid}/mine")
def phone_mine(pid: str, body: dict = Body(...), user: dict = Depends(get_current_user)):
    """The owner's own choices: daily quiet times, Share my last place."""
    body = body if isinstance(body, dict) else {}
    if set(body) - {"quietStart", "quietEnd", "sharePlace"}:
        raise HTTPException(422, "Send quietStart, quietEnd and sharePlace.")
    with db.get_conn() as conn:
        p = _phone(conn, pid, user, owner=True)
        qs, qe = (p["quiet_start"], p["quiet_end"])
        if "quietStart" in body or "quietEnd" in body:
            qs, qe = _Quiet.clean(body, "quietStart", "quietEnd")
        share = p["share_place"] if "sharePlace" not in body else int(body["sharePlace"] is True)
        conn.execute("UPDATE phones SET quiet_start = ?, quiet_end = ?, share_place = ? WHERE id = ?", (qs, qe, share, pid))
        if share != p["share_place"]:
            phones.history(conn, pid, "owner", "Share my last place " + ("on." if share else "off."), by=user["id"])
    return phone(pid, user)


@router.post("/phones/{pid}/pause")
def pause(pid: str, body: dict = Body(...), user: dict = Depends(get_current_user)):
    """{kind: alerts | watching, hours?: 1–168, until?: epoch seconds, untilOnline?: bool, reason?: text}."""
    body = body if isinstance(body, dict) else {}
    kind = body.get("kind")
    now = config.clock()
    with db.get_conn() as conn:
        p = _phone(conn, pid, user, owner=(kind == "watching"))
        if kind not in ("alerts", "watching"):
            raise HTTPException(422, "kind is alerts or watching.")
        until, until_online = None, False
        hours = body.get("hours")
        if body.get("untilOnline") is True:
            if kind == "watching":
                raise HTTPException(422, "Pause watching for some hours, or until a time.")
            until_online = True
        elif isinstance(hours, (int, float)) and not isinstance(hours, bool) and 0 < hours <= 168:
            until = now + hours * 3600
        elif isinstance(body.get("until"), (int, float)) and not isinstance(body.get("until"), bool) and body["until"] > now:
            until = float(body["until"])
        else:
            raise HTTPException(422, "Choose for how long.")
        if kind == "watching":
            if user["is_child"]:
                raise HTTPException(403, "Children's phones can't be paused this way — ask an admin.")
            if not settings.get("adults_may_pause"):
                raise HTTPException(403, "Pausing being watched is turned off in this household.")
            if until - now > settings.get("pause_max_hours") * 3600 + 60:
                raise HTTPException(422, f"At most {settings.get('pause_max_hours')} hours.")
        elif until is not None and until - now > 7 * 24 * 3600:
            raise HTTPException(422, "At most a week.")
        reason = " ".join(str(body.get("reason") or "").split())[:80] or None
        if p["pause_kind"] == "watching" and kind == "alerts" and p["owner_id"] != user["id"]:
            raise HTTPException(409, "The owner has paused being watched.")
        conn.execute("UPDATE phones SET pause_kind = ?, pause_until = ?, pause_until_online = ?, pause_reason = ?, "
                     "pause_by = ? WHERE id = ?", (kind, until, int(until_online), reason, user["id"], pid))
        p2 = phones.get(conn, pid)
        what = "Alerts paused" if kind == "alerts" else "Watching paused"
        phones.history(conn, pid, "pause", f"{what} by {user['name']} {monitor.until_text(p2)}".strip()
                       + (f" ({reason})" if reason else "") + ".", by=user["id"], now=now)
    monitor.WAKE.set()
    return phone(pid, user)


@router.delete("/phones/{pid}/pause")
def unpause(pid: str, user: dict = Depends(get_current_user)):
    with db.get_conn() as conn:
        p = _phone(conn, pid, user)
        if p["pause_kind"] == "watching" and p["owner_id"] != user["id"]:
            raise HTTPException(403, "Only the owner can end their pause.")
        conn.execute("UPDATE phones SET pause_kind = NULL, pause_until = NULL, pause_until_online = 0, "
                     "pause_reason = NULL, pause_by = NULL WHERE id = ?", (pid,))
        if p["pause_kind"]:
            phones.history(conn, pid, "pause", f"Pause ended by {user['name']}.", by=user["id"])
    monitor.WAKE.set()
    return phone(pid, user)


DISAPPEAR = ("1h", "1d", "7d", "30d")


@router.put("/phones/{pid}/forwarding")
def forwarding_settings(pid: str, body: dict = Body(...), user: dict = Depends(get_current_user)):
    """The owner's Forward my messages (§6). Only the owner: an admin can't switch it on for someone else."""
    body = body if isinstance(body, dict) else {}
    allowed_keys = {"on", "target", "chatId", "chatName", "chatKind", "disappear", "apps", "filter", "words",
                    "allConfirmed", "quietStart", "quietEnd", "quietMode"}
    if set(body) - allowed_keys:
        raise HTTPException(422, f"Unknown field(s): {', '.join(sorted(set(body) - allowed_keys))}.")
    ask_members = False
    with db.get_conn() as conn:
        p = _phone(conn, pid, user, owner=True, admin_ok=False)
        if phones.is_ios(p):
            raise HTTPException(409, "iPhones can't forward messages (the Companion app has no Last notification sensor there).")
        v = {k: p[k] for k in ("fwd_on", "fwd_target", "fwd_chat_id", "fwd_chat_name", "fwd_chat_kind", "fwd_disappear",
                               "fwd_apps", "fwd_filter", "fwd_words", "fwd_all_confirmed", "fwd_quiet_start",
                               "fwd_quiet_end", "fwd_quiet_mode")}
        if "on" in body:
            if not isinstance(body["on"], bool):
                raise HTTPException(422, "on is true or false.")
            if body["on"] and not settings.get("forwarding_allowed"):
                raise HTTPException(403, "Forwarding is turned off in this household (an admin's App setting).")
            v["fwd_on"] = int(body["on"])
        if "target" in body:
            t = body["target"]
            if t == "my_room":
                v.update(fwd_target="my_room", fwd_chat_id=None, fwd_chat_name=None, fwd_chat_kind=None)
            elif t == "chat":
                cid = body.get("chatId")
                if not isinstance(cid, str) or not app_messages.ID_RE.match(cid):
                    raise HTTPException(422, "Choose a chat.")
                kind = body.get("chatKind") if body.get("chatKind") in ("group", "direct", "personal") else "group"
                name = " ".join(str(body.get("chatName") or "").split())[:120] or "a chat"
                rule = settings.get("forward_chats")
                if kind == "personal":
                    v.update(fwd_target="my_room", fwd_chat_id=None, fwd_chat_name=None, fwd_chat_kind=None)
                else:
                    if rule == "own_room":
                        raise HTTPException(403, "In this household, forwards go only into your own 📌 My room.")
                    if rule == "listed" and name.lower() not in [g.lower() for g in settings.get("forward_groups")]:
                        raise HTTPException(403, "An admin allows only some groups for forwards; that one isn't listed.")
                    if cid != p["fwd_chat_id"]:
                        v.update(fwd_target="chat", fwd_chat_id=cid, fwd_chat_name=name, fwd_chat_kind=kind)
                        conn.execute("UPDATE phones SET fwd_members = NULL, fwd_members_at = NULL WHERE id = ?", (pid,))
                        ask_members = True
                        if "filter" not in body and settings.get("forward_group_filter") == "codes":
                            v["fwd_filter"] = "codes"
            else:
                raise HTTPException(422, "target is my_room or chat.")
        if "disappear" in body:
            if body["disappear"] not in DISAPPEAR:
                raise HTTPException(422, "disappear is 1h, 1d, 7d or 30d.")
            v["fwd_disappear"] = body["disappear"]
        if "apps" in body:
            apps = body["apps"]
            if not isinstance(apps, list) or len(apps) > 30 or not all(isinstance(a, str) and re.fullmatch(r"[A-Za-z0-9_.]{2,120}", a) for a in apps):
                raise HTTPException(422, "Apps are package names like com.google.android.apps.messaging.")
            v["fwd_apps"] = json.dumps(list(dict.fromkeys(apps)))
        if "words" in body:
            words = body["words"]
            if not isinstance(words, list) or len(words) > 20 or not all(isinstance(w, str) and 0 < len(w.strip()) <= 40 for w in words):
                raise HTTPException(422, "Up to 20 words, each up to 40 letters.")
            v["fwd_words"] = json.dumps([w.strip() for w in words])
        if "filter" in body:
            if body["filter"] not in ("codes", "words", "all"):
                raise HTTPException(422, "filter is codes, words or all.")
            v["fwd_filter"] = body["filter"]
        if v["fwd_filter"] == "all":
            if body.get("filter") == "all" and body.get("allConfirmed") is not True and not p["fwd_all_confirmed"]:
                raise HTTPException(422, "Confirm that all your texts will be forwarded.")
            v["fwd_all_confirmed"] = 1 if body.get("allConfirmed") is True or p["fwd_all_confirmed"] else 0
        else:
            v["fwd_all_confirmed"] = 0
        if v["fwd_filter"] == "words" and not json.loads(v["fwd_words"] or "[]"):
            raise HTTPException(422, "Add at least one word, or choose another filter.")
        if "quietStart" in body or "quietEnd" in body:
            v["fwd_quiet_start"], v["fwd_quiet_end"] = _Quiet.clean(body, "quietStart", "quietEnd")
        if "quietMode" in body:
            if body["quietMode"] not in ("hold", "drop"):
                raise HTTPException(422, "quietMode is hold or drop.")
            v["fwd_quiet_mode"] = body["quietMode"]
        conn.execute("UPDATE phones SET " + ", ".join(f"{k} = ?" for k in v) + " WHERE id = ?", (*v.values(), pid))
        if v["fwd_on"] != p["fwd_on"]:
            phones.history(conn, pid, "forward_on", "Forwarding " + ("switched on" if v["fwd_on"] else "switched off")
                           + f" by {user['name']}.", by=user["id"])
            if not v["fwd_on"]:
                conn.execute("UPDATE phones SET fwd_error = NULL WHERE id = ?", (pid,))
    if ask_members:
        try:
            app_messages.ask_members(pid)
        except Exception:                                    # noqa: BLE001 — asked again hourly
            pass
    return phone(pid, user)


@router.post("/phones/{pid}/forwarding/chats")
def forwarding_chats(pid: str, user: dict = Depends(get_current_user)):
    """Ask Household Chat for the chats you may post in (the page polls the answer)."""
    with db.get_conn() as conn:
        _phone(conn, pid, user, owner=True, admin_ok=False)
    try:
        rid = app_messages.ask_chats(user["id"], pid)
    except ValueError as e:
        raise HTTPException(409, str(e))
    return {"request": rid}


@router.get("/requests/{rid}")
def bus_request(rid: str, user: dict = Depends(get_current_user)):
    with db.get_conn() as conn:
        r = conn.execute("SELECT * FROM bus_requests WHERE id = ? AND user_id = ?", (rid[:32], user["id"])).fetchone()
    if r is None:
        raise HTTPException(404, "No such request.")
    expired = r["state"] == "pending" and config.clock() - r["created"] > app_messages.LIST_EXPIRES.total_seconds()
    return {"state": "failed" if expired else r["state"], "result": json.loads(r["result"]) if r["result"] else None,
            "error": "Household Chat didn't answer — is it running?" if expired else r["error"]}


@router.post("/phones/{pid}/stop-request")
def stop_request(pid: str, user: dict = Depends(get_current_user)):
    """"Stop watching my phone": a request to the admins (§9)."""
    from .. import alerts
    with db.get_conn() as conn:
        p = _phone(conn, pid, user, owner=True, admin_ok=False)
        conn.execute("UPDATE phones SET stop_requested_at = ? WHERE id = ?", (config.clock(), pid))
        phones.history(conn, pid, "owner", f"{user['name']} asked to stop watching this phone.", by=user["id"])
        name = phones.display_name(conn, p)
    alerts.send("request", None, f"{user['name']} asks to stop watching {name}. Admins: Phones → All phones → {name} → "
                              "Stop watching.", to="admins")
    return phone(pid, user)


@router.post("/phones/{pid}/replaces")
def replaces(pid: str, body: dict = Body(...), user: dict = Depends(get_current_user)):
    """This (new) phone replaces an older one: name, owner, settings, forwarding and history move over (§2)."""
    old_id = (body or {}).get("old") if isinstance(body, dict) else None
    with db.get_conn() as conn:
        new = _phone(conn, pid, user)
        old = phones.get(conn, old_id) if isinstance(old_id, str) else None
        if old is None or old["id"] == new["id"]:
            raise HTTPException(404, "Choose the phone it replaces.")
        if not user["is_admin"] and old["owner_id"] != user["id"]:
            raise HTTPException(403, "You can only replace your own phone.")
        replace(conn, old, new, user)
    monitor.WAKE.set()
    return phone(pid, user)


MOVED = ("name", "kind", "watched", "interval_min", "owner_id", "owner_by_admin", "quiet_start", "quiet_end",
         "share_place", "fwd_on", "fwd_target", "fwd_chat_id", "fwd_chat_name", "fwd_chat_kind", "fwd_members",
         "fwd_members_at", "fwd_disappear", "fwd_apps", "fwd_filter", "fwd_words", "fwd_all_confirmed",
         "fwd_quiet_start", "fwd_quiet_end", "fwd_quiet_mode")


def replace(conn, old, new, user: dict) -> None:
    oldname, newname = phones.display_name(conn, old), phones.display_name(conn, new)
    conn.execute("UPDATE phones SET " + ", ".join(f"{c} = ?" for c in MOVED) + ", kind_confirmed = 1 WHERE id = ?",
                 (*[old[c] for c in MOVED], new["id"]))
    for t in ("history", "checks", "forwards"):
        conn.execute(f"UPDATE {t} SET phone_id = ? WHERE phone_id = ?", (new["id"], old["id"]))
    conn.execute("DELETE FROM alert_state WHERE phone_id = ?", (old["id"],))
    conn.execute("UPDATE phones SET retired = 1, replaced_by = ?, device_id = NULL, fwd_on = 0, watched = 0 WHERE id = ?",
                 (new["id"], old["id"]))
    phones.history(conn, new["id"], "owner", f"Replaces {oldname} (marked by {user['name']}); was {newname}.",
                   by=user["id"])


# --------------------------------------------------------------------------------------------- Check now

@router.post("/phones/{pid}/check")
async def check(pid: str, request: Request):
    user = await run_in_threadpool(auth.get_current_user, request)
    try:
        cid = await run_in_threadpool(checknow.start, pid, user)
    except checknow.TooMany as e:
        raise HTTPException(429, str(e))
    return {"check": cid}


@router.get("/checks/{cid}")
def check_result(cid: str, user: dict = Depends(get_current_user)):
    return checknow.view(cid, user)


@router.get("/check/{pid}/latest")
def check_latest(pid: str, user: dict = Depends(get_current_user)):
    """The latest check of a phone (the #/check/<phone> page shows it before starting a new one)."""
    with db.get_conn() as conn:
        p = phones.get(conn, pid)
        if p is None or not checknow.may_check(p, user):
            raise HTTPException(404, "No such phone (or you can't check it).")
        r = conn.execute("SELECT id FROM checks WHERE phone_id = ? ORDER BY started DESC LIMIT 1", (pid,)).fetchone()
        name = phones.display_name(conn, p)
    return {"phone": {"id": pid, "name": name}, "check": checknow.view(r["id"], user) if r else None}
