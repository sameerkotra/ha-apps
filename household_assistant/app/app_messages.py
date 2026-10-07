"""The assistant on the household apps bus (APP_MESSAGES_SPEC.md §6.6; the shared app/common/app_bus.py).

It sends `assist.tools.list` (catalogue.py) and `assist.tool.call` (engine.py) and reads their answers through
`bus.on_reply`. It answers one kind of its own, `assist.ask`, from the companion Home Assistant integration
(`custom_components/household_assistant`) that makes the assistant an Assist conversation agent:

    assist.ask    integration → assistant   {requested_by: <HA user id>, name?: <their name>, text}
                  ack {} at once (the question is started after the bus commits), nack not_allowed "off" while
                  App settings → Answer Assist is off, nack invalid <field>
    assist.answer assistant → integration   a reply to the ask (reply_to its id, same ref), when the question ends:
                  {question, state: done|failed|stopped, answer?, error?, sources: [app names]}

The bus opens its own WebSocket to Home Assistant and runs its outbox on its own thread; without a Supervisor token
(the tests) it stays off.
"""
import logging
from datetime import timedelta

from . import auth, catalogue, config, db, engine, settings  # noqa: F401  (catalogue: its reply callbacks)
from .common import app_bus as bus

logger = logging.getLogger("app_messages")

ASK_KIND = "assist.ask"
ANSWER_KIND = "assist.answer"
MAX_SPOKEN = 3000                      # the answer as sent back (an event is at most 8 KB)
ANSWER_EXPIRES = timedelta(minutes=5)  # Assist has stopped listening long before


@bus.handler(ASK_KIND)
def on_ask(msg, conn):
    d = msg.data
    if not settings.get("assist_answers"):
        raise bus.Nack("not_allowed", "off")
    uid, text, name = d.get("requested_by"), d.get("text"), d.get("name")
    if not isinstance(uid, str) or not bus.ID_RE.match(uid):
        raise bus.Nack("invalid", "requested_by")
    if not isinstance(text, str) or not text.strip() or len(text) > engine.MAX_QUESTION:
        raise bus.Nack("invalid", "text")
    if name is not None and not isinstance(name, str):
        raise bus.Nack("invalid", "name")
    sender, ask_id, ref = msg.from_app, msg.id, msg.ref
    # engine.ask writes to this database, which the bus holds until its commit: start the question after it
    msg.after_commit(lambda: _start(sender, ask_id, ref, uid, name, text))
    return {}


def _start(sender: str, ask_id: str, ref, uid: str, name, text: str) -> None:
    try:
        user = auth.user_by_id(uid, name)
        qid = engine.ask(user, text)
    except (PermissionError, ValueError, engine.LimitError) as e:
        _answer(sender, ask_id, ref, {"question": None, "state": "failed", "error": str(e), "sources": []})
        return
    except Exception:
        logger.exception("Starting a question from Assist failed")
        _answer(sender, ask_id, ref, {"question": None, "state": "failed",
                                      "error": "Something went wrong while answering. Try again.", "sources": []})
        return
    engine.when_done(qid, lambda q: _answer(sender, ask_id, ref, _answer_data(q)))


def _answer_data(q: dict) -> dict:
    sources = []
    for s in q.get("sources") or []:
        if s.get("appName") and s["appName"] not in sources:
            sources.append(s["appName"])
    data = {"question": q["id"], "state": q["state"], "sources": sources}
    if q["state"] == "done":
        data["answer"] = (q.get("answer") or "")[:MAX_SPOKEN]
    elif q["state"] == "failed":
        data["error"] = (q.get("error") or "")[:500]
    return data


def _answer(sender: str, ask_id: str, ref, data: dict) -> None:
    try:
        bus.send(sender, ANSWER_KIND, data, ref=ref, reply_to=ask_id, expires_in=ANSWER_EXPIRES)
    except (bus.BusError, ValueError) as e:
        logger.info("Couldn't send the answer to %s: %s", sender, e)


def start() -> None:
    if bus.default.started:
        return
    bus.start(config.SLUG, config.APP_TITLE, config.APP_VERSION, can=[ASK_KIND], db=db.get_conn,
              outbox_thread=bool(config.SUPERVISOR_TOKEN), api_base=config.SUPERVISOR_CORE_API,
              ws_url=config.SUPERVISOR_CORE_WS, token=config.SUPERVISOR_TOKEN)


def stop() -> None:
    bus.stop()


def connected_apps() -> dict:
    """Admin → Connected apps (APP_MESSAGES_SPEC §5)."""
    on = bool(bus.default.started and bus.default.token)
    return {"on": on, "connected": bus.default.connected, "apps": bus.apps() if bus.default.started else []}
