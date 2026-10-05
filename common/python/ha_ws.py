"""A small WebSocket client to Home Assistant's event bus, for several subscriptions.

Shared by the household apps (common/python/ha_ws.py, copied into an app's
app/common/ by tools/sync_common.py when it uses it). Standard library only,
no imports from the app: the URL and token are parameters.

Generalised from Household Chat's ha_events.py: one connection to
`ws://supervisor/core/websocket` (the Supervisor token; covered by
`homeassistant_api`), text frames only, one daemon thread that authenticates,
subscribes to every registered event type, delivers events to their callbacks
and reconnects with back-off (5 s doubling to 5 min) when the connection drops.
Subscriptions survive reconnects.

    ws = HAWebSocket(url, token)
    ws.subscribe("household_apps", on_event)      # on_event(event: dict), in the client's thread
    ws.on_connect(lambda first: ...)              # after auth + subscribe, on every (re)connect
    ws.start()
    ...
    ws.stop()                                     # closes the socket and joins the thread

Callbacks run in the client's thread, one at a time; an exception in a
callback is logged and doesn't drop the connection.

One connection can serve several users: Household Chat's notification
buttons (ha_events.py) and the app bus (app_bus.start(..., ws=that client))
share one; each user removes only its own callbacks (unsubscribe/off_connect).
"""
import base64
import json
import logging
import os
import select
import socket
import struct
import threading
import time
from urllib.parse import urlparse

logger = logging.getLogger("ha_ws")

DEFAULT_URL = "ws://supervisor/core/websocket"


class WSClosed(Exception):
    pass


class MiniWS:
    """One client WebSocket connection (ws:// only, text frames, JSON messages)."""

    def __init__(self, url: str, timeout: float = 30):
        u = urlparse(url)
        if u.scheme != "ws":
            raise ValueError("Only ws:// is supported")
        self.sock = socket.create_connection((u.hostname, u.port or 80), timeout=timeout)
        self._send_lock = threading.Lock()
        try:
            self._handshake(u)
        except BaseException:
            self.close()
            raise

    def _handshake(self, u) -> None:
        key = base64.b64encode(os.urandom(16)).decode()
        path = u.path or "/"
        req = (f"GET {path} HTTP/1.1\r\nHost: {u.hostname}\r\nUpgrade: websocket\r\nConnection: Upgrade\r\n"
               f"Sec-WebSocket-Key: {key}\r\nSec-WebSocket-Version: 13\r\n\r\n")
        self.sock.sendall(req.encode())
        head = b""
        while b"\r\n\r\n" not in head:
            chunk = self.sock.recv(1024)
            if not chunk:
                raise WSClosed("closed during handshake")
            head += chunk
            if len(head) > 16384:
                raise WSClosed("bad handshake")
        status = head.split(b"\r\n", 1)[0]
        if b" 101 " not in status + b" ":
            raise WSClosed(f"handshake refused: {status[:60]!r}")
        self.buf = head.split(b"\r\n\r\n", 1)[1]

    def _recv_exact(self, n: int) -> bytes:
        while len(self.buf) < n:
            chunk = self.sock.recv(65536)
            if not chunk:
                raise WSClosed("closed")
            self.buf += chunk
        out, self.buf = self.buf[:n], self.buf[n:]
        return out

    def _send_frame(self, opcode: int, data: bytes) -> None:
        head = bytearray([0x80 | opcode])
        n = len(data)
        if n < 126:
            head.append(0x80 | n)
        elif n < 65536:
            head.append(0x80 | 126)
            head += struct.pack("!H", n)
        else:
            head.append(0x80 | 127)
            head += struct.pack("!Q", n)
        mask = os.urandom(4)
        head += mask
        with self._send_lock:
            self.sock.sendall(bytes(head) + bytes(b ^ mask[i % 4] for i, b in enumerate(data)))

    def send(self, obj: dict) -> None:
        self._send_frame(0x1, json.dumps(obj).encode())

    def readable(self, timeout: float) -> bool:
        """True when a message may be waiting (buffered bytes or data on the socket)."""
        if self.buf:
            return True
        r, _, _ = select.select([self.sock], [], [], timeout)
        return bool(r)

    def recv(self) -> dict:
        message = b""
        while True:
            b1, b2 = self._recv_exact(2)
            fin, opcode = b1 & 0x80, b1 & 0x0F
            n = b2 & 0x7F
            if n == 126:
                n = struct.unpack("!H", self._recv_exact(2))[0]
            elif n == 127:
                n = struct.unpack("!Q", self._recv_exact(8))[0]
            if n > 16 * 1024 * 1024:
                raise WSClosed("frame too large")
            mask = self._recv_exact(4) if b2 & 0x80 else None
            payload = self._recv_exact(n)
            if mask:
                payload = bytes(b ^ mask[i % 4] for i, b in enumerate(payload))
            if opcode == 0x8:
                raise WSClosed("closed by server")
            if opcode == 0x9:
                self._send_frame(0xA, payload)
                continue
            if opcode == 0xA:
                continue
            message += payload
            if fin:
                return json.loads(message.decode("utf-8"))

    def close(self) -> None:
        try:
            self.sock.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass
        try:
            self.sock.close()
        except OSError:
            pass


