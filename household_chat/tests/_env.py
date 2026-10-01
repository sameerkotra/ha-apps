"""Test environment. Import FIRST in every test module (app/config.py reads its environment at import)."""
import atexit
import os
import shutil
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
if not os.environ.get("HC_TEST_TMP"):
    TMP = tempfile.mkdtemp(prefix="household_chat_tests_")
    os.environ["HC_TEST_TMP"] = TMP
    atexit.register(shutil.rmtree, TMP, True)
TMP = os.environ["HC_TEST_TMP"]
DATA_DIR = os.path.join(TMP, "data")
os.makedirs(DATA_DIR, exist_ok=True)
os.environ["DATA_DIR"] = DATA_DIR
os.environ.pop("DB_PATH", None)
os.environ["SHARE_DIR"] = os.path.join(TMP, "share", "household_chat")
os.environ["DEV_ADMINS"] = "admin"
os.environ["OPTIONS_PATH"] = os.path.join(TMP, "no-such-options.json")
os.environ.pop("SUPERVISOR_TOKEN", None)
