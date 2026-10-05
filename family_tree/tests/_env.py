"""Test environment. Import this FIRST in every test module (before `app`):
app/config.py reads its environment at import time.

  DATA_DIR       fresh temp dir (family.db lives here)
  MEDIA_PATH     <tmp>/share/family_tree  (its parent exists, the folder doesn't)
  ALLOW_ANY_MEDIA_PATH=1, DEV_ADMINS=admin
  OPTIONS_PATH   a file that doesn't exist (defaults apply)
  SUPERVISOR_TOKEN removed (no Home Assistant calls)
"""
import os

from common_tests.env import app_root, data_dir_in, no_home_assistant, no_options_file, scratch_root

ROOT = app_root(__file__)
TMP = scratch_root("family_tree_tests_", "FT_TEST_TMP")

DATA_DIR = data_dir_in(TMP)
SHARE_DIR = os.path.join(TMP, "share")
MEDIA_PATH = os.path.join(SHARE_DIR, "family_tree")
os.makedirs(SHARE_DIR, exist_ok=True)

os.environ["MEDIA_PATH"] = MEDIA_PATH
os.environ["ALLOW_ANY_MEDIA_PATH"] = "1"
os.environ["DEV_ADMINS"] = "admin"
no_options_file(TMP)
no_home_assistant()
