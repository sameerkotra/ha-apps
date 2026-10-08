"""The gateway on port 11434: how other apps reach the model (SPEC.md §4, §6).

Ollama has no authentication and its API can download and delete models, so it is never exposed raw. This
small ASGI app, served on its own port next to the ingress page, passes on only the answering routes:

    GET /api/version, GET /api/tags, POST /api/show, POST /api/generate, POST /api/chat, POST /api/embed,
    GET /v1/models, POST /v1/chat/completions

Everything else is 404. Every request is checked before it is queued (body ≤ 32 MB; the model downloaded; the
caller's keep_alive replaced by the schedule's; num_ctx capped at *Context length*, num_thread set to *Threads*,
num_predict / max_tokens capped at *Longest answer*), then waits its turn (app/jobqueue.py), runs alone, and
is counted (tokens, seconds, wait). A caller that goes away while waiting leaves the queue; one that goes away
while its answer runs has the request to Ollama closed, which stops it. A run past *Longest run* is stopped
(504, or an error line on a stream).
"""
import asyncio
import json
import logging
import time

import anyio
import httpx
from starlette.applications import Starlette
from starlette.requests import ClientDisconnect, Request
from starlette.responses import JSONResponse, Response, StreamingResponse
from starlette.routing import Route

from . import callers, config, keys, machine, ollama, schedule, settings, usage
from .jobqueue import Full, RequestQueue

logger = logging.getLogger("gateway")

MAX_BODY = 32 * 1024 * 1024
MINUTE = 60                    # seconds in a minute of *Longest run* (tests shorten it)
ANSWERING = {"/api/generate", "/api/chat", "/api/embed", "/v1/chat/completions"}

QUEUE = RequestQueue(limit=lambda: settings.get("queue"))
LAST_USED: dict[str, float] = {}          # model → time.monotonic() of its last request (the schedule's)
STATS = {"day": None, "switches": 0, "last_model": None}


class Refused(Exception):
    def __init__(self, status: int, message: str, headers: dict | None = None):
        super().__init__(message)
        self.status, self.message, self.headers = status, message, headers or {}


def error(path: str, status: int, message: str, headers: dict | None = None) -> JSONResponse:
    """An error in the shape the caller's client expects (Ollama's {"error": "…"} or OpenAI's)."""
    if path.startswith("/v1/"):
        kind = {401: "authentication_error", 404: "not_found_error", 429: "rate_limit_error",
                503: "server_busy", 504: "timeout"}.get(status, "invalid_request_error")
        body = {"error": {"message": message, "type": kind}}
    else:
        body = {"error": message}
    return JSONResponse(body, status_code=status, headers=headers)


def server_ready() -> bool:
    from .server import SERVER    # (server imports nothing from here)
    return SERVER.state == "ready"


def not_ready_message() -> str:
    from .server import SERVER
    if not SERVER.status()["on"]:
        return "Household AI's model server is off — an admin can turn it on in Household AI."
    return SERVER.reason or "Household AI's model server is starting — try again shortly."


# -------------------------------------------------------------------------------------------- who is asking

async def identify(request: Request, path: str) -> tuple[str, dict | None]:
    """(caller name, key row or None). Raises Refused (401/429) when keys are on and the key is wrong."""
    address = request.client.host if request.client else None
    if not settings.get("require_key"):
        return await callers.RESOLVER.caller(address), None
    addr = address or "unknown"
    if keys.FAILURES.is_blocked(addr):
        raise Refused(429, "Too many wrong access keys — try again in a minute.", {"Retry-After": "60"})
    auth = request.headers.get("authorization", "")
    token = auth[7:].strip() if auth[:7].lower() == "bearer " else ""
    row = keys.check(token) if token else None
    if row is None:
        keys.FAILURES.fail(addr)
        raise Refused(401, "Household AI needs an access key: paste one from Household AI → Access keys into this "
                           "app's AI settings." if not token else "That access key isn't valid (revoked or mistyped).")
    return row["id"], row


# -------------------------------------------------------------------------------------------- the request

async def read_body(request: Request) -> bytes:
    size = int(request.headers.get("content-length") or 0)
    if size > MAX_BODY:
        raise Refused(413, "The request is larger than 32 MB.")
    chunks, total = [], 0
    async for chunk in request.stream():
        total += len(chunk)
        if total > MAX_BODY:
            raise Refused(413, "The request is larger than 32 MB.")
        chunks.append(chunk)
    return b"".join(chunks)


