"""Import this FIRST in every test module: it points the app at a scratch
data directory and a fake Home Assistant before `app.config` is imported."""
import os
import sys
import tempfile

_ROOT = os.path.join(os.path.dirname(__file__), "..")
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

if "DATA_DIR" not in os.environ:
    os.environ["DATA_DIR"] = tempfile.mkdtemp(prefix="household_test_")
os.environ.setdefault("OPTIONS_PATH", os.path.join(os.environ["DATA_DIR"], "options.json"))
os.environ.setdefault("SUPERVISOR_TOKEN", "test-token")
os.environ.setdefault("DEV_ADMINS", "adminy")
# No lifespan background loops under test: every test deletes and recreates
# the database, and a loop tick running in a worker thread meanwhile caused
# random "disk I/O error" / "no such table" failures. Tests call the passes
# (and ha_sensors.loop) directly.
os.environ.setdefault("BACKGROUND_LOOPS", "0")
