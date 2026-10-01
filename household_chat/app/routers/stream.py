"""GET /api/stream — Server-Sent Events (SPEC §8)."""
import asyncio

from fastapi import APIRouter, Depends, Header, Request
from fastapi.responses import StreamingResponse
from starlette.concurrency import run_in_threadpool

from .. import chats, db
from ..auth import require_user
from ..live import format_event, hub

router = APIRouter(prefix="/api", tags=["stream"])
PING_SECONDS = 20
REPLAY_MAX = 500


def replay(user_id: str, last_id: int) -> list:
    """Messages newer than the browser's last one, in chats it may see — or a `reload` if too many."""
    with db.get_conn() as conn:
        n = conn.execute(
            "SELECT COUNT(*) FROM messages m JOIN members mb ON mb.conversation_id = m.conversation_id AND mb.user_id = ? "
            "WHERE m.id > ? AND m.id > mb.joined_message_id", (user_id, last_id)).fetchone()[0]
        if n > REPLAY_MAX:
            return [format_event("reload", {})]
        rows = conn.execute(
            "SELECT m.*, mb.joined_message_id AS vf FROM messages m JOIN members mb ON mb.conversation_id = m.conversation_id "
            "AND mb.user_id = ? WHERE m.id > ? AND m.id > mb.joined_message_id ORDER BY m.id", (user_id, last_id)).fetchall()
        return [format_event("message", chats.messages_out(conn, [r], None, r["vf"])[0], r["id"]) for r in rows]


@router.get("/stream")
async def stream(request: Request, last_event_id: str | None = Header(default=None), user: dict = Depends(require_user)):
    conn = hub.register(user["id"])
    backlog = []
    if last_event_id and last_event_id.isdigit():
        backlog = await run_in_threadpool(replay, user["id"], int(last_event_id))

    async def gen():
        try:
            yield "retry: 3000\n\n"
            yield format_event("hello", {"userId": user["id"]})
            for item in backlog:
                yield item
            while True:
                try:
                    item = await asyncio.wait_for(conn.queue.get(), PING_SECONDS)
                except asyncio.TimeoutError:
                    if await request.is_disconnected():
                        break
                    hub.heartbeat(user["id"])
                    yield ": ping\n\n"
                    continue
                if item is None:
                    yield format_event("closed", {})
                    break
                yield item
        finally:
            hub.unregister(conn)

    return StreamingResponse(gen(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache, no-transform", "X-Accel-Buffering": "no"})
