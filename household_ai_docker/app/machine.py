"""What the page says about the machine (SPEC.md §2): memory, cores, free disk under /data, whether the models
sit on an SD card, and the CPU temperature where the machine reports one.

Everything is read from /proc and /sys (PROC_ROOT / SYS_ROOT move them for tests); nothing here fails: a value
that can't be read is None.
"""
import glob
import os
import shutil

from . import config

PROC = os.environ.get("PROC_ROOT", "/proc")
SYS = os.environ.get("SYS_ROOT", "/sys")
GB = 1024 ** 3
# A Raspberry Pi starts slowing its CPU at 80 °C (85 °C hard); the page warns a little before.
HOT_C = 75.0


def memory() -> dict:
    """{"total": bytes, "free": bytes} from /proc/meminfo (MemAvailable: what a new program could use)."""
    out = {"total": None, "free": None}
    try:
        with open(os.path.join(PROC, "meminfo"), encoding="ascii", errors="replace") as f:
            for line in f:
                key, _, rest = line.partition(":")
                parts = rest.split()
                if not parts:
                    continue
                value = int(parts[0]) * 1024
                if key == "MemTotal":
                    out["total"] = value
                elif key == "MemAvailable":
                    out["free"] = value
    except (OSError, ValueError):
        pass
    return out


def disk(path: str | None = None) -> dict:
    path = path or config.DATA_DIR
    try:
        u = shutil.disk_usage(path)
        return {"total": u.total, "free": u.free}
    except OSError:
        return {"total": None, "free": None}


def on_sd_card(path: str | None = None) -> bool | None:
    """True when the filesystem holding `path` is on an SD card (an mmcblk device), as on a Raspberry Pi without
    an SSD; None when /proc/mounts can't be read."""
    path = os.path.abspath(path or config.DATA_DIR)
    try:
        with open(os.path.join(PROC, "mounts"), encoding="utf-8", errors="replace") as f:
            mounts = [line.split()[:2] for line in f if len(line.split()) >= 2]
    except OSError:
        return None
    best, device = "", ""
    for dev, point in mounts:
        if (path == point or path.startswith(point.rstrip("/") + "/")) and len(point) >= len(best):
            best, device = point, dev
    return "mmcblk" in device


def temperature() -> float | None:
    """The hottest CPU-ish thermal zone, in °C, or None."""
    temps = []
    for zone in glob.glob(os.path.join(SYS, "class", "thermal", "thermal_zone*")):
        try:
            with open(os.path.join(zone, "temp"), encoding="ascii") as f:
                milli = int(f.read().strip())
        except (OSError, ValueError):
            continue
        if 0 < milli < 150000:
            temps.append(milli / 1000)
    return round(max(temps), 1) if temps else None


def cores() -> int:
    return os.cpu_count() or 1


def default_threads() -> int:
    """All cores but one, at least 1 (SPEC.md §6.1)."""
    return max(1, cores() - 1)


def summary() -> dict:
    mem = memory()
    d = disk()
    temp = temperature()
    return {"memory": mem, "cores": cores(), "disk": d, "sdCard": on_sd_card(), "temperature": temp,
            "hot": temp is not None and temp >= HOT_C}
