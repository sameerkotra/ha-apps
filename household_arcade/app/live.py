"""Live duels on two phones (SPEC §13.4): the relay and referee for lockstep play.

Both phones run the same game rules (JavaScript, in the browser) from the same seed; each sends only its own
inputs, stamped with the update ("tick") they are played on. This module never runs a game. Per live match it
keeps a `Room` in memory that

- **relays** each phone's input messages to the other phone, in order: a message is accepted only as the next of
  its sender (`seq` = last + 1; an older one is a duplicate and is only acknowledged again, a later one is a gap
  the phone is told about, so it sends again from there), checked (size, rate, the game's actions and their
  values, the ticks it may speak for) and numbered per receiver (`n`), so a phone that reconnects says the last
  `n` it has (`hello`) and gets the rest again;
- **starts** the match once both phones are ready (with a count-in), choosing the input delay from the round
  trips the phones measured;
- **compares** the checksums both phones send every second: different → the match ends "out of step", with no
  result;
- **watches** the phones: one silent for 10 s pauses both ("waiting for …"), silent for 60 s it has left (the
  other wins); a player's pause pauses both (at most 5 minutes).

Nothing here touches the database: an end the room decides is taken by the caller (`take_end()`) and written by
`together.end_live`; an end decided elsewhere (both results in, a resignation, a child's time) reaches the room
through `on_end()`. Rooms are created when an invite is accepted and forgotten 10 minutes after the end. All
methods take the room's lock (they are called from the event loop and from worker threads).

Messages (JSON objects; at most MAX_MSG_BYTES each) — from a phone:
  hello {since}            first on every connection: the last numbered message it has
  ping {id, ack?}          every 2 s (keeps the phone "here"); answered with pong {id}
  ready {rtt}              the game page has its session; rtt = its measured round trip in ms
  in {seq, upto, ev, sum?} its inputs: ev = [[tick, action, value], …] for ticks after its last `upto` up to
                           this `upto` (the last tick its inputs are complete for); sum = [tick, checksum]
  pause, resume
to a phone (numbered: n = 1, 2, 3 … per phone, kept until acknowledged):
  in {seat, seq, upto, ev}  the other phone's inputs
  start {delay, inMs}       both are ready: count in, then play tick 1
  pause {by, why}           why = "player" (by a seat) or "lost" (that seat's phone went quiet)
  resume
  end {reason, winner}      winner = seat 1 or 2, 0 a draw, null no result
and not numbered: welcome {seat, inSeq, delay, phase, last}, ack {seq}, pong {id}, error {why, inSeq}.

**Taking turns live** (Carrom; `games.py` `live.turns`): the players take turns, each move ("shot") a single input,
so no stream of inputs per update is needed. The same messages and checks, with these differences: a shot is
`in {seq, upto: k, ev: [[k, "shot", [a, b, c]]]}` for shot number k = the shots so far + 1, only from the seat whose
turn it is; it is relayed to *both* phones (the server's order is the only order: a phone plays its own shot when it
comes back), and stored (`take_shots`, into match_moves). After a shot has stopped moving each phone sends
`in {seq, upto: k, ev: [], sum: [k, checksum], next: seat | 0}` — the checksums are compared as in a duel, and the
first report says whose turn is next (0: the game is over). The turn's clock: SHOT_SECONDS from the first report (or
the count-in's end); when it runs out (+ SHOT_GRACE for the network) the server plays the game's weak `timeout_shot`
for that player (`in {seat, seq: 0, upto: k, ev, timer: true}`, to both). A shot of the mover's that comes after
the server's timeout shot took its number is acknowledged and dropped. Paused time doesn't count.
"""
from __future__ import annotations

import logging
import math
import threading
import time
from collections import deque

logger = logging.getLogger("live")

clock = time.monotonic           # replaced by the tests

