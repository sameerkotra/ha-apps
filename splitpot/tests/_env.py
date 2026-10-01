"""Import this FIRST in every test module: points Splitpot at a scratch data
directory and makes the app folder the working directory (main.py mounts
the frontend from the relative path "public") before main is imported."""
import os
import sys
import tempfile

_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)
os.chdir(_ROOT)

if "DATA_DIR" not in os.environ:
    os.environ["DATA_DIR"] = tempfile.mkdtemp(prefix="splitpot_test_")
os.environ.pop("SUPERVISOR_TOKEN", None)   # never talk to a real Home Assistant
