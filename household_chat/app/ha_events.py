"""Listen for the phone's notification buttons (SPEC §15.1).

One WebSocket to Home Assistant (`ws://supervisor/core/websocket`, the
Supervisor token; covered by `homeassistant_api`), subscribed to
`mobile_app_notification_action`. Actions without the HCHAT_ prefix are
ignored. A small stdlib client (text frames only) in a daemon thread,
reconnecting with back-off.
"""
import base64
import json
import logging
import os
import socket
import struct
import threading
import time
from urllib.parse import urlparse

from . import config

logger = logging.getLogger("ha_events")
_stop = threading.Event()
_thread: threading.Thread | None = None


class WSClosed(Exception):
    pass


class MiniWS:
    def __init__(self, url: str, timeout: float = 30):
        u = urlparse(url)
        if u.scheme != "ws":
            raise ValueError("Only ws:// is supported")
        self.sock = socket.create_connection((u.hostname, u.port or 80), timeout=timeout)
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

    def send(self, obj: dict) -> None:
        data = json.dumps(obj).encode()
        head = bytearray([0x81])
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
        self.sock.sendall(bytes(head) + bytes(b ^ mask[i % 4] for i, b in enumerate(data)))

    def _send_control(self, opcode: int, payload: bytes = b"") -> None:
        mask = os.urandom(4)
        self.sock.sendall(bytes([0x80 | opcode, 0x80 | len(payload)]) + mask +
                          bytes(b ^ mask[i % 4] for i, b in enumerate(payload)))

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
                self._send_control(0xA, payload)
                continue
            if opcode == 0xA:
                continue
            message += payload
            if fin:
                return json.loads(message.decode("utf-8"))

    def close(self) -> None:
        try:
            self.sock.close()
        except OSError:
            pass


def _run() -> None:
    from . import notifier
    delay = 5
    while not _stop.is_set():
        ws = None
        try:
            ws = MiniWS(config.SUPERVISOR_CORE_WS)
            ws.sock.settimeout(90)
            first = ws.recv()
            if first.get("type") != "auth_required":
                raise WSClosed("unexpected greeting")
            ws.send({"type": "auth", "access_token": config.SUPERVISOR_TOKEN})
            if ws.recv().get("type") != "auth_ok":
                raise WSClosed("auth refused")
            ws.send({"id": 1, "type": "subscribe_events", "event_type": "mobile_app_notification_action"})
            logger.info("Listening for notification replies from Home Assistant.")
            delay = 5
            last_ping = time.monotonic()
            while not _stop.is_set():
                try:
                    msg = ws.recv()
                except socket.timeout:
                    msg = None
                if time.monotonic() - last_ping > 50:
                    ws.send({"id": int(time.time()), "type": "ping"})
                    last_ping = time.monotonic()
                if not msg or msg.get("type") != "event":
                    continue
                data = (msg.get("event") or {}).get("data") or {}
                action = data.get("action")
                if isinstance(action, str) and action.startswith("HCHAT_"):
                    try:
                        result = notifier.handle_action(action, data.get("reply_text"))
                        logger.info("Notification action: %s", result)
                    except Exception:
                        logger.exception("Handling a notification action failed")
        except Exception as e:
            if not _stop.is_set():
                logger.info("Home Assistant event connection: %s — retrying in %s s", type(e).__name__, delay)
        finally:
            if ws:
                ws.close()
        _stop.wait(delay)
        delay = min(delay * 2, 300)


def start() -> None:
    global _thread
    if not config.SUPERVISOR_TOKEN or (_thread and _thread.is_alive()):
        return
    _stop.clear()
    _thread = threading.Thread(target=_run, daemon=True, name="ha-events")
    _thread.start()


def stop() -> None:
    _stop.set()
