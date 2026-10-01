#!/usr/bin/env python3
"""App entrypoint (Docker CMD). Home Assistant writes the app options to
/data/options.json; this turns them into the environment variables the app
reads at import time, resolves the time zone, then execs uvicorn. Local
development runs uvicorn directly and sets the variables itself (DEV.md).

TZ has to be set here, before the process starts: date.today() /
datetime.now() read the OS local time, which only follows TZ if it is already
in the environment. SQLite's datetime('now') timestamps stay UTC regardless.
"""
import json
import os
import urllib.request

OPTIONS_PATH = "/data/options.json"

# Only when Home Assistant's own zone can't be read. Mountain Time with
# daylight saving (the install is in Colorado) — not fixed-offset "MST".
FALLBACK_TZ = "America/Denver"


def _resolve_timezone() -> str:
    """Home Assistant's configured zone (Settings -> System -> General), read
    through Supervisor's Core API proxy — needs `homeassistant_api: true`,
    which makes Supervisor inject SUPERVISOR_TOKEN. Any failure -> fallback."""
    token = os.environ.get("SUPERVISOR_TOKEN", "")
    if not token:
        return FALLBACK_TZ
    try:
        req = urllib.request.Request(
            "http://supervisor/core/api/config",
            headers={"Authorization": f"Bearer {token}"},
        )
        with urllib.request.urlopen(req, timeout=5) as resp:
            data = json.load(resp)
        return (data.get("time_zone") or "").strip() or FALLBACK_TZ
    except Exception:
        return FALLBACK_TZ


if os.path.exists(OPTIONS_PATH):
    with open(OPTIONS_PATH) as f:
        options = json.load(f)
    os.environ["ADMIN_USERS"] = ",".join(options.get("admin_users", []))
    # No longer app options (Admin → App settings holds them). A value still left in
    # options.json from an older version seeds Settings once, if Settings is empty.
    os.environ["OLLAMA_URL"] = options.get("ollama_url") or ""
    os.environ["OLLAMA_MODEL"] = options.get("ollama_model") or ""
    # Extra client addresses allowed besides Supervisor's ingress proxy (security.py).
    os.environ["TRUSTED_CLIENT_IPS"] = ",".join(options.get("trusted_client_ips", []))

os.environ["TZ"] = _resolve_timezone()

# --no-proxy-headers: the client address security.py checks must be the real TCP
# peer (Supervisor's proxy), never something rewritten from X-Forwarded-For.
os.execvp("uvicorn", ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8321", "--no-proxy-headers"])