PROTOCOL = 1
MAX_MSG_BYTES = 2048             # one message from a phone
MAX_EV = 32                      # inputs in one message
MAX_PER_TICK = 8                 # inputs one player can put on one tick
MAX_AHEAD = 600                  # ticks one message may move `upto` on
SUM_EVERY = 60                   # a checksum every second of play
SUMS_KEPT = 30
RATE = 90.0                      # messages a second a phone may send (it sends at most one an update and a ping
BURST = 180.0                    # every 2 s), with a burst for a reconnection
MAX_BAD = 30                     # refused messages before the connection is closed
STALL_SECONDS = 10.0             # a phone silent this long pauses both
GONE_SECONDS = 60.0              # … and this long has left the match
READY_SECONDS = 120.0            # both phones must be ready this soon after the invite was accepted
PAUSE_MAX_SECONDS = 300.0        # a pause lasts at most 5 minutes
COUNT_IN_SECONDS = 3.0
CONNECTED_WITHIN = 6.0
DELAY_MIN, DELAY_MAX, DELAY_DEFAULT_RTT = 4, 16, 60.0
DELAY_HEADROOM = 4               # ticks on top of the one-way time: a frame to send, a frame to draw, and two of jitter
MAX_INPUT_DELAY = 30             # a phone may raise its own input delay to this (games/lockstep.js) when the link is slow
SHOT_SECONDS = 30.0              # taking turns live: a player has this long to shoot …
SHOT_GRACE = 2.0                 # … plus this for the network, then the server plays the weak shot for them
OUTBOX_MAX = 6000                # numbered messages waiting for a phone that doesn't read them
FORGET_AFTER = 600.0             # an ended room is kept this long (for late reconnections)
TICK_MS = 1000.0 / 60

ROOMS: dict[str, "Room"] = {}
REPORTS: dict[str, dict[int, dict]] = {}     # match → seat → the end of the game as that phone saw it
_rooms_lock = threading.Lock()


def choose_delay(rtts) -> int:
    """The input delay in ticks to start with: the time an input takes from one phone through the app to the other
    (half of each phone's round trip), plus DELAY_HEADROOM ticks for sending, drawing and jitter; 4 at home, at most
    16 (0.27 s). It is the least: each phone raises its own delay while the link makes it wait (games/lockstep.js)."""
    known = [r for r in rtts if isinstance(r, (int, float)) and not isinstance(r, bool) and r == r]
    one_way = sum(known) / 2 if len(known) == 2 else (known[0] if known else DELAY_DEFAULT_RTT)
    return max(DELAY_MIN, min(DELAY_MAX, math.ceil(one_way / TICK_MS) + DELAY_HEADROOM))


def _int(v) -> bool:
    return isinstance(v, int) and not isinstance(v, bool)


class Player:
    def __init__(self, seat: int, uid: str, now: float):
        self.seat, self.uid = seat, uid
        self.ready, self.rtt = False, None
        self.heard: float | None = None
        self.seq, self.upto = 0, 0
        self.sums: dict[int, int] = {}
        self.out: deque = deque()          # numbered messages for this phone, until it acknowledges them
        self.n = 0
        self.tokens, self.tok_at = BURST, now
        self.bad = 0

    def connected(self, now: float) -> bool:
        return self.heard is not None and now - self.heard < CONNECTED_WITHIN


