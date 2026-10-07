"""Playing together: invites, matches, the live link and head to head (SPEC §13).

    GET  /api/players?game=            who can be invited to a game now (and why not, for the rest)
    POST /api/matches                  invite someone to a race
    GET  /api/matches                  mine: waiting for me, sent, playing, recent
    GET  /api/matches/{id}?since=&wait=   one match; with `since` (its `v`) it waits for a change (long poll)
    POST /api/matches/{id}/state       my score / level / still playing (the long-poll fallback's way in)
    POST /api/matches/{id}/accept | decline | cancel
    POST /api/matches/{id}/resign      give up a live duel (the other wins), or a turn-by-turn match
    POST /api/matches/{id}/start       turn by turn, 3–4 players: start with those who have joined
    GET  /api/matches/{id}/turns       turn by turn: the board as this player may see it (moves, view, whose move)
    POST /api/matches/{id}/move        turn by turn: {move, n?} — checked by the server's rules (409 not your move,
                                       422 not legal); the next player is told it's their move
    POST /api/matches/{id}/roll        turn by turn with dice: the server's roll for the player to move
    GET  /api/matches/{id}/live        WebSocket: a race's picture pushed and my state sent; a live duel's lockstep
                                       messages both ways (app/live.py)
    POST /api/matches/{id}/live        a live duel without a WebSocket: {since, msgs, wait} → {msgs}
    GET  /api/against                  head to head: wins, losses, draws per person and game

The match's results come from each person's own score (POST /api/scores on a session started with `matchId`).
"""
import asyncio
import json
import logging

from fastapi import APIRouter, BackgroundTasks, Body, Depends, HTTPException, Query, WebSocket
from starlette.concurrency import run_in_threadpool
from starlette.websockets import WebSocketDisconnect

from .. import auth, db, games, live, settings, together, turns
from ..common import auth_core, web_security
from ..auth import get_current_user

logger = logging.getLogger("together")
router = APIRouter(prefix="/api", tags=["together"])

MAX_WAIT = 25.0          # longest a long poll holds
POLL_STEP = 0.2          # how often a waiting request looks for a change
WS_IDLE = 30.0           # a phone that sends nothing for this long (it sends every 2 s) is gone
WS_PUSH_AT_LEAST = 3.0   # push a fresh picture at least this often
LIVE_SEND_STEP = 0.05    # a live duel's connection is woken for each message, and looks for itself this often
LIVE_CHECK_EVERY = 0.5   # … and at the room's clock (a quiet phone, the count-in) this often
LIVE_WATCH_EVERY = 5.0   # … and at the players' play time (children) this often
LIVE_POLL_MAX = 15.0     # longest a live duel's HTTP fallback request holds
LIVE_POLL_MSGS = 64      # messages one fallback request may carry


def _require_enabled(current: dict) -> None:
    if current["disabled"]:
        raise HTTPException(403, "An admin has switched you off in Household Arcade, so you can't play just now.")


def _boom(e: together.TogetherError):
    return HTTPException(e.status, str(e))


@router.get("/players")
def players(game: str = Query(...), mode: str | None = Query(default=None), current: dict = Depends(get_current_user)):
    if not games.exists(game) or not settings.game_enabled(game):
        raise HTTPException(404, "Unknown game.")
    if not together.together_ok(game):
        raise HTTPException(409, f"{games.name(game)} can't be played together yet.")
    kind = together.kind_for(game, mode)
    out = {"game": game, "kind": kind, "maxPlayers": turns.players_range(game)[1] if kind == "turns" else 2}
    with db.get_conn() as conn:
        out["players"] = together.players_for(conn, current, game, kind)
    return out


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
def accept(match_id: str, background: BackgroundTasks, current: dict = Depends(get_current_user)):
    _require_enabled(current)
    out = _act(together.accept, match_id, current)
    if out["kind"] == "turns" and out["status"] == "playing":
        background.add_task(turns.notify_blocking, match_id)        # the first to move, if it isn't this person
    return out


@router.post("/matches/{match_id}/decline")
def decline(match_id: str, background: BackgroundTasks, current: dict = Depends(get_current_user)):
    out = _act(together.decline, match_id, current)
    if out["kind"] == "turns" and out["status"] == "playing":
        background.add_task(turns.notify_blocking, match_id)
    return out


@router.post("/matches/{match_id}/start")
def start_now(match_id: str, background: BackgroundTasks, current: dict = Depends(get_current_user)):
    _require_enabled(current)
    out = _act(together.start_now, match_id, current)
    background.add_task(turns.notify_blocking, match_id)
    return out