class HAWebSocket:
    """Home Assistant event subscriptions over one reconnecting WebSocket (one thread)."""

    def __init__(self, url: str | None = None, token: str | None = None, *, name: str = "ha-ws",
                 retry_first: float = 5, retry_max: float = 300, ping_every: float = 50,
                 read_timeout: float = 30):
        self.url = url or os.environ.get("SUPERVISOR_CORE_WS", DEFAULT_URL)
        self.token = token if token is not None else os.environ.get("SUPERVISOR_TOKEN", "")
        self.name = name
        self.retry_first, self.retry_max = retry_first, retry_max
        self.ping_every, self.read_timeout = ping_every, read_timeout
        self._subs: dict[str, list] = {}        # event_type -> [callback(event)]
        self._on_connect: list = []
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._ws: MiniWS | None = None
        self._sub_ids: dict[int, str] = {}      # subscription id on the current connection -> event_type
        self._next_id = 1
        self.connected = threading.Event()      # set while authenticated and subscribed
        self.connections = 0                    # successful connections so far

    # -- registration ------------------------------------------------------
    def subscribe(self, event_type: str, callback) -> None:
        """Call `callback(event)` for every `event_type` event (`event` is HA's event object:
        event_type, data, origin, time_fired, context). Works before or after start()."""
        with self._lock:
            first = event_type not in self._subs
            self._subs.setdefault(event_type, []).append(callback)
            ws = self._ws if self.connected.is_set() else None
        if first and ws:
            try:
                self._subscribe_on(ws, event_type)
            except OSError:
                pass                             # the reader notices and reconnects

    def unsubscribe(self, event_type: str, callback) -> None:
        with self._lock:
            cbs = self._subs.get(event_type, [])
            if callback in cbs:
                cbs.remove(callback)             # the HA subscription stays until the next reconnect

    def on_connect(self, callback) -> None:
        """Call `callback(first: bool)` after each successful (re)connect, in the client's thread."""
        self._on_connect.append(callback)

    def off_connect(self, callback) -> None:
        """Stop calling an on_connect callback (e.g. a user of a shared connection stopping)."""
        if callback in self._on_connect:
            self._on_connect.remove(callback)

    # -- life cycle ----------------------------------------------------------
    def start(self) -> bool:
        """Start the thread. Returns False (and does nothing) without a token or when running."""
        if not self.token:
            return False
        if self._thread and self._thread.is_alive():
            return False
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, daemon=True, name=self.name)
        self._thread.start()
        return True

    def stop(self, timeout: float = 5) -> None:
        """Close the connection and wait for the thread to end."""
        self._stop.set()
        with self._lock:
            ws = self._ws
        if ws:
            ws.close()
        t = self._thread
        if t and t is not threading.current_thread():
            t.join(timeout)
        self._thread = None
        self.connected.clear()

    @property
    def running(self) -> bool:
        return bool(self._thread and self._thread.is_alive())

    # -- inside the thread -----------------------------------------------------
    def _subscribe_on(self, ws: MiniWS, event_type: str) -> None:
        with self._lock:
            sid = self._next_id
            self._next_id += 1
            self._sub_ids[sid] = event_type
        ws.send({"id": sid, "type": "subscribe_events", "event_type": event_type})

    def _connect(self) -> MiniWS:
        ws = MiniWS(self.url, timeout=min(self.read_timeout, 10))
        with self._lock:
            self._ws = ws
        try:
            self._authenticate(ws)
        except BaseException:
            ws.close()
            raise
        return ws

    def _authenticate(self, ws: MiniWS) -> None:
        if self._stop.is_set():
            raise WSClosed("stopping")
        ws.sock.settimeout(self.read_timeout)
        first = ws.recv()
        if first.get("type") != "auth_required":
            raise WSClosed("unexpected greeting")
        ws.send({"type": "auth", "access_token": self.token})
        if ws.recv().get("type") != "auth_ok":
            raise WSClosed("auth refused")
        with self._lock:
            self._sub_ids, self._next_id = {}, 1
            types = list(self._subs)
        for t in types:
            self._subscribe_on(ws, t)

    def _dispatch(self, msg: dict) -> None:
        if msg.get("type") != "event":
            if msg.get("type") == "result" and msg.get("success") is False:
                logger.warning("Home Assistant refused request %s: %s", msg.get("id"),
                               (msg.get("error") or {}).get("code"))
            return
        event = msg.get("event") or {}
        with self._lock:
            etype = self._sub_ids.get(msg.get("id")) or event.get("event_type")
            cbs = list(self._subs.get(etype, []))
        for cb in cbs:
            try:
                cb(event)
            except Exception:
                logger.exception("Handling a %s event failed", etype)

    def _run(self) -> None:
        delay = self.retry_first
        while not self._stop.is_set():
            ws = None
            try:
                ws = self._connect()
                self.connections += 1
                self.connected.set()
                logger.info("Connected to Home Assistant events (%s).", ", ".join(self._subs) or "none")
                delay = self.retry_first
                for cb in list(self._on_connect):
                    try:
                        cb(self.connections == 1)
                    except Exception:
                        logger.exception("on_connect callback failed")
                last_ping = last_heard = time.monotonic()
                ping_id = 0
                while not self._stop.is_set():
                    now = time.monotonic()
                    if now - last_ping >= self.ping_every:
                        with self._lock:
                            ping_id = self._next_id
                            self._next_id += 1
                        ws.send({"id": ping_id, "type": "ping"})
                        last_ping = now
                    if now - last_heard > 2 * self.ping_every + self.read_timeout:
                        raise WSClosed("no answer to ping")
                    if not ws.readable(min(self.ping_every, 5)):
                        continue
                    msg = ws.recv()
                    last_heard = time.monotonic()
                    self._dispatch(msg)
            except Exception as e:
                if not self._stop.is_set():
                    logger.info("Home Assistant event connection: %s — retrying in %s s", type(e).__name__, delay)
            finally:
                self.connected.clear()
                with self._lock:
                    self._ws = None
                if ws:
                    ws.close()
            self._stop.wait(delay)
            delay = min(delay * 2, self.retry_max)
