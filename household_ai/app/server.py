"""The model server: `ollama serve` as a child process (SPEC.md §3), and the day/night schedule (§6.2).

The app starts it when *Model server* is on: `nice -n 10 ollama serve` (Home Assistant stays responsive while a
model works), on 127.0.0.1:11435 only, models in /data/models. It is watched (/api/version every 30 s),
restarted when it exits (5 s, doubling to 5 min; back to 5 s once it has run 10 minutes) and stopped with
SIGTERM on shutdown (10 s, then SIGKILL). Killed by the kernel for lack of memory (SIGKILL we didn't send), it
says so. A restart the admin causes (a server setting, a restore) takes its turn in the gateway's queue
(`restart_soon`), so it never cuts off an answer.
"""
import asyncio
import collections
import contextlib
import logging
import os
import shlex
import shutil
import signal
import time

from . import config, machine, ollama, schedule, settings

logger = logging.getLogger("server")

LOG_LINES = 200
READY_TIMEOUT = 120
WATCH_SECONDS = 30
BACKOFF_FIRST, BACKOFF_MAX, STABLE_AFTER = 5.0, 300.0, 600.0
OOM_MESSAGE = ("The model server was stopped for lack of memory — choose a smaller model, unload sooner, or "
               "keep one model loaded at a time.")


LLAMA_SERVER = os.environ.get("OLLAMA_LLAMA_SERVER", "/usr/lib/ollama/llama-server")


def image_problem() -> str | None:
    """A model can't run without Ollama's llama-server next to it (Ollama 0.40 and later): say so up front, rather
    than as a 500 on the first request. Only checked for the real `ollama` binary (tests use a stand-in)."""
    if config.OLLAMA_BIN != "ollama" or os.path.exists(LLAMA_SERVER):
        return None
    return (f"This app's image has no {LLAMA_SERVER}, so no model can run — rebuild or update the app. "
            "(Ollama starts, but every answer fails.)")


def command() -> list[str]:
    cmd = shlex.split(config.OLLAMA_BIN) + ["serve"]
    nice = shutil.which("nice")
    return [nice, "-n", "10", *cmd] if nice else cmd


# Secrets in the app's environment that the model server never needs: Home Assistant's token (under both of the
# Supervisor's names) and, on a Docker host, the settings page's password.
SECRET_ENV = ("SUPERVISOR_TOKEN", "HASSIO_TOKEN", "ADMIN_PASSWORD")


def environment(values: dict) -> dict:
    env = {k: v for k, v in os.environ.items() if k not in SECRET_ENV}
    env.update({
        "OLLAMA_HOST": f"127.0.0.1:{config.OLLAMA_PORT}",
        "OLLAMA_MODELS": config.MODELS_DIR,
        "OLLAMA_MAX_LOADED_MODELS": str(values["max_loaded"]),
        "OLLAMA_NUM_PARALLEL": "1",
        "OLLAMA_KEEP_ALIVE": f"{int(values['night_unload_min'])}m",
        "OLLAMA_CONTEXT_LENGTH": str(values["context"]),
        "OLLAMA_NO_CLOUD": "1",
        "OLLAMA_NOHISTORY": "1",
    })
    return env


