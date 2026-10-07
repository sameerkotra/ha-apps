"""The Models list (SPEC.md §2): the recommended models shipped with the app, what is downloaded, downloads with
progress (cancellable) and deletes.

Downloads come from Ollama's registry: a machine without internet, or a failed download, gets the registry's
error in plain words. A partial download is left to the model server, which removes unused files the next time
it starts. A download is refused when the free disk under /data would fall under 2 GB.
"""
import asyncio
import logging
import time
import uuid

import httpx

from . import config, machine, ollama, schedule, settings

logger = logging.getLogger("models")

GB = 1024 ** 3
DISK_MARGIN = 2 * GB

# name, kind, download size (GB), memory needed (GB), which apps it suits. Sizes checked at build time.
RECOMMENDED = [
    {"name": "qwen2.5:1.5b", "kind": "text", "download": 1.0, "ram": 1.5,
     "suits": "The assistant on a Raspberry Pi; Docs, Calorie Tracker, Arcade extras"},
    {"name": "qwen2.5:3b", "kind": "text", "download": 1.9, "ram": 2.5, "default": True,
     "suits": "The assistant; Docs, Calorie Tracker, Arcade extras; Finance's optional text model"},
    {"name": "llama3.2:3b", "kind": "text", "download": 2.0, "ram": 2.5,
     "suits": "The same, an alternative"},
    {"name": "qwen2.5vl:3b", "kind": "vision", "download": 3.2, "ram": 4.0,
     "suits": "Receipt Price Intelligence, Docs' text from scans — slow on a CPU"},
    {"name": "qwen2.5vl:7b", "kind": "vision", "download": 6.0, "ram": 7.0,
     "suits": "Finance Dashboard statements, Receipt (better reading) — 16 GB machines"},
]
BY_NAME = {schedule.norm_model(m["name"]): m for m in RECOMMENDED}


class Pull:
    def __init__(self, name: str):
        self.id = uuid.uuid4().hex[:12]
        self.name = name
        self.status = "Starting"
        self.completed = 0
        self.total = 0
        self.error: str | None = None
        self.done = False
        self.cancelled = False
        self.started = time.monotonic()
        self.task: asyncio.Task | None = None

    def view(self) -> dict:
        pct = round(100 * self.completed / self.total, 1) if self.total else None
        return {"id": self.id, "name": self.name, "status": self.status, "completed": self.completed,
                "total": self.total, "percent": pct, "error": self.error, "done": self.done,
                "cancelled": self.cancelled}


PULLS: dict[str, Pull] = {}


def manifest_url(name: str) -> str:
    """The registry's manifest address for a model name ("qwen2.5:3b" → …/v2/library/qwen2.5/manifests/3b)."""
    full = schedule.norm_model(name)
    repo, _, tag = full.rpartition(":")
    if "/" not in repo:
        repo = "library/" + repo
    return f"{config.REGISTRY_URL}/v2/{repo}/manifests/{tag}"


async def download_size(name: str) -> int | None:
    """Bytes to download: the registry's manifest (best effort), else the recommended table, else None."""
    missing = False
    try:
        async with httpx.AsyncClient(timeout=8) as c:
            r = await c.get(manifest_url(name),
                            headers={"Accept": "application/vnd.docker.distribution.manifest.v2+json"})
        if r.status_code == 200:
            m = r.json()
            return (sum(int(x.get("size") or 0) for x in (m.get("layers") or []))
                    + int((m.get("config") or {}).get("size") or 0))
        missing = r.status_code == 404
    except (httpx.HTTPError, ValueError, TypeError, AttributeError):
        pass
    if missing:
        raise ValueError(f"Ollama's library has no model called {name}.")
    rec = BY_NAME.get(schedule.norm_model(name))
    return int(rec["download"] * GB) if rec else None


def check_disk(size: int | None, free: int | None) -> None:
    if free is None:
        return
    need = (size or 0) + DISK_MARGIN
    if free < need:
        raise ValueError(f"Not enough disk: this download needs about {fmt(size)} and Home Assistant should keep "
                         f"2 GB free; there are {fmt(free)} free.")


