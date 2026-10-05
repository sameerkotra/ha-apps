"""Test environment. Import FIRST in every test module (app/config.py reads its environment at import)."""
import os

from common_tests.env import app_root, data_dir_in, no_home_assistant, no_options_file, scratch_root

ROOT = app_root(__file__)
TMP = scratch_root("household_chat_tests_", "HC_TEST_TMP")
DATA_DIR = data_dir_in(TMP)
os.environ["SHARE_DIR"] = os.path.join(TMP, "share", "household_chat")
os.environ["DEV_ADMINS"] = "admin"
no_options_file(TMP)
no_home_assistant()