class ModelServer:
    def __init__(self):
        self.state = "stopped"                 # stopped | starting | ready
        self.reason = "Off"
        self.version: str | None = None
        self.log: collections.deque[str] = collections.deque(maxlen=LOG_LINES)
        self.proc: asyncio.subprocess.Process | None = None
        self.started_at: float | None = None
        self._task: asyncio.Task | None = None
        self._wanted = False
        self._stopping = False
        self.on_ready = []                     # async callbacks after each (re)start
        # The keep_alive (seconds) the running server was started with (OLLAMA_KEEP_ALIVE): what a request without
        # one gets. None until it has started.
        self.default_keep_alive: int | None = None
        self._restart: asyncio.Task | None = None
        self._restart_began = False

    # ------------------------------------------------------------------ control
    def running(self) -> bool:
        return self._task is not None and not self._task.done()

    async def start(self) -> None:
        self._wanted = True
        if not self.running():
            self.state, self.reason = "starting", ""
            self._task = asyncio.create_task(self._run(), name="ollama")

    async def stop(self, reason: str = "Off") -> None:
        self._wanted = False
        task, self._task = self._task, None
        await self._terminate()
        if task is not None:
            task.cancel()
            try:
                await task
            except (asyncio.CancelledError, Exception):
                pass
        self.state, self.reason, self.started_at = "stopped", reason, None
        ollama.MODELS.clear()

    async def restart(self) -> None:
        await self.stop("Restarting")
        await self.start()

    def restart_soon(self, queue) -> asyncio.Task:
        """Restart in turn: after the request running now, ahead of the waiting ones (`turn`). A setting changed
        again before the restart begins is picked up by the same restart; one changed while it runs gets another."""
        if self._restart is not None and not self._restart.done() and not self._restart_began:
            return self._restart
        previous = self._restart
        self._restart_began = False
        self._restart = asyncio.create_task(self._restart_in_turn(queue, previous), name="ollama-restart")
        return self._restart

    async def _restart_in_turn(self, queue, previous) -> None:
        if previous is not None and not previous.done():
            with contextlib.suppress(Exception):
                await previous
        async with turn(queue, "household_ai:restart"):
            if not self._wanted:                         # turned off meanwhile
                return
            self._restart_began = True
            await self.restart()
            await self.wait_ready(READY_TIMEOUT)

    async def wait_ready(self, timeout: float) -> bool:
        end = time.monotonic() + timeout
        while self.state != "ready" and self._wanted and time.monotonic() < end:
            await asyncio.sleep(0.1)
        return self.state == "ready"

    async def cancel_restart(self) -> None:
        """At shutdown: a restart still waiting for its turn is dropped."""
        task, self._restart = self._restart, None
        if task is not None and not task.done():
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await task

    # ------------------------------------------------------------------ the child process
    async def _spawn(self) -> None:
        os.makedirs(config.MODELS_DIR, exist_ok=True)
        cmd = command()
        self.log.append(f"[household_ai] starting: {' '.join(cmd)}")
        values = settings.all()
        self.default_keep_alive = int(values["night_unload_min"]) * 60
        self.proc = await asyncio.create_subprocess_exec(
            *cmd, env=environment(values), stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT, start_new_session=True)
        self.started_at = time.monotonic()
        asyncio.create_task(self._read_output(self.proc), name="ollama-log")

    async def _read_output(self, proc) -> None:
        try:
            while True:
                line = await proc.stdout.readline()
                if not line:
                    return
                self.log.append(line.decode("utf-8", "replace").rstrip()[:1000])
        except Exception:     # the pipe closed with the process
            return

    async def _terminate(self) -> None:
        proc = self.proc
        if proc is None or proc.returncode is not None:
            return
        self._stopping = True
        try:
            proc.send_signal(signal.SIGTERM)
            try:
                await asyncio.wait_for(proc.wait(), 10)
            except asyncio.TimeoutError:
                proc.kill()
                await proc.wait()
        except ProcessLookupError:
            pass
        finally:
            self._stopping = False

    async def _run(self) -> None:
        backoff = BACKOFF_FIRST
        while self._wanted:
            try:
                await self._spawn()
            except OSError as e:
                self.state, self.reason = "stopped", f"Couldn't start the model server: {e}"
                logger.error(self.reason)
                await asyncio.sleep(backoff)
                backoff = min(BACKOFF_MAX, backoff * 2)
                continue
            self.state, self.reason = "starting", ""
            code = await self._watch(self.proc)
            if not self._wanted:
                return
            ran = time.monotonic() - (self.started_at or time.monotonic())
            if code == -signal.SIGKILL:
                self.reason = OOM_MESSAGE
            else:
                self.reason = f"The model server stopped (exit code {code}); restarting in {int(backoff)} s."
            self.state = "stopped"
            ollama.MODELS.clear()
            logger.warning(self.reason)
            self.log.append(f"[household_ai] {self.reason}")
            if ran >= STABLE_AFTER:
                backoff = BACKOFF_FIRST
            await asyncio.sleep(backoff)
            backoff = min(BACKOFF_MAX, backoff * 2)

    async def _watch(self, proc) -> int:
        """Until the process exits (its return code): ready once /api/version answers, then checked every 30 s."""
        deadline = time.monotonic() + READY_TIMEOUT
        exit_wait = asyncio.create_task(proc.wait())
        try:
            while not exit_wait.done():
                v = await ollama.version(timeout=3)
                if v:
                    if self.state != "ready":
                        self.state, self.reason, self.version = "ready", "", v
                        logger.info("Model server %s ready", v)
                        await self._ready()
                    delay = WATCH_SECONDS
                else:
                    if self.state == "ready":
                        self.state, self.reason = "starting", "Not answering"
                    elif time.monotonic() > deadline:
                        self.reason = "Still starting (slow disk?)"
                    delay = 1
                await asyncio.wait({exit_wait}, timeout=delay)
            return exit_wait.result()
        finally:
            if not exit_wait.done():
                exit_wait.cancel()

    async def _ready(self) -> None:
        problem = image_problem()
        if problem:
            logger.error(problem)
            self.log.append(f"[household_ai] {problem}")
        try:
            await ollama.MODELS.refresh()
        except Exception as e:
            logger.warning("Listing the models failed: %s", e)
        for cb in self.on_ready:
            try:
                await cb()
            except Exception:
                logger.exception("after start-up")

    def status(self) -> dict:
        up = None if self.started_at is None else int(time.monotonic() - self.started_at)
        return {"state": self.state, "reason": self.reason, "version": self.version, "uptime": up,
                "on": self._wanted, "problem": image_problem() if self._wanted else None}


