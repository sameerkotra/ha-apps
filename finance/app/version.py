"""APP_VERSION: config.yaml's `version`, read once with a regex (no YAML
dependency). Templates append it to static URLs so every rebuild busts
browser/WebView caches. "0" if config.yaml can't be read."""
import re
from pathlib import Path

_CONFIG_PATH = Path(__file__).parent.parent / "config.yaml"


def _read_version() -> str:
    try:
        text = _CONFIG_PATH.read_text(encoding="utf-8")
    except OSError:
        return "0"
    m = re.search(r'^version:\s*"([^"]+)"', text, re.MULTILINE)
    return m.group(1) if m else "0"


APP_VERSION = _read_version()
