# Shared file: edit common/tests/fake_ha_bus.py and run tools/sync_common.py; don't edit this copy. sha256=f0d10130a7962df9a052c45781f0d7b15e9fd3775871d5598f0ff4010e882c07
"""A fake Home Assistant event bus for tests (shared: common/tests/fake_ha_bus.py).

Extends FakeHA (fake_ha.py) with what the app bus (app_bus.py, ha_ws.py) uses:

- `POST /api/events/<event_type>` — fires an event (Bearer token checked), delivered at once to every
  WebSocket subscribed to that type (or to all events);
- a WebSocket at `/websocket` (as `ws://supervisor/core/websocket`) and `/api/websocket`:
  `auth_required` → `auth` → `auth_ok`/`auth_invalid`, `subscribe_events` (optional `event_type`),
  `unsubscribe_events`, `ping` → `pong`. Text frames only.

Standard library only. Test hooks:

    bus = FakeHABus(token="t")
    api = bus.start()                 # "http://127.0.0.1:<port>/api"
    bus.ws_url                        # "ws://127.0.0.1:<port>/websocket"
    bus.fired                         # [(event_type, data)] in firing order
    bus.drop = lambda etype, data: …  # return True to lose that event (fired, never delivered)
    bus.drop_connections()            # close every WebSocket (as when HA restarts)
    bus.subscribers()                 # number of live subscriptions
"""
import base64
import hashlib
import json
import os
import socket
import struct
import threading
from http.server import ThreadingHTTPServer

try:
    from .fake_ha import FakeHA
except ImportError:                      # imported as a top-level module
    from fake_ha import FakeHA

_WS_GUID = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"


class _Conn:
    """One server-side WebSocket connection."""

    def __init__(self, sock, rfile):
        self.sock, self.rfile = sock, rfile
        self.lock = threading.Lock()
        self.subs = {}              # subscription id -> event_type or None (all)
        self.authed = False
        self.closed = False
        self.pongs = 0              # WebSocket pongs the client sent back (answers to ping())

    def send(self, obj):
        data = json.dumps(obj).encode()
        n = len(data)
        head = bytearray([0x81])
        if n < 126:
            head.append(n)
        elif n < 65536:
            head.append(126)
            head += struct.pack("!H", n)
        else:
            head.append(127)
            head += struct.pack("!Q", n)
        with self.lock:
            if self.closed:
                return
            try:
                self.sock.sendall(bytes(head) + data)
            except OSError:
                self.closed = True

    def _read(self, n):
        out = self.rfile.read(n)
        if out is None or len(out) < n:
            raise EOFError
        return out

    def recv(self):
        """Next text message (dict), or None when the client closed."""
        message = b""
        while True:
            try:
                b1, b2 = self._read(2)
                n = b2 & 0x7F
                if n == 126:
                    n = struct.unpack("!H", self._read(2))[0]
                elif n == 127:
                    n = struct.unpack("!Q", self._read(8))[0]
                mask = self._read(4) if b2 & 0x80 else b"\0\0\0\0"
                payload = bytes(b ^ mask[i % 4] for i, b in enumerate(self._read(n)))
            except (EOFError, OSError, ValueError):
                return None
            opcode = b1 & 0x0F
            if opcode == 0x8:
                return None
            if opcode == 0xA:
                self.pongs += 1
            if opcode in (0x9, 0xA):
                continue
            message += payload
            if b1 & 0x80:
                try:
                    return json.loads(message)
                except ValueError:
                    return {}

    def ping(self, payload=b"hb"):
        """A WebSocket ping frame, as the Supervisor's proxy sends after 30 s without traffic."""
        with self.lock:
            if not self.closed:
                self.sock.sendall(bytes([0x89, len(payload)]) + payload)

    def close(self):
        with self.lock:
            self.closed = True
        try:
            self.sock.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass


class FakeHABus(FakeHA):
    def __init__(self, token="test-token", time_zone="UTC"):
        super().__init__(time_zone=time_zone)
        self.token = token
        self.fired = []               # (event_type, data)
        self.drop = None              # callable(event_type, data) -> True to lose the event
        self._conns = []
        self._lock = threading.Lock()
        self.ws_url = None

    # -- event delivery ------------------------------------------------------
    def fire(self, event_type, data):
        """Fire an event as HA would (also usable directly from a test, like an automation)."""
        with self._lock:
            self.fired.append((event_type, data))
            conns = list(self._conns)
        if self.drop and self.drop(event_type, data):
            return
        event = {"event_type": event_type, "data": data, "origin": "REMOTE",
                 "time_fired": "2026-01-01T00:00:00+00:00", "context": {"id": "fake", "user_id": None}}
        for c in conns:
            for sid, etype in list(c.subs.items()):
                if etype in (None, event_type):
                    c.send({"id": sid, "type": "event", "event": event})

    def events(self, event_type="household_apps"):
        with self._lock:
            return [d for t, d in self.fired if t == event_type]

    def subscribers(self):
        with self._lock:
            return sum(len(c.subs) for c in self._conns if not c.closed)

    def ping_connections(self):
        """Send every open connection a WebSocket ping frame."""
        with self._lock:
            conns = list(self._conns)
        for c in conns:
            c.ping()

    def pongs(self):
        with self._lock:
            return sum(c.pongs for c in self._conns)

    def drop_connections(self):
        with self._lock:
            conns, self._conns = list(self._conns), []
        for c in conns:
            c.close()

    # -- the WebSocket session -------------------------------------------------
    def _ws_session(self, handler):
        key = handler.headers.get("Sec-WebSocket-Key", "")
        accept = base64.b64encode(hashlib.sha1((key + _WS_GUID).encode()).digest()).decode()
        handler.send_response(101, "Switching Protocols")
        handler.send_header("Upgrade", "websocket")
        handler.send_header("Connection", "Upgrade")
        handler.send_header("Sec-WebSocket-Accept", accept)
        handler.end_headers()
        handler.wfile.flush()
        conn = _Conn(handler.connection, handler.rfile)
        with self._lock:
            self._conns.append(conn)
        try:
            conn.send({"type": "auth_required", "ha_version": "2026.10.0"})
            while True:
                msg = conn.recv()
                if msg is None:
                    break
                mtype = msg.get("type")
                if not conn.authed:
                    if mtype == "auth" and msg.get("access_token") == self.token:
                        conn.authed = True
                        conn.send({"type": "auth_ok", "ha_version": "2026.10.0"})
                        continue
                    conn.send({"type": "auth_invalid", "message": "Invalid access token or password"})
                    break
                mid = msg.get("id")
                if mtype == "subscribe_events":
                    conn.subs[mid] = msg.get("event_type")
                    conn.send({"id": mid, "type": "result", "success": True, "result": None})
                elif mtype == "unsubscribe_events":
                    ok = conn.subs.pop(msg.get("subscription"), None) is not None
                    conn.send({"id": mid, "type": "result", "success": ok, "result": None} if ok else
                              {"id": mid, "type": "result", "success": False,
                               "error": {"code": "not_found", "message": "Subscription not found."}})
                elif mtype == "ping":
                    conn.send({"id": mid, "type": "pong"})
                else:
                    conn.send({"id": mid, "type": "result", "success": False,
                               "error": {"code": "unknown_command", "message": "Unknown command."}})
        finally:
            conn.close()
            with self._lock:
                if conn in self._conns:
                    self._conns.remove(conn)
            handler.close_connection = True

    # -- HTTP --------------------------------------------------------------------
    def _make_handler(outer):  # noqa: N805
        base = FakeHA._make_handler(outer)

        class H(base):
            def do_GET(self):
                if (self.path in ("/websocket", "/api/websocket")
                        and self.headers.get("Upgrade", "").lower() == "websocket"):
                    return outer._ws_session(self)
                return super().do_GET()

            def do_POST(self):
                if self.path.startswith("/api/events/"):
                    body = self._body()
                    auth = self.headers.get("Authorization")
                    outer.requests.append(("POST", self.path, body, auth))
                    if outer.fail:
                        return self._reply(500)
                    if auth != f"Bearer {outer.token}":
                        return self._reply(401, {"message": "Unauthorized"})
                    etype = self.path[len("/api/events/"):]
                    if body is not None and not isinstance(body, dict):
                        return self._reply(400, {"message": "Event data should be a JSON object"})
                    outer.fire(etype, body or {})
                    return self._reply(200, {"message": f"Event {etype} fired."})
                return super().do_POST()

        return H

    def start(self):
        self._server = ThreadingHTTPServer(("127.0.0.1", 0), self._make_handler())
        self._server.daemon_threads = True
        self._thread = threading.Thread(target=self._server.serve_forever, kwargs={"poll_interval": 0.05},
                                        daemon=True, name="fake-ha-bus")
        self._thread.start()
        port = self._server.server_port
        self.ws_url = f"ws://127.0.0.1:{port}/websocket"
        return f"http://127.0.0.1:{port}/api"

    def stop(self):
        self.drop_connections()
        super().stop()
