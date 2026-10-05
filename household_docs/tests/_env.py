"""Import this FIRST in every test module: it points the app at a scratch data folder, a scratch /share and a
fake Home Assistant before `app.config` is imported."""
import os
import tempfile

from common_tests.env import app_root, scratch_data_dir

app_root(__file__)
scratch_data_dir("docs_test_")
os.environ.setdefault("SHARE_DIR", tempfile.mkdtemp(prefix="docs_share_"))
os.environ.setdefault("OPTIONS_PATH", os.path.join(os.environ["DATA_DIR"], "options.json"))
os.environ.setdefault("SUPERVISOR_TOKEN", "test-token")
os.environ.setdefault("DEV_ADMINS", "asha")
# No background loops under test: the tests run the passes (scans, housekeeping) themselves.
os.environ.setdefault("BACKGROUND_LOOPS", "0")
