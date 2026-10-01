"""Live updates (SPEC §8): an in-memory hub of Server-Sent Event connections.

One app runs one process (uvicorn, 1 worker), so a dict in memory is the
whole "message bus". `publish()` may be called from any thread (the request
handlers run in a thread pool); it hands events to each connection's asyncio
queue on the event loop. Recipients are worked out by the caller from the
database at send time, so someone removed from a chat never gets its events.

Also here: which tab is looking at which chat (for "already looking" in §7),
who's online, and typing.
"""
import asyncio
import json
import threading
import time

QUEUE_MAX = 300
LOOKING_SECONDS = 30
ONLINE_SECONDS = 60


class Connection:
    def __init__(self, user_id: str, loop: asyncio.AbstractEventLoop):
        self.user_id = user_id
        self.loop = loop
        self.queue: asyncio.Queue = asyncio.Queue(maxsize=QUEUE_MAX)
        self.closed = False

    def _put(self, item) -> None:
        if self.closed:
            return
        try:
            self.queue.put_nowait(item)
        except asyncio.QueueFull:
            # a slow client: drop it, it reconnects and catches up with Last-Event-ID
            self.closed = True
            try:
                self.queue.get_nowait()
            except asyncio.QueueEmpty:
                pass
            self.queue.put_nowait(None)

    def send(self, item) -> None:
        try:
            self.loop.call_soon_threadsafe(self._put, item)
        except RuntimeError:      # loop closed
            self.closed = True


class Hub:
    def __init__(self):
        self._lock = threading.Lock()
        self._conns: dict[str, set[Connection]] = {}
        self._tabs: dict[str, dict[str, tuple]] = {}     # user → tab id → (conversation id, visible, time)
        self._heartbeat: dict[str, float] = {}
        self.on_online_change = None                     # set by main: fn(user_id, online)

    # ----- connections -----
    def register(self, user_id: str) -> Connection:
        c = Connection(user_id, asyncio.get_running_loop())
        with self._lock:
            was = self._online_locked(user_id)
            self._conns.setdefault(user_id, set()).add(c)
            self._heartbeat[user_id] = time.monotonic()
        if not was and self.on_online_change:
            self.on_online_change(user_id, True)
        return c

    def unregister(self, c: Connection) -> None:
        with self._lock:
            s = self._conns.get(c.user_id)
            if s:
                s.discard(c)
                if not s:
                    del self._conns[c.user_id]
        c.closed = True

    def close_user(self, user_id: str) -> None:
        """Disabled: end their live connections at once."""
        with self._lock:
            conns = list(self._conns.pop(user_id, set()))
            self._tabs.pop(user_id, None)
            self._heartbeat.pop(user_id, None)
        for c in conns:
            c.send(None)
        if conns and self.on_online_change:
            self.on_online_change(user_id, False)

    def connected_users(self) -> set:
        with self._lock:
            return set(self._conns)

    def connection_count(self) -> int:
        with self._lock:
            return sum(len(s) for s in self._conns.values())

    # ----- events -----
    def publish(self, user_ids, event: str, data: dict, event_id: int | None = None) -> None:
        payload = format_event(event, data, event_id)
        with self._lock:
            conns = [c for u in set(user_ids) for c in self._conns.get(u, ())]
        for c in conns:
            c.send(payload)

    def publish_all(self, event: str, data: dict) -> None:
        self.publish(self.connected_users(), event, data)

    # ----- presence: looking at a chat, online -----
    def set_tab(self, user_id: str, tab: str, conversation_id: str | None, visible: bool) -> None:
        with self._lock:
            tabs = self._tabs.setdefault(user_id, {})
            tabs[tab[:40]] = (conversation_id, bool(visible), time.monotonic())
            if len(tabs) > 20:
                oldest = min(tabs, key=lambda k: tabs[k][2])
                tabs.pop(oldest, None)
            self._heartbeat[user_id] = time.monotonic()

    def heartbeat(self, user_id: str) -> None:
        with self._lock:
            self._heartbeat[user_id] = time.monotonic()

    def is_looking(self, user_id: str, conversation_id: str) -> bool:
        now = time.monotonic()
        with self._lock:
            return any(cid == conversation_id and vis and now - t < LOOKING_SECONDS
                       for cid, vis, t in self._tabs.get(user_id, {}).values())

    def _online_locked(self, user_id: str) -> bool:
        if self._conns.get(user_id):
            return True
        t = self._heartbeat.get(user_id)
        return t is not None and time.monotonic() - t < ONLINE_SECONDS

    def is_online(self, user_id: str) -> bool:
        with self._lock:
            return self._online_locked(user_id)

    def expire(self) -> list:
        """Drop stale tabs and heartbeats; → users who just went offline."""
        now = time.monotonic()
        gone = []
        with self._lock:
            for u, tabs in list(self._tabs.items()):
                for k in [k for k, v in tabs.items() if now - v[2] > 120]:
                    tabs.pop(k, None)
                if not tabs:
                    self._tabs.pop(u, None)
            for u, t in list(self._heartbeat.items()):
                if now - t > ONLINE_SECONDS and not self._conns.get(u):
                    self._heartbeat.pop(u, None)
                    gone.append(u)
        if self.on_online_change:
            for u in gone:
                self.on_online_change(u, False)
        return gone

    def reset(self) -> None:
        """End every live connection (after a restore); browsers reconnect and reload."""
        with self._lock:
            conns = [c for s in self._conns.values() for c in s]
            self._conns.clear()
            self._tabs.clear()
            self._heartbeat.clear()
        for c in conns:
            c.send(None)


def format_event(event: str, data: dict, event_id: int | None = None) -> str:
    out = f"id: {event_id}\n" if event_id is not None else ""
    return out + f"event: {event}\ndata: {json.dumps(data, separators=(',', ':'), ensure_ascii=False)}\n\n"


hub = Hub()