class Room:
    def __init__(self, match_id: str, game: str, actions: dict, seats: dict[str, int], now: float | None = None,
                 turns: dict | None = None, first: int = 1):
        now = clock() if now is None else now
        self.match_id, self.game = match_id, game
        self.turns = turns is not None              # taking turns live (Carrom): one input a move, relayed to both
        if self.turns:
            self.actions = {k: [(int(r[0]), int(r[1])) for r in v] for k, v in actions.items()}
            self.timeout_shot = list(turns.get("timeout_shot") or [])
        else:
            self.actions = {k: (int(v[0]), int(v[1])) for k, v in actions.items()}
        self.shots = 0                               # shots taken (taking turns)
        self.mover: int | None = first if first in (1, 2) else 1   # whose shot is next (None: one is moving)
        self.turn_since: float | None = None
        self.shot_log: list[dict] = []               # shots not yet stored in match_moves (take_shots)
        self.next_seen: dict[int, int] = {}          # shot number → the "next" the first report gave
        self.stopped_at: float | None = None
        self._now = now
        self.players = {seat: Player(seat, uid, now) for uid, seat in seats.items()}
        self.lock = threading.RLock()
        self.created = now
        self.phase = "waiting"             # waiting | countin | running | paused | ended
        self.delay: int | None = None
        self.wakers: dict = {}             # seat → called when a message is put out for it (wake_with)
        self.start_at: float | None = None
        self.paused_by, self.pause_why, self.paused_at = 0, None, None
        self.end: tuple | None = None       # (reason, winner seat 1/2, 0 draw, None no result)
        self.watched = now                  # the last look at the players' play time (children)
        self.ended_at: float | None = None
        self._pending_end: tuple | None = None

    # ---------- who ----------
    def seat_of(self, uid: str) -> int | None:
        for seat, p in self.players.items():
            if p.uid == uid:
                return seat
        return None

    def _other(self, seat: int) -> Player:
        return self.players[3 - seat]

    # ---------- the numbered messages ----------
    def _push(self, seat: int, msg: dict) -> None:
        p = self.players[seat]
        p.n += 1
        msg = dict(msg, n=p.n)
        p.out.append(msg)
        if len(p.out) > OUTBOX_MAX and self.phase != "ended":
            logger.warning("Live match %s: seat %s isn't reading; ending the match", self.match_id, seat)
            self._finish("left", 3 - seat)
        waker = self.wakers.get(seat)
        if waker is not None:
            try:
                waker()
            except Exception:                   # a connection that is going: its reader is closing anyway
                pass

    def wake_with(self, seat: int, waker) -> None:
        """Called (from any thread) whenever a numbered message is put out for `seat`: the phone's connection
        sends it at once instead of looking every few milliseconds. None to stop."""
        with self.lock:
            if waker is None:
                self.wakers.pop(seat, None)
            else:
                self.wakers[seat] = waker

    def _broadcast(self, msg: dict) -> None:
        for seat in self.players:
            self._push(seat, msg)

    def _ack_to(self, p: Player, n) -> None:
        if _int(n):
            while p.out and p.out[0]["n"] <= n:
                p.out.popleft()

    def pull(self, seat: int, since: int) -> list[dict]:
        """The numbered messages for `seat` after `since` (which it has, so they are dropped)."""
        with self.lock:
            p = self.players[seat]
            self._ack_to(p, since)
            return [m for m in p.out if m["n"] > since]

    # ---------- from a phone ----------
    def hello(self, seat: int, since, now: float | None = None) -> list[dict]:
        now = clock() if now is None else now
        with self.lock:
            p = self.players[seat]
            p.heard = now
            self._ack_to(p, since if _int(since) else 0)
            return [{"t": "welcome", "protocol": PROTOCOL, "seat": seat, "inSeq": p.seq, "delay": self.delay,
                     "phase": self.phase, "last": p.n}]

    def receive(self, seat: int, msg, now: float | None = None) -> list[dict]:
        """One message from a phone; returns the replies for that phone that aren't numbered."""
        now = clock() if now is None else now
        with self.lock:
            p = self.players[seat]
            p.heard = now
            self._now = now
            # the rate limit: a token bucket of RATE a second
            p.tokens = min(BURST, p.tokens + (now - p.tok_at) * RATE)
            p.tok_at = now
            if p.tokens < 1:
                return [self._bad(p, "slow down")]
            p.tokens -= 1
            if not isinstance(msg, dict):
                return [self._bad(p, "not a message")]
            t = msg.get("t")
            if "ack" in msg:
                self._ack_to(p, msg.get("ack"))
            if t == "ping":
                pid = msg.get("id")
                return [{"t": "pong", "id": pid if _int(pid) else None}]
            if t == "ready":
                return self._ready(p, msg.get("rtt"), now)
            if t == "in":
                return self._input(p, msg)
            if t == "pause":
                self._pause(seat, "player", now)
                return []
            if t == "resume":
                self._resume(now)
                return []
            if t == "hello":
                return self.hello(seat, msg.get("since"), now)
            return []                       # something this version doesn't know: ignored

    def _bad(self, p: Player, why: str) -> dict:
        p.bad += 1
        return {"t": "error", "why": why, "inSeq": p.seq}

    def closing(self, seat: int) -> bool:
        """Too many refused messages from this phone: its connection is closed."""
        return self.players[seat].bad > MAX_BAD

    def _ready(self, p: Player, rtt, now: float) -> list[dict]:
        if isinstance(rtt, (int, float)) and not isinstance(rtt, bool) and rtt == rtt:
            p.rtt = max(0.0, min(5000.0, float(rtt)))
        if self.phase != "waiting":
            return []
        p.ready = True
        if all(x.ready for x in self.players.values()):
            self.phase, self.start_at = "countin", now + COUNT_IN_SECONDS
            if self.turns:                         # no input delay: a shot is played when it comes back from here
                self.delay = 0
                self.turn_since = self.start_at
                self._broadcast({"t": "start", "delay": 0, "inMs": int(COUNT_IN_SECONDS * 1000), "turns": True,
                                 "first": self.mover})
                return []
            self.delay = choose_delay([x.rtt for x in self.players.values()])
            for x in self.players.values():
                x.upto = self.delay - 1          # ticks 1 … delay-1 never have inputs
            self._broadcast({"t": "start", "delay": self.delay, "inMs": int(COUNT_IN_SECONDS * 1000)})
        return []

    def _input(self, p: Player, msg: dict) -> list[dict]:
        seq, upto, ev, sm = msg.get("seq"), msg.get("upto"), msg.get("ev"), msg.get("sum")
        if not _int(seq) or seq < 1:
            return [self._bad(p, "seq")]
        if seq <= p.seq:                    # sent again after a reconnection: already have it
            return [{"t": "ack", "seq": p.seq}]
        if seq != p.seq + 1:                # one is missing: send again from the one after inSeq
            return [{"t": "error", "why": "gap", "inSeq": p.seq}]
        if self.phase not in ("countin", "running", "paused"):
            return [self._bad(p, "not started")]
        if self.turns:
            return self._turn_input(p, msg, seq)
        other = self._other(p.seat)
        if not _int(upto) or upto < p.upto or upto - p.upto > MAX_AHEAD:
            return [self._bad(p, "upto")]
        # a phone plays tick k only with the other's inputs for it, so it can't speak for ticks further ahead
        # than the other phone's last word plus the most its own input delay can be
        if upto > other.upto + MAX_INPUT_DELAY:
            return [self._bad(p, "ahead")]
        at = msg.get("at")                  # the tick the phone is at (the other phone paces itself by it)
        if at is not None and (not _int(at) or at < 0 or at > upto):
            return [self._bad(p, "at")]
        if not isinstance(ev, list) or len(ev) > MAX_EV:
            return [self._bad(p, "ev")]
        last, per = p.upto + 1, 0
        for e in ev:
            if not isinstance(e, list) or len(e) != 3:
                return [self._bad(p, "ev")]
            tick, action, value = e
            if not _int(tick) or tick <= p.upto or tick > upto or tick < last:
                return [self._bad(p, "tick")]
            per = per + 1 if tick == last else 1
            last = tick
            if per > MAX_PER_TICK:
                return [self._bad(p, "too many")]
            rng = self.actions.get(action) if isinstance(action, str) else None
            if rng is None:
                return [self._bad(p, "action")]
            if not _int(value) or not rng[0] <= value <= rng[1]:
                return [self._bad(p, "value")]
        if sm is not None:
            if (not isinstance(sm, list) or len(sm) != 2 or not _int(sm[0]) or not _int(sm[1]) or sm[0] <= 0
                    or sm[0] % SUM_EVERY or sm[0] > upto - self.delay + 1 or not 0 <= sm[1] < 2 ** 32):
                return [self._bad(p, "sum")]
        # accepted: on to the other phone
        p.seq, p.upto = seq, upto
        relay = {"t": "in", "seat": p.seat, "seq": seq, "upto": upto, "ev": ev}
        if at is not None:
            relay["at"] = at
        self._push(other.seat, relay)
        if sm is not None:
            self._checksum(p, sm[0], sm[1])
        return [{"t": "ack", "seq": seq}]

    def _valid_shot(self, action, value) -> bool:
        rngs = self.actions.get(action) if isinstance(action, str) else None
        return (rngs is not None and isinstance(value, list) and len(value) == len(rngs)
                and all(_int(v) and lo <= v <= hi for v, (lo, hi) in zip(value, rngs)))

    def _turn_input(self, p: Player, msg: dict, seq: int) -> list[dict]:
        """Taking turns live: a shot (from the seat whose turn it is, numbered shots + 1) or the report after one."""
        upto, ev, sm, nxt = msg.get("upto"), msg.get("ev"), msg.get("sum"), msg.get("next")
        if not _int(upto) or not isinstance(ev, list) or len(ev) > 1:
            return [self._bad(p, "ev" if not isinstance(ev, list) or len(ev) > 1 else "upto")]
        if ev:
            e = ev[0]
            if not isinstance(e, list) or len(e) != 3 or not _int(e[0]) or e[0] != upto:
                return [self._bad(p, "ev")]
            k, action, value = e
            if not self._valid_shot(action, value):
                return [self._bad(p, "value" if action in self.actions else "action")]
            if k <= self.shots:                      # the server's timeout shot took this number: too late, dropped
                p.seq = seq
                return [{"t": "ack", "seq": seq, "late": True}]
            if k != self.shots + 1:
                return [self._bad(p, "upto")]
            if self.phase != "running":
                return [self._bad(p, "not started" if self.phase == "countin" else "paused")]
            if self.mover != p.seat:
                return [self._bad(p, "turn")]
            p.seq = seq
            self._shot(p.seat, k, value, seq, False)
            return [{"t": "ack", "seq": seq}]
        # a report: the shot `k` has stopped moving on this phone
        if not (isinstance(sm, list) and len(sm) == 2 and _int(sm[0]) and _int(sm[1]) and 1 <= sm[0] <= self.shots
                and 0 <= sm[1] < 2 ** 32 and sm[0] not in p.sums and (nxt is None or (_int(nxt) and nxt in (0, 1, 2)))):
            return [self._bad(p, "sum")]
        p.seq = seq
        k = sm[0]
        self._checksum(p, k, sm[1])
        if self.phase != "ended" and nxt is not None and k == self.shots and self.mover is None and k not in self.next_seen:
            self.next_seen[k] = nxt
            if nxt in (1, 2):
                self.mover, self.turn_since = nxt, self._now
        return [{"t": "ack", "seq": seq}]

    def _shot(self, seat: int, k: int, value: list, seq: int, timer: bool) -> None:
        self.shots, self.mover = k, None
        msg = {"t": "in", "seat": seat, "seq": seq, "upto": k, "ev": [[k, "shot", list(value)]]}
        if timer:
            msg["timer"] = True
        self._broadcast(msg)
        self.shot_log.append({"k": k, "seat": seat, "value": list(value), "timer": timer})

    def take_shots(self) -> list[dict]:
        """Shots not yet stored (taking turns live), once each, for match_moves."""
        with self.lock:
            out, self.shot_log = self.shot_log, []
            return out

    def _checksum(self, p: Player, tick: int, value: int) -> None:
        p.sums[tick] = value
        if len(p.sums) > SUMS_KEPT:
            del p.sums[min(p.sums)]
        theirs = self._other(p.seat).sums.get(tick)
        if theirs is not None and theirs != value and self.phase != "ended":
            logger.warning("Live match %s (%s) out of step at tick %s: %s != %s", self.match_id, self.game, tick,
                           value, theirs)
            self._finish("out_of_step", None)

    def _pause(self, seat: int, why: str, now: float) -> None:
        if self.phase not in ("running", "countin") and not (self.phase == "paused" and self.pause_why == "lost"
                                                            and why == "player"):
            return
        if self.phase != "paused":
            self.stopped_at = now                # the shot clock (taking turns) stands still while paused
        self.phase, self.paused_by, self.pause_why, self.paused_at = "paused", seat, why, now
        self._broadcast({"t": "pause", "by": seat, "why": why})

    def _resume(self, now: float) -> None:
        if self.phase != "paused":
            return
        if self.pause_why == "lost":
            lost = self.players.get(self.paused_by)
            if lost and (lost.heard is None or now - lost.heard >= STALL_SECONDS):
                return                      # that phone is still quiet
        if self.turns and self.turn_since is not None and self.stopped_at is not None:
            self.turn_since += max(0.0, now - max(self.stopped_at, self.turn_since))    # the shot clock stood still
        self.stopped_at = None
        self.phase, self.paused_by, self.pause_why, self.paused_at = "running", 0, None, None
        self._broadcast({"t": "resume"})

    # ---------- the clock ----------
    def check(self, now: float | None = None) -> None:
        """Timeouts: the count-in is over, a phone went quiet (pause) or away (left), a pause went on too long."""
        now = clock() if now is None else now
        with self.lock:
            if self.phase == "ended":
                return
            if self.phase == "waiting":
                if now - self.created > READY_SECONDS:
                    ready = [p.seat for p in self.players.values() if p.ready and p.connected(now)]
                    self._finish("left", ready[0] if len(ready) == 1 else None)
                return
            if self.phase == "countin" and now >= self.start_at:
                self.phase = "running"
            quiet = {}
            for p in self.players.values():
                since = p.heard if p.heard is not None else (self.start_at or self.created)
                quiet[p.seat] = now - since
            gone = [s for s, q in quiet.items() if q > GONE_SECONDS]
            if gone:
                stay = [s for s in self.players if s not in gone]
                self._finish("left", stay[0] if len(stay) == 1 else None)
                return
            if self.phase == "running":
                lost = [s for s, q in quiet.items() if q > STALL_SECONDS]
                if lost:
                    self._pause(lost[0], "lost", now)
                elif (self.turns and self.mover and self.turn_since is not None
                      and now - self.turn_since > SHOT_SECONDS + SHOT_GRACE):
                    logger.info("Live match %s: seat %s didn't shoot in time; the weak shot is played", self.match_id,
                                self.mover)
                    self._shot(self.mover, self.shots + 1, self.timeout_shot, 0, True)
            elif self.phase == "paused":
                if self.pause_why == "lost" and quiet.get(self.paused_by, 0) < STALL_SECONDS:
                    self._resume(now)
                elif self.pause_why == "player" and now - self.paused_at > PAUSE_MAX_SECONDS:
                    self._finish("timeout", None)

    def watch_due(self, now: float | None = None, every: float = 5.0) -> bool:
        """True at most every `every` seconds: time to look at the players' play time (whichever phone asks)."""
        now = clock() if now is None else now
        with self.lock:
            if now - self.watched < every:
                return False
            self.watched = now
            return True

    # ---------- the end ----------
    def _finish(self, reason: str, winner) -> None:
        """The room decides the end: tell both phones; the caller writes it (take_end)."""
        if self.phase == "ended":
            return
        self._pending_end = (reason, winner)
        self._ended(reason, winner)

    def _ended(self, reason: str, winner) -> None:
        if self.phase == "ended":
            return
        self.phase, self.end, self.ended_at = "ended", (reason, winner), clock()
        self._broadcast({"t": "end", "reason": reason, "winner": winner})

    def take_end(self) -> tuple | None:
        """An end decided here that the database doesn't have yet (once)."""
        with self.lock:
            e, self._pending_end = self._pending_end, None
            return e

    def ended(self, reason: str, winner) -> None:
        """The match ended elsewhere (results in, a resignation, a child's time): tell both phones."""
        with self.lock:
            self._pending_end = None
            self._ended(reason, winner)

    # ---------- what the match picture shows ----------
    def picture(self, now: float | None = None) -> dict:
        now = clock() if now is None else now
        with self.lock:
            out = {"phase": self.phase, "delay": self.delay, "pausedBy": self.paused_by or None,
                   "why": self.pause_why,
                   "seats": {str(s): {"connected": p.connected(now), "ready": p.ready}
                             for s, p in self.players.items()}}
            if self.turns:
                left = None
                if self.mover and self.turn_since is not None and self.phase == "running":
                    left = max(0, int(SHOT_SECONDS - (now - self.turn_since) + 0.999))
                out["turns"] = {"shots": self.shots, "mover": self.mover, "left": left}
            return out