@router.post("/matches/{match_id}/cancel")
def cancel(match_id: str, current: dict = Depends(get_current_user)):
    return _act(together.cancel, match_id, current)


@router.post("/matches/{match_id}/resign")
def resign(match_id: str, background: BackgroundTasks, current: dict = Depends(get_current_user)):
    out = _act(together.resign, match_id, current)
    if out["kind"] == "turns" and out["status"] == "playing":         # 3–4 players: the game goes on without them
        background.add_task(turns.notify_blocking, match_id)
    return out


# ---------------------------------------------------------------------------
# Turn by turn (SPEC §13.5)
# ---------------------------------------------------------------------------

def _turns_boom(e):
    return HTTPException(e.status, str(e))


@router.get("/matches/{match_id}/turns")
def turn_picture(match_id: str, current: dict = Depends(get_current_user)):
    with db.get_conn() as conn:
        if not together.is_player(conn, match_id, current["id"]):
            raise HTTPException(404, "That match isn't known.")
        m = together.settle(conn, match_id)
        if m["kind"] != "turns":
            raise HTTPException(409, "That match isn't played turn by turn.")
        turns.seen(conn, match_id, current["id"])
        try:
            out = turns.picture(conn, m, current["id"])
        except turns.TurnsError as e:
            raise _turns_boom(e)
    together.mark_seen(match_id, current["id"])
    return out


@router.post("/matches/{match_id}/move")
def turn_move(match_id: str, background: BackgroundTasks, body: dict = Body(...), current: dict = Depends(get_current_user)):
    try:
        with db.get_conn() as conn:
            out, tell = turns.move(conn, match_id, current, body)
    except turns.TurnsError as e:
        raise _turns_boom(e)
    together.mark_seen(match_id, current["id"])
    if tell:
        background.add_task(turns.notify_blocking, match_id)
    return out


@router.post("/matches/{match_id}/roll")
def turn_roll(match_id: str, background: BackgroundTasks, current: dict = Depends(get_current_user)):
    try:
        with db.get_conn() as conn:
            out, tell = turns.roll(conn, match_id, current)
    except turns.TurnsError as e:
        raise _turns_boom(e)
    together.mark_seen(match_id, current["id"])
    if tell:
        background.add_task(turns.notify_blocking, match_id)
    return out


# ---------------------------------------------------------------------------
# The live link: a WebSocket per player
# ---------------------------------------------------------------------------

async def _close(ws: WebSocket, code: int = 1008) -> None:
    try:
        await ws.close(code=code)
    except Exception:
        pass


@router.websocket("/matches/{match_id}/live")
async def live_link(ws: WebSocket, match_id: str):
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
    if with_snapshot["kind"] == "live" and live.get(match_id) is not None:
        await _live_socket(ws, match_id, uid, with_snapshot)
        return
    # a race, or a live duel's invite that hasn't been answered yet: the picture, pushed
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


# ---------------------------------------------------------------------------
# Live duels (SPEC §13.4): the lockstep messages, over the WebSocket or the HTTP fallback
# ---------------------------------------------------------------------------

def _apply_end(match_id: str, ended) -> None:
    with db.get_conn() as conn:
        together.end_live(conn, match_id, *ended)


def _store_shots(match_id: str, shots: list) -> None:
    with db.get_conn() as conn:
        together.store_shots(conn, match_id, shots)


async def _write_end(room) -> None:
    """An end the room decided (a phone went away, out of step) goes into the database; so do the shots of a game
    whose players take turns live (Carrom), into match_moves."""
    shots = room.take_shots() if room.turns else []
    if shots:
        await run_in_threadpool(_store_shots, room.match_id, shots)
    ended = room.take_end()
    if ended:
        await run_in_threadpool(_apply_end, room.match_id, ended)


