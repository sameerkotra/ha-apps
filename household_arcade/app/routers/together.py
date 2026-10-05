"""Playing together: invites, matches, the live link and head to head (SPEC §13).

    GET  /api/players?game=            who can be invited to a game now (and why not, for the rest)
    POST /api/matches                  invite someone to a race
    GET  /api/matches                  mine: waiting for me, sent, playing, recent
    GET  /api/matches/{id}?since=&wait=   one match; with `since` (its `v`) it waits for a change (long poll)
    POST /api/matches/{id}/state       my score / level / still playing (the long-poll fallback's way in)
    POST /api/matches/{id}/accept | decline | cancel
    GET  /api/matches/{id}/live        WebSocket: the same picture pushed, my state sent
    GET  /api/against                  head to head: wins, losses, draws per person and game

The match's results come from each person's own score (POST /api/scores on a session started with `matchId`).
"""
import asyncio
import json
import logging

from fastapi import APIRouter, BackgroundTasks, Body, Depends, HTTPException, Query, WebSocket
from starlette.concurrency import run_in_threadpool
from starlette.websockets import WebSocketDisconnect

from .. import auth, db, games, settings, together
from ..common import auth_core, web_security
from ..auth import get_current_user

logger = logging.getLogger("together")
router = APIRouter(prefix="/api", tags=["together"])

MAX_WAIT = 25.0          # longest a long poll holds
POLL_STEP = 0.2          # how often a waiting request looks for a change
WS_IDLE = 30.0           # a phone that sends nothing for this long (it sends every 2 s) is gone
WS_PUSH_AT_LEAST = 3.0   # push a fresh picture at least this often


def _require_enabled(current: dict) -> None:
    if current["disabled"]:
        raise HTTPException(403, "An admin has switched you off in Household Arcade, so you can't play just now.")


def _boom(e: together.TogetherError):
    return HTTPException(e.status, str(e))


@router.get("/players")
def players(game: str = Query(...), current: dict = Depends(get_current_user)):
    if not games.exists(game) or not settings.game_enabled(game):
        raise HTTPException(404, "Unknown game.")
    if not together.race_ok(game):
        raise HTTPException(409, f"{games.name(game)} can't be played together yet.")
    with db.get_conn() as conn:
        return {"game": game, "players": together.players_for(conn, current, game)}


@router.post("/matches", status_code=201)
def create_match(background: BackgroundTasks, body: dict = Body(...), current: dict = Depends(get_current_user)):
    _require_enabled(current)
    try:
        with db.get_conn() as conn:
            m = together.create(conn, current, body)
            out = together.view(conn, m, current["id"])
    except together.TogetherError as e:
        raise _boom(e)
    background.add_task(together.invite_blocking, m["id"])
    return out


@router.get("/matches")
def my_matches(current: dict = Depends(get_current_user)):
    with db.get_conn() as conn:
        return together.mine(conn, current["id"])


@router.get("/against")
def against(current: dict = Depends(get_current_user)):
    with db.get_conn() as conn:
        return {"people": together.against(conn, current["id"])}


@router.get("/matches/{match_id}")
async def one_match(match_id: str, since: int | None = Query(default=None), wait: float = Query(default=0),
                    current: dict = Depends(get_current_user)):
    """The match as you see it. With `since` (the `v` you last had) the request waits up to `wait` seconds for
    something to change, so a phone that can't open the WebSocket still hears the other player within a moment."""
    uid = current["id"]
    snap = await run_in_threadpool(_seen_snapshot, match_id, uid)
    if snap is None:
        raise HTTPException(404, "That match isn't known.")
    if since is None or wait <= 0:
        return snap
    loop = asyncio.get_running_loop()
    end = loop.time() + min(MAX_WAIT, max(0.0, wait))
    while snap["v"] == since and loop.time() < end:
        await asyncio.sleep(POLL_STEP)
        if together.signature(match_id) != since:
            snap = await run_in_threadpool(together.snapshot, match_id, uid)
            if snap is None:
                raise HTTPException(404, "That match isn't known.")
        elif snap["status"] in together.TERMINAL:
            break
    return snap