# ---------------------------------------------------------------------------
# The rooms
# ---------------------------------------------------------------------------

def open_room(match_id: str, game: str, actions: dict, seats: dict[str, int], turns: dict | None = None,
              first: int = 1) -> Room:
    with _rooms_lock:
        room = ROOMS.get(match_id)
        if room is None:
            room = ROOMS[match_id] = Room(match_id, game, actions, seats, turns=turns, first=first)
        return room


def get(match_id: str) -> Room | None:
    return ROOMS.get(match_id)


def on_end(match_id: str, reason: str, winner) -> None:
    room = ROOMS.get(match_id)
    if room is not None:
        room.ended(reason, winner)


def store_report(match_id: str, seat: int, report) -> None:
    """The end of the game as one phone saw it (sent with that player's score): {tick, sum, scores, levels,
    winner}. Both must be the same for the match to have a result."""
    clean = None
    if isinstance(report, dict):
        tick, sm, sc, lv, w = (report.get(k) for k in ("tick", "sum", "scores", "levels", "winner"))
        if (_int(tick) and _int(sm) and isinstance(sc, list) and len(sc) == 2 and all(_int(x) for x in sc)
                and _int(w) and w in (0, 1, 2)):
            clean = {"tick": tick, "sum": sm, "scores": sc, "winner": w,
                     "levels": lv if isinstance(lv, list) and len(lv) == 2 and all(_int(x) for x in lv) else None}
    REPORTS.setdefault(match_id, {})[seat] = clean


def cleanup(now: float | None = None) -> int:
    """Forget rooms (and reports) of matches that ended a while ago."""
    now = clock() if now is None else now
    n = 0
    with _rooms_lock:
        for mid, room in list(ROOMS.items()):
            if room.phase == "ended" and room.ended_at is not None and now - room.ended_at > FORGET_AFTER:
                ROOMS.pop(mid, None)
                REPORTS.pop(mid, None)
                n += 1
    return n


def forget(match_id: str) -> None:
    with _rooms_lock:
        ROOMS.pop(match_id, None)
        REPORTS.pop(match_id, None)