async def _live_socket(ws: WebSocket, match_id: str, uid: str, first_snapshot: dict) -> None:
    room = live.get(match_id)
    seat = room.seat_of(uid) if room else None
    if room is None or seat is None:
        await _close(ws)
        return
    await ws.accept()
    replies: asyncio.Queue = asyncio.Queue()
    cursor = {"since": None}            # None until the phone says hello (it says which numbered message it has)
    wake = asyncio.Event()              # set when there is something to send: a reply, or a message for this phone
    main_loop = asyncio.get_running_loop()
    room.wake_with(seat, lambda: main_loop.call_soon_threadsafe(wake.set))

    async def sender():
        loop = asyncio.get_running_loop()
        await ws.send_text(json.dumps({"t": "match", **first_snapshot}))
        last_v, pushed = first_snapshot["v"], loop.time()
        checked = loop.time()
        while True:
            while not replies.empty():
                await ws.send_text(json.dumps(replies.get_nowait()))
            if cursor["since"] is not None:
                for m in room.pull(seat, cursor["since"]):
                    await ws.send_text(json.dumps(m))
                    cursor["since"] = m["n"]
            now = loop.time()
            if now - checked >= LIVE_CHECK_EVERY:
                checked = now
                room.check()
                await _write_end(room)
            if room.watch_due(every=LIVE_WATCH_EVERY):
                await run_in_threadpool(together.live_watch, match_id)
            if together.signature(match_id) != last_v or now - pushed > WS_PUSH_AT_LEAST:
                snap = await run_in_threadpool(together.snapshot, match_id, uid)
                if snap is None:
                    return
                await ws.send_text(json.dumps({"t": "match", **snap}))
                last_v, pushed = snap["v"], now
            try:
                await asyncio.wait_for(wake.wait(), LIVE_SEND_STEP)
            except asyncio.TimeoutError:
                pass
            wake.clear()

    async def reader():
        while True:
            try:
                text = await asyncio.wait_for(ws.receive_text(), WS_IDLE)
            except asyncio.TimeoutError:
                return
            if len(text) > live.MAX_MSG_BYTES:
                return
            try:
                msg = json.loads(text)
            except ValueError:
                msg = None
            if isinstance(msg, dict) and msg.get("t") == "hello":
                since = msg.get("since")
                since = since if isinstance(since, int) and not isinstance(since, bool) and since >= 0 else 0
                out = room.hello(seat, since)
                cursor["since"] = since
            else:
                out = room.receive(seat, msg)
            together.mark_seen(match_id, uid)
            await _write_end(room)          # a shot (Carrom) is in the database before it is acknowledged
            for r in out:
                replies.put_nowait(r)
            if out:
                wake.set()
            if room.closing(seat):
                return

    tasks = [asyncio.create_task(sender()), asyncio.create_task(reader())]
    try:
        await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
    except Exception:
        pass
    finally:
        room.wake_with(seat, None)
        for t in tasks:
            t.cancel()
        for t in tasks:
            try:
                await t
            except (asyncio.CancelledError, WebSocketDisconnect, RuntimeError, Exception):
                pass
        await _close(ws, 1000)


@router.post("/matches/{match_id}/live")
async def live_poll(match_id: str, body: dict = Body(default={}), current: dict = Depends(get_current_user)):
    """A live duel's messages over plain HTTP, when the WebSocket can't be used: {since, msgs, wait, v?} → the
    replies to `msgs`, then the numbered messages after `since` (waiting up to `wait` seconds for one), and the
    match picture when its `v` isn't the one given."""
    uid = current["id"]
    room = live.get(match_id)
    seat = room.seat_of(uid) if room else None
    if room is None or seat is None:
        raise HTTPException(404, "That match isn't being played live.")
    if not isinstance(body, dict):
        raise HTTPException(422, "Send {since, msgs, wait}.")
    since, msgs, wait = body.get("since", 0), body.get("msgs", []), body.get("wait", 0)
    if not isinstance(since, int) or isinstance(since, bool) or since < 0:
        raise HTTPException(422, "since must be a whole number.")
    if not isinstance(msgs, list) or len(msgs) > LIVE_POLL_MSGS:
        raise HTTPException(422, f"msgs must be a list of at most {LIVE_POLL_MSGS}.")
    if isinstance(wait, bool) or not isinstance(wait, (int, float)) or wait != wait:
        raise HTTPException(422, "wait must be a number of seconds.")
    out: list[dict] = []
    for msg in msgs:
        if len(json.dumps(msg)) > live.MAX_MSG_BYTES:
            out.append({"t": "error", "why": "too big"})
            continue
        if isinstance(msg, dict) and msg.get("t") == "hello":
            out += room.hello(seat, since)
        else:
            out += room.receive(seat, msg)
    together.mark_seen(match_id, uid)
    room.check()
    await _write_end(room)
    if room.watch_due(every=LIVE_WATCH_EVERY):
        await run_in_threadpool(together.live_watch, match_id)
    loop = asyncio.get_running_loop()
    end = loop.time() + min(LIVE_POLL_MAX, max(0.0, float(wait)))
    numbered = room.pull(seat, since)
    while not numbered and not out and loop.time() < end and room.phase != "ended":
        await asyncio.sleep(0.02)
        numbered = room.pull(seat, since)
    res = {"msgs": out + numbered}
    v = body.get("v")
    if v is None or v != together.signature(match_id):
        res["match"] = await run_in_threadpool(together.snapshot, match_id, uid)
    return res