SERVER = ModelServer()


@contextlib.asynccontextmanager
async def turn(queue, caller: str):
    """Hold the gateway's queue while the app does something an answer must not overlap (restarting the model
    server, deleting a model): waits for the running request to finish, goes ahead of the waiting ones, and is
    never refused for a full queue."""
    ticket = queue.enter(caller, caller, first=True, force=True)
    try:
        await queue.wait(ticket)
        yield
    finally:
        queue.leave(ticket)


# ---------------------------------------------------------------------------------------------- the schedule

class Schedule:
    """Every minute (and at start-up): at the daytime window's start, load the kept model (with *Load at the start
    of the day*); at its end, shorten every loaded model to what's left of the night rule; in the daytime, load
    the kept model back once nothing else is loaded and nothing is waiting (after a restart, or after a vision
    model unloaded)."""

    def __init__(self, queue, last_used: dict, clock=None):
        self.queue = queue
        self.last_used = last_used               # model → time.monotonic() of its last request
        self.clock = clock or config.now
        self.was_day: bool | None = None
        self.manual_unload = False               # "Unload now": nothing loads again until a request or day start
        self.loading = False

    def is_day(self, values: dict | None = None) -> bool:
        values = values or settings.all()
        return schedule.in_daytime(self.clock(), values["day_start"], values["day_end"])

    def kept(self, values: dict | None = None) -> str | None:
        return schedule.kept_model(values or settings.all(), ollama.MODELS.text_models())

    async def tick(self) -> None:
        if SERVER.state != "ready":
            self.was_day = None
            return
        values = settings.all()
        day = self.is_day(values)
        started_day = day and self.was_day is False
        ended_day = (not day) and self.was_day is True
        self.was_day = day
        if started_day:
            self.manual_unload = False
        if ended_day:
            await self._end_of_day(values)
        if day and values["preload"] and not self.manual_unload:
            await self._keep_loaded(values)

    async def _end_of_day(self, values: dict) -> None:
        try:
            loaded = await ollama.ps()
        except Exception as e:
            logger.warning("End of the day: %s", e)
            return
        now = time.monotonic()
        for m in loaded:
            name = schedule.norm_model(m.get("name") or m.get("model") or "")
            idle = now - self.last_used.get(name, now - 10 ** 6)
            try:
                await ollama.set_keep_alive(name, schedule.night_keep_alive(idle, values), timeout=30)
            except Exception as e:
                logger.warning("End of the day for %s: %s", name, e)

    async def _keep_loaded(self, values: dict) -> None:
        kept = self.kept(values)
        if not kept or not ollama.MODELS.has(kept) or self.loading or not self.queue.idle():
            return
        try:
            loaded = await ollama.ps()
        except Exception:
            return
        if loaded:
            return
        await self.load(kept)

    async def load(self, model: str) -> None:
        """Load `model` for the day, through the queue (so it never overlaps a request)."""
        self.loading = True
        ticket = None
        try:
            ticket = self.queue.enter("household_ai", model, first=False)
            await self.queue.wait(ticket)
            logger.info("Loading %s for the day", model)
            await ollama.set_keep_alive(model, schedule.FOREVER)
        except Exception as e:
            logger.warning("Loading %s failed: %s", model, e)
        finally:
            if ticket is not None:
                self.queue.leave(ticket)
            self.loading = False

    async def unload_now(self) -> list[str]:
        self.manual_unload = True
        names = []
        for m in await ollama.ps():
            name = m.get("name") or m.get("model")
            await ollama.set_keep_alive(name, 0, timeout=30)
            names.append(name)
        return names

    def describe(self, loaded: list[dict]) -> list[dict]:
        """What is loaded, and until when, in words for the page."""
        values = settings.all()
        day = self.is_day(values)
        kept = self.kept(values)
        out = []
        for m in loaded:
            name = schedule.norm_model(m.get("name") or m.get("model") or "")
            if day and name == kept:
                rule = (f"kept until {values['day_end']}, then unloads {values['night_unload_min']} min after its "
                        "last use")
            else:
                mins = values["day_unload_min"] if day else values["night_unload_min"]
                rule = f"unloads {mins} min after its last use"
            out.append({"name": name, "size": m.get("size"), "expiresAt": m.get("expires_at"), "rule": rule})
        return out


SCHEDULE: Schedule | None = None        # made by main.py's lifespan (it needs the gateway's queue)


def machine_status() -> dict:
    return machine.summary()
