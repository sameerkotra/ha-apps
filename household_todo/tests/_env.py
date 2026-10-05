"""Import this FIRST in every test module: it points the app at a scratch
data directory and a fake Home Assistant before `app.config` is imported."""
import os

from common_tests.env import app_root, scratch_data_dir

app_root(__file__)
scratch_data_dir("household_test_")
os.environ.setdefault("OPTIONS_PATH", os.path.join(os.environ["DATA_DIR"], "options.json"))
os.environ.setdefault("SUPERVISOR_TOKEN", "test-token")
os.environ.setdefault("DEV_ADMINS", "adminy")
# No lifespan background loops under test: every test deletes and recreates
# the database, and a loop tick running in a worker thread meanwhile caused
# random "disk I/O error" / "no such table" failures. Tests call the passes
# (and ha_sensors.loop) directly.
os.environ.setdefault("BACKGROUND_LOOPS", "0")
