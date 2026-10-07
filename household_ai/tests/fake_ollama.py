#!/usr/bin/env python3
"""A tiny stand-in for `ollama serve` (standard library only): the few routes Household AI and its callers use.

    OLLAMA_HOST=127.0.0.1:<port> OLLAMA_MODELS=<dir> FAKE_OLLAMA_LOG=<file> python3 fake_ollama.py serve

- Models live in <OLLAMA_MODELS>/fake_models.json; /api/pull adds one ("missing:…" fails), /api/delete removes.
- A prompt or last message "sleep:N" takes N seconds; the fake notices when the caller closes the connection
  meanwhile and logs it as {"cancelled": path}.
- Every request is logged (one JSON line: path, body, environment at start-up on the first line).
"""
import json
import os
import select
import socket
import sys
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

HOST, _, PORT = os.environ.get("OLLAMA_HOST", "127.0.0.1:11435").rpartition(":")
MODELS_DIR = os.environ.get("OLLAMA_MODELS", ".")
STORE = os.path.join(MODELS_DIR, "fake_models.json")
LOG = os.environ.get("FAKE_OLLAMA_LOG")
LOADED: dict = {}


def log(entry):
    if LOG:
        with open(LOG, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry) + "\n")


def models():
    try:
        with open(STORE, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return []


def save(ms):
    os.makedirs(MODELS_DIR, exist_ok=True)
    with open(STORE, "w", encoding="utf-8") as f:
        json.dump(ms, f)


def pull_step() -> float:
    """Seconds per download step: <OLLAMA_MODELS>/fake_pull_step when the test wrote one, else 0.05."""
    try:
        with open(os.path.join(MODELS_DIR, "fake_pull_step"), encoding="utf-8") as f:
            return float(f.read())
    except (OSError, ValueError):
        return 0.05


def full(name):
    last = name.rsplit("/", 1)[-1]
    return name if ":" in last else name + ":latest"


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *a):
        pass

    def body(self):
        n = int(self.headers.get("content-length") or 0)
        raw = self.rfile.read(n) if n else b""
        try:
            return json.loads(raw or b"{}")
        except ValueError:
            return {}

    def send_json(self, obj, status=200):
        data = json.dumps(obj).encode()
        self.send_response(status)
        self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def client_gone(self) -> bool:
        r, _, _ = select.select([self.connection], [], [], 0)
        if not r:
            return False
        try:
            return self.connection.recv(1, socket.MSG_PEEK) == b""
        except OSError:
            return True

    def sleep(self, seconds) -> bool:
        """False when the caller went away meanwhile."""
        end = time.monotonic() + seconds
        while time.monotonic() < end:
            if self.client_gone():
                log({"cancelled": self.path})
                return False
            time.sleep(0.05)
        return True

    def do_GET(self):
        log({"path": self.path, "method": "GET"})
        if self.path == "/api/version":
            return self.send_json({"version": "0.40.0"})
        if self.path == "/api/tags":
            return self.send_json({"models": models()})
        if self.path == "/api/ps":
            return self.send_json({"models": [{"name": n, "model": n, "size": 1000, "expires_at": e}
                                              for n, e in LOADED.items()]})
        if self.path == "/v1/models":
            return self.send_json({"object": "list", "data": [{"id": m["name"], "object": "model"} for m in models()]})
        self.send_json({"error": "not found"}, 404)

    def do_DELETE(self):
        b = self.body()
        log({"path": self.path, "method": "DELETE", "body": b})
        if self.path == "/api/delete":
            ms = models()
            keep = [m for m in ms if m["name"] != full(b.get("model", ""))]
            if len(keep) == len(ms):
                return self.send_json({"error": "model not found"}, 404)
            save(keep)
            return self.send_json({})
        self.send_json({"error": "not found"}, 404)

    def do_POST(self):
        b = self.body()
        log({"path": self.path, "method": "POST", "body": b})
        p = self.path
        if p == "/api/show":
            name = full(b.get("model", ""))
            if name not in {m["name"] for m in models()}:
                return self.send_json({"error": "not found"}, 404)
            caps = ["completion"] + (["vision"] if "vl" in name else [])
            return self.send_json({"capabilities": caps, "details": {}})
        if p == "/api/pull":
            return self.pull(b)
        if p in ("/api/generate", "/api/chat"):
            return self.generate(p, b)
        if p == "/api/embed":
            return self.send_json({"model": b.get("model"), "embeddings": [[0.1, 0.2]], "prompt_eval_count": 2})
        if p == "/v1/chat/completions":
            return self.openai(b)
        self.send_json({"error": "not found"}, 404)

    def pull(self, b):
        name = full(b.get("model", ""))
        self.send_response(200)
        self.send_header("content-type", "application/x-ndjson")
        self.send_header("transfer-encoding", "chunked")
        self.end_headers()

        def line(obj):
            data = (json.dumps(obj) + "\n").encode()
            self.wfile.write(f"{len(data):x}\r\n".encode() + data + b"\r\n")
            self.wfile.flush()
        try:
            if name.startswith("missing"):
                line({"error": "pull model manifest: file does not exist"})
            else:
                for done in (0, 50, 100):
                    line({"status": "pulling abc", "completed": done, "total": 100})
                    if not self.sleep(pull_step()):
                        return
                ms = [m for m in models() if m["name"] != name]
                ms.append({"name": name, "model": name, "size": 100, "digest": "d-" + name,
                           "modified_at": time.strftime("%Y-%m-%dT%H:%M:%S") + f".{len(ms):06d}Z", "details": {}})
                save(ms)
                line({"status": "success"})
            self.wfile.write(b"0\r\n\r\n")
        except OSError:
            log({"cancelled": "/api/pull"})

    def text_of(self, p, b):
        if p == "/api/generate":
            return b.get("prompt")
        msgs = b.get("messages") or []
        return msgs[-1].get("content") if msgs else None

    def generate(self, p, b):
        name = full(b.get("model", ""))
        text = self.text_of(p, b)
        ka = b.get("keep_alive")
        if not text:                                   # load / unload
            if ka == 0:
                LOADED.pop(name, None)
            else:
                LOADED[name] = ka
            return self.send_json({"model": name, "done": True, "done_reason": "unload" if ka == 0 else "load"})
        LOADED[name] = ka
        if isinstance(text, str) and text.startswith("sleep:"):
            if not self.sleep(float(text[6:])):
                return
        key = "response" if p == "/api/generate" else "message"
        val = "ok" if p == "/api/generate" else {"role": "assistant", "content": "ok"}
        if b.get("stream", True) is False:
            return self.send_json({"model": name, key: val, "done": True, "prompt_eval_count": 5, "eval_count": 3})
        self.send_response(200)
        self.send_header("content-type", "application/x-ndjson")
        self.send_header("transfer-encoding", "chunked")
        self.end_headers()
        try:
            for i in range(3):
                piece = {"model": name, key: ("o" if p == "/api/generate" else {"role": "assistant", "content": "o"}),
                         "done": False}
                data = (json.dumps(piece) + "\n").encode()
                self.wfile.write(f"{len(data):x}\r\n".encode() + data + b"\r\n")
                self.wfile.flush()
                time.sleep(float(os.environ.get("FAKE_STREAM_STEP", "0.02")))
            last = (json.dumps({"model": name, "done": True, "prompt_eval_count": 5, "eval_count": 3}) + "\n").encode()
            self.wfile.write(f"{len(last):x}\r\n".encode() + last + b"\r\n0\r\n\r\n")
        except OSError:
            log({"cancelled": p})

    def openai(self, b):
        name = b.get("model")
        msgs = b.get("messages") or []
        text = msgs[-1].get("content") if msgs else ""
        if isinstance(text, str) and text.startswith("sleep:"):
            if not self.sleep(float(text[6:])):
                return
        usage = {"prompt_tokens": 7, "completion_tokens": 4, "total_tokens": 11}
        if not b.get("stream"):
            return self.send_json({"id": "c1", "object": "chat.completion", "model": name,
                                   "choices": [{"index": 0, "message": {"role": "assistant", "content": "ok"},
                                                "finish_reason": "stop"}], "usage": usage})
        self.send_response(200)
        self.send_header("content-type", "text/event-stream")
        self.send_header("transfer-encoding", "chunked")
        self.end_headers()

        def event(obj):
            data = b"data: " + (obj if isinstance(obj, bytes) else json.dumps(obj).encode()) + b"\n\n"
            self.wfile.write(f"{len(data):x}\r\n".encode() + data + b"\r\n")
            self.wfile.flush()
        event({"id": "c1", "object": "chat.completion.chunk", "choices": [{"index": 0, "delta": {"content": "ok"}}]})
        if (b.get("stream_options") or {}).get("include_usage"):
            event({"id": "c1", "object": "chat.completion.chunk", "choices": [], "usage": usage})
        event(b"[DONE]")
        self.wfile.write(b"0\r\n\r\n")


def main():
    if sys.argv[1:2] != ["serve"]:
        print("usage: fake_ollama.py serve", file=sys.stderr)
        return 2
    log({"env": {k: v for k, v in os.environ.items() if k.startswith("OLLAMA_")}})
    print(f"fake ollama listening on {HOST}:{PORT}", flush=True)
    ThreadingHTTPServer.daemon_threads = True
    ThreadingHTTPServer((HOST, int(PORT)), Handler).serve_forever()
    return 0


if __name__ == "__main__":
    sys.exit(main())
