"""Import this FIRST in every test module: it points the app at a scratch
data directory and a fake Home Assistant before `app.config` is imported."""
import os
import sys
import tempfile

_ROOT = os.path.join(os.path.dirname(__file__), "..")
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

if "DATA_DIR" not in os.environ:
    os.environ["DATA_DIR"] = tempfile.mkdtemp(prefix="arcade_test_")
os.environ.setdefault("OPTIONS_PATH", os.path.join(os.environ["DATA_DIR"], "options.json"))
os.environ.setdefault("SUPERVISOR_TOKEN", "test-token")
os.environ.setdefault("DEV_ADMINS", "asha")
# No lifespan background loops under test: tests call the passes directly.
os.environ.setdefault("BACKGROUND_LOOPS", "0")