def threads() -> int:
    return int(settings.get("threads")) or machine.default_threads()


async def check_model(name, key: dict | None) -> str:
    if not isinstance(name, str) or not name.strip():
        raise Refused(400, "The request names no model.")
    model = schedule.norm_model(name)
    if not ollama.MODELS.has(model):
        try:
            await ollama.MODELS.refresh()
        except Exception:
            pass
        if not ollama.MODELS.has(model):
            raise Refused(404, f"Download {name} in Household AI first.")
    if key and key.get("models") and model not in key["models"]:
        raise Refused(403, f"This access key may not use {name}.")
    return model


def rewrite(path: str, body: dict, model: str) -> dict:
    """The checks and caps of SPEC.md §4, applied to the request Ollama gets."""
    values = settings.all()
    kept = schedule.kept_model(values, ollama.MODELS.text_models())
    cap = int(values["max_answer_tokens"])
    if path.startswith("/api/"):
        body["keep_alive"] = schedule.keep_alive(model, values, config.now(), kept)
        opts = body.get("options")
        opts = dict(opts) if isinstance(opts, dict) else {}
        opts["num_thread"] = threads()
        ctx = opts.get("num_ctx")
        if not isinstance(ctx, int) or isinstance(ctx, bool) or ctx > values["context"] or ctx <= 0:
            opts["num_ctx"] = int(values["context"])
        if path != "/api/embed":
            n = opts.get("num_predict")
            if not isinstance(n, int) or isinstance(n, bool) or n < 0 or n > cap:
                opts["num_predict"] = cap
        body["options"] = opts
    else:                                   # /v1/chat/completions
        body.pop("keep_alive", None)
        for k in ("max_tokens", "max_completion_tokens"):
            n = body.get(k)
            if k in body and (not isinstance(n, int) or isinstance(n, bool) or n <= 0 or n > cap):
                body[k] = cap
        if "max_tokens" not in body and "max_completion_tokens" not in body:
            body["max_tokens"] = cap
    return body


def wants_stream(path: str, body: dict) -> bool:
    if path in ("/api/generate", "/api/chat"):
        return body.get("stream", True) is not False       # Ollama streams unless told not to
    if path == "/v1/chat/completions":
        return body.get("stream") is True
    return False


class Counter:
    """Tokens from what Ollama sends back, line by line (streams) or whole."""

    def __init__(self, path: str):
        self.path = path
        self.input = self.output = 0
        self.buf = b""

    def whole(self, data: bytes) -> None:
        try:
            msg = json.loads(data)
        except ValueError:
            return
        self._take(msg)

    def _take(self, msg) -> None:
        if not isinstance(msg, dict):
            return
        if self.path.startswith("/v1/"):
            u = msg.get("usage")
            if isinstance(u, dict):
                self.input = int(u.get("prompt_tokens") or 0)
                self.output = int(u.get("completion_tokens") or 0)
        else:
            if "prompt_eval_count" in msg:
                self.input = int(msg.get("prompt_eval_count") or 0)
            if "eval_count" in msg:
                self.output = int(msg.get("eval_count") or 0)

    def line(self, line: bytes) -> bool:
        """Count one line of a stream; False when the gateway should drop it (the usage chunk it asked for)."""
        text = line.strip()
        if self.path.startswith("/v1/"):
            if not text.startswith(b"data:"):
                return True
            text = text[5:].strip()
            if text == b"[DONE]":
                return True
        try:
            msg = json.loads(text)
        except ValueError:
            return True
        self._take(msg)
        return True

    def is_usage_only(self, line: bytes) -> bool:
        text = line.strip()
        if not text.startswith(b"data:"):
            return False
        try:
            msg = json.loads(text[5:].strip())
        except ValueError:
            return False
        return isinstance(msg, dict) and msg.get("usage") is not None and not msg.get("choices")


async def wait_for_disconnect(request: Request) -> None:
    """Returns when the caller closes the connection (the body has already been read)."""
    while True:
        message = await request.receive()
        if message["type"] == "http.disconnect":
            return


def note_model(model: str) -> None:
    day = config.today().isoformat()
    if STATS["day"] != day:
        STATS.update(day=day, switches=0)
    if STATS["last_model"] and STATS["last_model"] != model and settings.get("max_loaded") == 1:
        STATS["switches"] += 1
    STATS["last_model"] = model


