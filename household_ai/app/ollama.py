"""Talking to the model server (Ollama on 127.0.0.1:11435, never reachable from outside the container).

One shared httpx.AsyncClient; every call has its own timeout. The gateway (app/gateway.py) proxies the
answering routes; this module is the app's own calls: version, the downloaded models, what is loaded, a
model's details, loading/unloading (an empty generate with keep_alive), downloads and deletes.
"""
import json
import logging
import time

import httpx

from . import config, schedule

logger = logging.getLogger("ollama")

_client: httpx.AsyncClient | None = None


class OllamaError(Exception):
    pass


def client() -> httpx.AsyncClient:
    global _client
    if _client is None or _client.is_closed:
        # No proxy from the environment: the server is on loopback.
        _client = httpx.AsyncClient(base_url=config.OLLAMA_URL, trust_env=False,
                                    timeout=httpx.Timeout(30.0, connect=5.0),
                                    limits=httpx.Limits(max_connections=20, max_keepalive_connections=10))
    return _client


async def close() -> None:
    global _client
    if _client is not None:
        await _client.aclose()
        _client = None


def _error(r: httpx.Response) -> str:
    try:
        return str(r.json().get("error") or r.text)[:300]
    except ValueError:
        return r.text[:300] or f"HTTP {r.status_code}"


async def version(timeout: float = 5) -> str | None:
    try:
        r = await client().get("/api/version", timeout=timeout)
        return r.json().get("version") if r.status_code == 200 else None
    except (httpx.HTTPError, ValueError):
        return None


async def tags() -> list[dict]:
    """The downloaded models: [{name, size, modified_at, digest, details}]."""
    r = await client().get("/api/tags", timeout=10)
    if r.status_code != 200:
        raise OllamaError(_error(r))
    return r.json().get("models") or []


async def ps() -> list[dict]:
    """The loaded models: [{name, size, size_vram, expires_at}]."""
    r = await client().get("/api/ps", timeout=10)
    if r.status_code != 200:
        raise OllamaError(_error(r))
    return r.json().get("models") or []


async def show(name: str) -> dict:
    r = await client().post("/api/show", json={"model": name}, timeout=15)
    if r.status_code != 200:
        raise OllamaError(_error(r))
    return r.json()


async def set_keep_alive(name: str, keep_alive: int, timeout: float = 300) -> None:
    """An empty generate: loads the model if needed and (re)sets its timer; keep_alive 0 unloads it."""
    r = await client().post("/api/generate", json={"model": name, "keep_alive": keep_alive}, timeout=timeout)
    if r.status_code != 200:
        raise OllamaError(_error(r))


async def delete(name: str) -> None:
    r = await client().request("DELETE", "/api/delete", json={"model": name}, timeout=30)
    if r.status_code == 404:
        raise OllamaError(f"{name} isn't downloaded.")
    if r.status_code != 200:
        raise OllamaError(_error(r))


async def pull(name: str, progress) -> None:
    """Download `name`, calling progress(status, completed, total) per line of Ollama's stream. Raises
    OllamaError on an error line; cancelling the task stops the download."""
    async with client().stream("POST", "/api/pull", json={"model": name, "stream": True},
                               timeout=httpx.Timeout(None, connect=5.0)) as r:
        if r.status_code != 200:
            await r.aread()
            raise OllamaError(_error(r))
        async for line in r.aiter_lines():
            if not line.strip():
                continue
            try:
                msg = json.loads(line)
            except ValueError:
                continue
            if msg.get("error"):
                raise OllamaError(str(msg["error"])[:300])
            progress(msg.get("status") or "", msg.get("completed"), msg.get("total"))


# General models that also read images: kept loaded in the day like a text model, unlike a vision-only model (a
# "vl" model is only loaded for a receipt or a scan).
BOTH_FAMILIES = ("gemma3", "gemma4")


def kind_of(name: str, caps: list | None) -> str:
    """"text", "vision" or "both" (answers text and reads images) from Ollama's capabilities; by the name when
    Ollama couldn't say (caps None)."""
    base = name.rsplit("/", 1)[-1].split(":")[0].lower()
    vision = ("vl" in base or base.startswith(BOTH_FAMILIES)) if caps is None else "vision" in caps
    if not vision:
        return "text"
    return "both" if base.startswith(BOTH_FAMILIES) else "vision"


class Models:
    """The downloaded models, cached (the gateway checks every request's model against it)."""

    def __init__(self):
        self.names: set[str] = set()
        self.rows: list[dict] = []
        self.kinds: dict[str, str] = {}          # digest → "text" | "vision" | "both"
        self.loaded_at = 0.0
        self.tried_at = 0.0                      # time.monotonic() of the last refresh started

    async def refresh_if_older(self, seconds: float) -> list[dict]:
        """`refresh()`, unless one started less than `seconds` ago (then the list as it is): the gateway looks
        again for a model it doesn't know, but not on every request that names one."""
        if time.monotonic() - self.tried_at < seconds:
            return self.rows
        return await self.refresh()

    async def refresh(self) -> list[dict]:
        self.tried_at = time.monotonic()
        rows = await tags()
        for row in rows:
            digest = row.get("digest") or row.get("name")
            if digest not in self.kinds:
                try:
                    caps = (await show(row["name"])).get("capabilities") or []
                except (OllamaError, httpx.HTTPError):
                    caps = None
                self.kinds[digest] = kind_of(row["name"], caps)
            row["kind"] = self.kinds[digest]
        self.rows = sorted(rows, key=lambda r: r.get("modified_at") or "")
        self.names = {schedule.norm_model(r["name"]) for r in rows}
        self.loaded_at = time.monotonic()
        return self.rows

    def text_models(self) -> list[str]:
        """Downloaded models that answer text (text, or both text and images), oldest download first (the default
        kept model is the first)."""
        return [schedule.norm_model(r["name"]) for r in self.rows if r.get("kind") in ("text", "both")]

    def has(self, name: str) -> bool:
        return schedule.norm_model(name) in self.names

    def clear(self) -> None:
        self.names, self.rows = set(), []


MODELS = Models()