def _seen_snapshot(match_id: str, uid: str):
    together.set_state(match_id, uid, {})          # asking is a sign of life
    return together.snapshot(match_id, uid)


@router.post("/matches/{match_id}/state")
def post_state(match_id: str, body: dict = Body(default={}), current: dict = Depends(get_current_user)):
    with db.get_conn() as conn:
        if not together.can_use(conn, current["id"], match_id):
            raise HTTPException(404, "That match isn't known.")
    together.set_state(match_id, current["id"], body)
    return together.snapshot(match_id, current["id"])


def _act(fn, match_id: str, current: dict) -> dict:
    try:
        with db.get_conn() as conn:
            m = fn(conn, match_id, current)
            return together.view(conn, m, current["id"])
    except together.TogetherError as e:
        raise _boom(e)


@router.post("/matches/{match_id}/accept")
def accept(match_id: str, current: dict = Depends(get_current_user)):
    _require_enabled(current)
    return _act(together.accept, match_id, current)


@router.post("/matches/{match_id}/decline")
def decline(match_id: str, current: dict = Depends(get_current_user)):
    return _act(together.decline, match_id, current)


@router.post("/matches/{match_id}/cancel")
def cancel(match_id: str, current: dict = Depends(get_current_user)):
    return _act(together.cancel, match_id, current)


# ---------------------------------------------------------------------------
# The live link: a WebSocket per player
# ---------------------------------------------------------------------------

async def _close(ws: WebSocket, code: int = 1008) -> None:
    try:
        await ws.close(code=code)
    except Exception:
        pass


@router.websocket("/matches/{match_id}/live")
async def live(ws: WebSocket, match_id: str):
    """Messages from the phone: {"t": "state", score, level, over, paused} (and {"t": "ping"}); to the phone:
    {"t": "match", ...the match as it sees it}. The middleware that keeps outsiders away doesn't see WebSockets,
    so the source address and the origin are checked here."""
    if not auth_core.from_ingress(ws, auth.INGRESS_ALLOWED_HOSTS):
        await _close(ws)
        return
    if web_security.cross_origin_websocket(ws):
        await _close(ws)
        return
    try:
        ident = auth_core.identity(ws)
        user = await run_in_threadpool(auth.load_user, ident.user_id, ident.username, ident.display_name)
    except HTTPException:
        await _close(ws)
        return
    uid = user["id"]
    with_snapshot = await run_in_threadpool(_seen_snapshot, match_id, uid)
    if with_snapshot is None:
        await _close(ws)
        return
    await ws.accept()
    state = {"last": None, "pushed": 0.0}

    async def push(snap):
        await ws.send_text(json.dumps({"t": "match", **snap}))
        state["last"] = snap["v"]
        state["pushed"] = asyncio.get_running_loop().time()

    async def pusher():
        await push(with_snapshot)
        loop = asyncio.get_running_loop()
        while True:
            await asyncio.sleep(POLL_STEP)
            if together.signature(match_id) != state["last"] or loop.time() - state["pushed"] > WS_PUSH_AT_LEAST:
                snap = await run_in_threadpool(together.snapshot, match_id, uid)
                if snap is None:
                    return
                await push(snap)

    async def reader():
        while True:
            try:
                text = await asyncio.wait_for(ws.receive_text(), WS_IDLE)
            except asyncio.TimeoutError:
                return
            if len(text) > 1024:
                return
            try:
                msg = json.loads(text)
            except ValueError:
                continue
            if isinstance(msg, dict) and msg.get("t") in ("state", "ping"):
                await run_in_threadpool(together.set_state, match_id, uid, msg if msg["t"] == "state" else {})

    tasks = [asyncio.create_task(pusher()), asyncio.create_task(reader())]
    try:
        await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
    except Exception:
        pass
    finally:
        for t in tasks:
            t.cancel()
        for t in tasks:
            try:
                await t
            except (asyncio.CancelledError, WebSocketDisconnect, RuntimeError, Exception):
                pass
        await _close(ws, 1000)
