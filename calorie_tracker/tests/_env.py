"""Import this FIRST in every test module: it points the app at a scratch
data directory (and a dev admin list) before `app.config` is imported."""
import os
import sys
import tempfile

_ROOT = os.path.join(os.path.dirname(__file__), "..")
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

if "DATA_DIR" not in os.environ:
    os.environ["DATA_DIR"] = tempfile.mkdtemp(prefix="calorie_test_")
os.environ.pop("SUPERVISOR_TOKEN", None)          # never talk to a real Home Assistant
os.environ.setdefault("DEV_ADMIN_USERS", "adminy")
