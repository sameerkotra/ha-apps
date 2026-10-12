"""Import this FIRST in every test module: it points the app at a scratch data directory (and a dev admin list)
before `app.config` is imported."""
import os

from common_tests.env import app_root, no_home_assistant, scratch_data_dir

app_root(__file__)
scratch_data_dir("proxy_test_")
no_home_assistant()                               # never talk to a real Home Assistant
os.environ.setdefault("DEV_ADMIN_USERS", "adminy")
os.environ["PROXY_NO_WATCHDOG"] = "1"             # monitoring rounds are run by the tests themselves
