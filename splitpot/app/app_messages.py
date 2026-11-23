"""Splitpot on the household apps bus (APP_MESSAGES_SPEC.md; the shared app/common/app_bus.py).

Splitpot answers the Household Assistant (tools.py, APP_MESSAGES_SPEC §6.6) and Household Docs'
`splitpot.balance.get` (§6.8 there): one person's own balance, overall and per group, for =SPLITPOT.BALANCE() in a
sheet. The bus opens its own WebSocket to Home Assistant and runs its outbox on its own thread; without a Supervisor
token (the tests) it stays off.
"""
from . import config, db, tools
from .common import app_bus as bus
from .common import assist_tools

SLUG = "splitpot"
DOCS = "household_docs"
MAX_GROUPS = 100                     # in one answer (the whole event stays well under 8 KB)

tools.tools.install(bus)


@bus.handler("splitpot.balance.get")
def on_balance(msg, conn):
    """{requested_by: a Home Assistant user id} from Household Docs → that person's own balance: {currency, overall,
    groups: [{name, amount}]} (+ they're owed, − they owe; amounts as on the group pages). Only ever the asking
    person's: matched by their Home Assistant login (users.ha_user_id), never by name. Nack not_allowed: sender
    (not Docs), off (Admin → App settings), no_access (not a Splitpot person, or turned off)."""
    from . import main                            # at call time: main imports this module
    if msg.from_app != DOCS:
        raise bus.Nack("not_allowed", "sender")
    uid = msg.data.get("requested_by") if isinstance(msg.data, dict) else None
    if not isinstance(uid, str) or not 0 < len(uid.strip()) <= 128:
        raise bus.Nack("invalid", "requested_by")
    if not main.get_setting("docs_answers"):
        raise bus.Nack("not_allowed", "off")
    me = conn.execute("SELECT id, disabled FROM users WHERE ha_user_id = ?", (uid.strip().lower(),)).fetchone()
    if me is None or me["disabled"]:
        raise bus.Nack("not_allowed", "no_access")
    groups = conn.execute("SELECT g.id, g.name FROM groups g JOIN group_members m ON m.group_id = g.id "
                          "WHERE m.user_id = ? ORDER BY g.created_at, g.id", (me["id"],)).fetchall()
    out, cents = [], 0
    for g in groups:
        mine = main.group_net(conn, g["id"], main.group_member_ids(conn, g["id"])).get(me["id"], 0.0)
        cents += round(mine * 100)
        out.append({"name": g["name"], "amount": round(mine, 2)})
    return {"currency": main.get_setting("currency"), "overall": round(cents / 100, 2), "groups": out[:MAX_GROUPS],
            "more": len(out) > MAX_GROUPS}


def start() -> None:
    """In the lifespan: learn the sidebar page (for links), then join the bus."""
    if bus.default.started:
        return
    config.SIDEBAR_PAGE = assist_tools.sidebar_page(SLUG, token=config.SUPERVISOR_TOKEN,
                                                    supervisor_api=config.SUPERVISOR_API)
    bus.start(SLUG, config.APP_TITLE, config.APP_VERSION, db=db.get_conn, outbox_thread=bool(config.SUPERVISOR_TOKEN),
              api_base=config.SUPERVISOR_CORE_API, ws_url=config.SUPERVISOR_CORE_WS, token=config.SUPERVISOR_TOKEN,
              signing_key=_signing_key)


def _signing_key() -> str:
    """Admin → App settings → App-messages signing key (empty = messages aren't signed), read live."""
    from . import main          # at call time: main imports this module
    return main.get_setting("bus_signing_key") or ""


def stop() -> None:
    bus.stop()
