"""Import this FIRST in every test module: it points the app at a scratch
data directory and a fake Home Assistant before `app.config` is imported."""
import os

from common_tests.env import app_root, scratch_data_dir

app_root(__file__)
scratch_data_dir("arcade_test_")
os.environ.setdefault("OPTIONS_PATH", os.path.join(os.environ["DATA_DIR"], "options.json"))
os.environ.setdefault("SUPERVISOR_TOKEN", "test-token")
os.environ.setdefault("DEV_ADMINS", "asha")
# No lifespan background loops under test: tests call the passes directly.
os.environ.setdefault("BACKGROUND_LOOPS", "0")