def fmt(n: int | None) -> str:
    if not n:
        return "an unknown size"
    return f"{n / GB:.1f} GB"


async def start_pull(name: str) -> Pull:
    name = name.strip()
    if not settings.MODEL_NAME.match(name):
        raise ValueError("That isn't a model name (e.g. qwen2.5:3b).")
    for p in PULLS.values():
        if not p.done and schedule.norm_model(p.name) == schedule.norm_model(name):
            return p
    size = await download_size(name)
    check_disk(size, machine.disk(config.MODELS_DIR if _exists(config.MODELS_DIR) else config.DATA_DIR)["free"])
    pull = Pull(name)
    pull.total = size or 0
    PULLS[pull.id] = pull
    pull.task = asyncio.create_task(_run(pull), name=f"pull {name}")
    _forget_old()
    return pull


def _exists(path: str) -> bool:
    import os
    return os.path.isdir(path)


async def _run(pull: Pull) -> None:
    def progress(status, completed, total):
        pull.status = status or pull.status
        if total:
            pull.total = int(total)
        if completed is not None:
            pull.completed = int(completed)
    try:
        await ollama.pull(pull.name, progress)
        pull.status = "Downloaded"
        logger.info("Downloaded %s", pull.name)
    except asyncio.CancelledError:
        pull.cancelled = True
        pull.status = "Cancelled"
    except ollama.OllamaError as e:
        pull.error = plain(str(e))
        pull.status = "Failed"
        logger.warning("Download of %s failed: %s", pull.name, e)
    except httpx.HTTPError as e:
        pull.error = "The model server stopped answering during the download."
        pull.status = "Failed"
        logger.warning("Download of %s failed: %s", pull.name, e)
    finally:
        pull.done = True
        try:
            await ollama.MODELS.refresh()
        except Exception:
            pass


def plain(message: str) -> str:
    low = message.lower()
    if "file does not exist" in low or "not found" in low:
        return "Ollama's library has no model by that name."
    if "no such host" in low or "dial tcp" in low or "lookup" in low or "connection refused" in low:
        return f"Couldn't reach Ollama's library (is the machine on the internet?) — {message}"
    if "no space" in low:
        return "The disk is full."
    return message


def cancel(pull_id: str) -> bool:
    p = PULLS.get(pull_id)
    if p is None or p.done or p.task is None:
        return False
    p.task.cancel()
    return True


def _forget_old() -> None:
    old = [k for k, p in PULLS.items() if p.done and time.monotonic() - p.started > 3600]
    for k in old:
        del PULLS[k]


async def listing() -> dict:
    """The Models section: recommended (with whether each is downloaded), every downloaded model, the kept one,
    and the models the settings name that are missing (after a restore)."""
    rows = ollama.MODELS.rows
    downloaded = {schedule.norm_model(r["name"]): r for r in rows}
    values = settings.all()
    kept = schedule.kept_model(values, ollama.MODELS.text_models())
    mem = machine.memory()
    out_rec = []
    for m in RECOMMENDED:
        full = schedule.norm_model(m["name"])
        out_rec.append({**m, "downloaded": full in downloaded})
    out_down = [{"name": schedule.norm_model(r["name"]), "size": r.get("size"), "kind": r.get("kind", "text"),
                 "modifiedAt": r.get("modified_at"), "ram": BY_NAME.get(schedule.norm_model(r["name"]), {}).get("ram"),
                 "kept": schedule.norm_model(r["name"]) == kept} for r in rows]
    missing = []
    if values["keep_model"] and values["keep_model"] != "none" and \
            schedule.norm_model(values["keep_model"]) not in downloaded and rows is not None:
        missing.append(values["keep_model"])
    return {"recommended": out_rec, "downloaded": out_down, "kept": kept, "keepSetting": values["keep_model"],
            "missing": missing, "memory": mem, "pulls": [p.view() for p in PULLS.values()],
            "disk": machine.disk(config.DATA_DIR)}