async def after_answer(path: str, model: str, caller: str, address: str | None, counter: Counter,
                       ran: float, waited: float) -> None:
    LAST_USED[model] = time.monotonic()
    if path == "/v1/chat/completions":
        # /v1 requests carry no keep_alive: reset the model's timer to the schedule's (SPEC.md §6.2).
        values = settings.all()
        ka = schedule.keep_alive(model, values, config.now(), schedule.kept_model(values, ollama.MODELS.text_models()))
        try:
            await ollama.set_keep_alive(model, ka, timeout=30)
        except Exception as e:
            logger.debug("keep_alive after /v1: %s", e)
    try:
        await asyncio.to_thread(usage.record, caller, model, address, counter.input, counter.output, ran, waited)
    except Exception:
        logger.exception("recording usage")


async def proxy(request: Request) -> Response:
    path = request.url.path
    method = request.method
    address = request.client.host if request.client else None
    if path == "/api/version":
        # Answered without a key (the Supervisor's watchdog asks it), even while the model server is off.
        from .server import SERVER
        v = await ollama.version() if server_ready() else None
        return JSONResponse({"version": v or SERVER.version or "0.0.0"})
    try:
        caller, key = await identify(request, path)
        if not server_ready():
            raise Refused(503, not_ready_message(), {"Retry-After": "30"})
        if method == "GET" and path in ("/api/tags", "/v1/models"):
            return await list_models(path, key)
        raw = await read_body(request)
        try:
            body = json.loads(raw or b"{}")
        except ValueError:
            raise Refused(400, "The request body isn't JSON.")
        if not isinstance(body, dict):
            raise Refused(400, "The request body must be a JSON object.")
        model = await check_model(body.get("model") or body.get("name"), key)
        if path == "/api/show":
            body = {"model": body.get("model") or body.get("name"), "verbose": False}
            r = await ollama.client().post("/api/show", json=body, timeout=30)
            return Response(r.content, status_code=r.status_code, media_type="application/json")
        body = rewrite(path, body, model)
    except Refused as e:
        return error(path, e.status, e.message, e.headers)
    except ClientDisconnect:
        return Response(status_code=499)

    # Waiting for our turn: until granted, or the caller goes away.
    from .server import SCHEDULE
    if SCHEDULE is not None:
        SCHEDULE.manual_unload = False
    first = caller in (settings.get("answer_first") or [])
    try:
        ticket = QUEUE.enter(caller, model, first)
    except Full as e:
        return error(path, 503, str(e), {"Retry-After": str(e.retry_after)})
    gone = asyncio.create_task(wait_for_disconnect(request))
    granted = asyncio.create_task(QUEUE.wait(ticket))
    done, _ = await asyncio.wait({gone, granted}, return_when=asyncio.FIRST_COMPLETED)
    if granted not in done:
        granted.cancel()
        QUEUE.leave(ticket)
        return Response(status_code=499)
    waited = ticket.waited(QUEUE.clock())
    note_model(model)

    stream = wants_stream(path, body)
    asked_usage = False
    if stream and path == "/v1/chat/completions":
        so = body.get("stream_options")
        asked_usage = isinstance(so, dict) and so.get("include_usage") is True
        body["stream_options"] = {**(so if isinstance(so, dict) else {}), "include_usage": True}
    limit = int(settings.get("max_run_min")) * MINUTE
    counter = Counter(path)
    started = time.monotonic()
    client = ollama.client()

    if not stream:
        gone_task = gone
        call = asyncio.create_task(client.post(path, json=body, timeout=httpx.Timeout(limit + 5, connect=5)))
        try:
            done, _ = await asyncio.wait({call, gone_task}, timeout=limit, return_when=asyncio.FIRST_COMPLETED)
            if call not in done:
                call.cancel()
                try:
                    await call
                except BaseException:
                    pass
                if gone_task in done:
                    logger.info("%s went away; stopped its %s request", callers.label(caller), model)
                    return Response(status_code=499)
                return error(path, 504, f"Stopped after the longest run ({settings.get('max_run_min')} min).")
            try:
                r = call.result()
            except httpx.HTTPError as e:
                return error(path, 502, f"The model server didn't answer: {e}")
            if r.status_code == 200:
                counter.whole(r.content)
            return Response(r.content, status_code=r.status_code,
                            media_type=r.headers.get("content-type", "application/json"))
        finally:
            gone_task.cancel()
            ran = time.monotonic() - started
            QUEUE.leave(ticket)
            await after_answer(path, model, caller, address, counter, ran, waited)

    # Streaming: Starlette stops the generator when the caller goes away; closing the upstream stops Ollama.
    gone.cancel()
    try:
        upstream = await client.send(client.build_request("POST", path, json=body,
                                                          timeout=httpx.Timeout(limit + 5, connect=5)), stream=True)
    except httpx.HTTPError as e:
        QUEUE.leave(ticket)
        return error(path, 502, f"The model server didn't answer: {e}")
    if upstream.status_code != 200:
        content = await upstream.aread()
        await upstream.aclose()
        QUEUE.leave(ticket)
        return Response(content, status_code=upstream.status_code, media_type="application/json")

    finished = False

    async def cleanup():
        nonlocal finished
        if finished:
            return
        finished = True
        QUEUE.leave(ticket)
        with anyio.CancelScope(shield=True):
            await upstream.aclose()
            await after_answer(path, model, caller, address, counter, time.monotonic() - started, waited)

    async def lines():
        buf = b""
        try:
            async with asyncio.timeout(limit):
                async for chunk in upstream.aiter_raw():
                    buf += chunk
                    while b"\n" in buf:
                        line, buf = buf.split(b"\n", 1)
                        counter.line(line)
                        if not asked_usage and path.startswith("/v1/") and counter.is_usage_only(line):
                            continue
                        yield line + b"\n"
            if buf:
                counter.line(buf)
                yield buf
        except TimeoutError:
            msg = f"Stopped after the longest run ({settings.get('max_run_min')} min)."
            yield ((b"data: " + json.dumps({"error": {"message": msg}}).encode() + b"\n\n")
                   if path.startswith("/v1/") else json.dumps({"error": msg}).encode() + b"\n")
        finally:
            await cleanup()

    media = upstream.headers.get("content-type", "application/x-ndjson")
    return Stream(lines(), cleanup, media_type=media)


