"""What Chat answers the Household Assistant (HOUSEHOLD_ASSISTANT_SPEC.md §4.2; the shared app/common/assist_tools.py).

One tool, `chat.unread`: which of the person's chats have unread messages and how many, as the chat list shows
them. Never message text, previews or who wrote what — a chat's text is the most private thing on the bus, and
the person can search in Chat itself. Answered only while the admin's *Answer the Household Assistant* is on and
the person hasn't turned off *Let the Household Assistant answer for me* (Settings → You).
"""
import re

from . import chats, config, db, settings
from .common import app_bus as bus
from .common import assist_tools

_PANEL_RE = re.compile(r"^/[a-z0-9]{1,16}_household_chat$")


def _busy() -> None:
    if db.RESTORING.is_set():
        raise bus.Nack("busy", "restoring")


def _actor(conn, uid: str):
    u = chats.user_row(conn, uid)
    if u is None or u["disabled"]:
        return None
    return {"id": u["id"], "name": u["name"], "is_child": bool(u["is_child"]), "assistant_ok": bool(u["assistant_ok"])}


def _panel():
    """The sidebar page ("/<full slug>"), or None when Chat isn't in the sidebar or the slug isn't known."""
    url = config.INGRESS_URL
    return url if _PANEL_RE.match(url or "") else None


tools = assist_tools.Catalogue(
    "chat", targets=[r"/chat/[A-Za-z0-9_-]{1,64}"],
    actor=_actor, enabled=lambda conn: bool(settings.get("assistant_answers", conn)),
    person_enabled=lambda conn, user: user["assistant_ok"], panel=_panel, busy=_busy)


@tools.tool("chat.unread",
            "Which of the person's chats have unread messages, and how many (and how many mention them). Names and "
            "counts only: it never says what was written.",
            returns="the chats with unread messages: name, unread, mentions, muted",
            examples=("Do I have unread messages?",), children=True)
def unread(ctx):
    rows = [c for c in chats.list_conversations(ctx.conn, ctx.user["id"]) if c["unread"] > 0]
    rows.sort(key=lambda c: (-c["mentionUnread"], -c["unread"]))
    if not rows:
        return ctx.result("No unread messages in Household Chat.", links=[ctx.link("Open Household Chat")])
    total = sum(c["unread"] for c in rows)
    mentions = sum(c["mentionUnread"] for c in rows)
    text = (f"{total} unread message{'s' if total != 1 else ''} in {len(rows)} chat{'s' if len(rows) != 1 else ''}"
            + (f", {mentions} mentioning {ctx.user['name']}" if mentions else "") + ": "
            + ", ".join(f"{c['name']} ({c['unread']})" for c in rows[:10]) + ("…" if len(rows) > 10 else "") + ".")
    items = [{"chat": c["name"], "kind": c["kind"], "unread": c["unread"], "mentions": c["mentionUnread"],
              "muted": c["muted"]} for c in rows]
    links = [ctx.link(f"{c['name']} in Household Chat", f"/chat/{c['id']}") for c in rows[:assist_tools.MAX_LINKS]]
    return ctx.result(text, items=items, links=links)
