"""Test environment. Import this FIRST in every test module (before `app`):
app/config.py reads its environment at import time.

  DATA_DIR       fresh temp dir (family.db lives here)
  MEDIA_PATH     <tmp>/share/family_tree  (its parent exists, the folder doesn't)
  ALLOW_ANY_MEDIA_PATH=1, DEV_ADMINS=admin
  OPTIONS_PATH   a file that doesn't exist (defaults apply)
  SUPERVISOR_TOKEN removed (no Home Assistant calls)
"""
import atexit
import os
import shutil
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

if not os.environ.get("FT_TEST_TMP"):
    TMP = tempfile.mkdtemp(prefix="family_tree_tests_")
    os.environ["FT_TEST_TMP"] = TMP
    atexit.register(shutil.rmtree, TMP, True)
TMP = os.environ["FT_TEST_TMP"]

DATA_DIR = os.path.join(TMP, "data")
SHARE_DIR = os.path.join(TMP, "share")
MEDIA_PATH = os.path.join(SHARE_DIR, "family_tree")
os.makedirs(DATA_DIR, exist_ok=True)
os.makedirs(SHARE_DIR, exist_ok=True)

os.environ["DATA_DIR"] = DATA_DIR
os.environ.pop("DB_PATH", None)
os.environ["MEDIA_PATH"] = MEDIA_PATH
os.environ["ALLOW_ANY_MEDIA_PATH"] = "1"
os.environ["DEV_ADMINS"] = "admin"
os.environ["OPTIONS_PATH"] = os.path.join(TMP, "no-such-options.json")
os.environ.pop("SUPERVISOR_TOKEN", None)