class Stream(StreamingResponse):
    """A streamed answer that always cleans up (closes the request to Ollama, frees the queue, counts the
    usage) when the response ends — finished, failed, or the caller gone."""

    def __init__(self, content, cleanup, **kw):
        super().__init__(content, **kw)
        self.cleanup = cleanup

    async def __call__(self, scope, receive, send):
        try:
            await super().__call__(scope, receive, send)
        finally:
            with anyio.CancelScope(shield=True):
                await self.cleanup()


async def list_models(path: str, key: dict | None) -> Response:
    """The downloaded models (only those a key may use)."""
    allowed = set(key["models"]) if key and key.get("models") else None
    r = await ollama.client().get(path, timeout=15)
    if r.status_code != 200 or allowed is None:
        return Response(r.content, status_code=r.status_code, media_type="application/json")
    data = r.json()
    if path == "/api/tags":
        data["models"] = [m for m in data.get("models") or [] if schedule.norm_model(m.get("name", "")) in allowed]
    else:
        data["data"] = [m for m in data.get("data") or [] if schedule.norm_model(m.get("id", "")) in allowed]
    return JSONResponse(data)


async def not_found(request: Request) -> Response:
    return error(request.url.path, 404, "Not available through Household AI (model management is on its page).")


ROUTES = [
    Route("/api/version", proxy, methods=["GET"]),
    Route("/api/tags", proxy, methods=["GET"]),
    Route("/api/show", proxy, methods=["POST"]),
    Route("/api/generate", proxy, methods=["POST"]),
    Route("/api/chat", proxy, methods=["POST"]),
    Route("/api/embed", proxy, methods=["POST"]),
    Route("/v1/models", proxy, methods=["GET"]),
    Route("/v1/chat/completions", proxy, methods=["POST"]),
    Route("/{rest:path}", not_found, methods=["GET", "POST", "PUT", "DELETE", "HEAD", "PATCH", "OPTIONS"]),
]

app = Starlette(routes=ROUTES)


async def serve(port: int, host: str = "0.0.0.0"):
    """The gateway's uvicorn server (proxy headers off: callers are told apart by their real address)."""
    import uvicorn
    cfg = uvicorn.Config(app, host=host, port=port, proxy_headers=False, log_level="warning",
                         timeout_keep_alive=30, lifespan="off")
    server = uvicorn.Server(cfg)
    server.install_signal_handlers = lambda: None
    return server
