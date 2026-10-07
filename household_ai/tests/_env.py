"""Import this FIRST in every test module: a scratch data directory, a dev admin list, the fake model server
(tests/fake_ollama.py) on a free port, and no gateway of the app's own (tests start one where they need it)."""
import os
import socket
import sys

from common_tests.env import app_root, no_home_assistant, scratch_data_dir

HERE = os.path.dirname(os.path.abspath(__file__))
app_root(__file__)
DATA = scratch_data_dir("household_ai_test_")
no_home_assistant()
os.environ.setdefault("DEV_ADMIN_USERS", "adminy")


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


os.environ.setdefault("OLLAMA_BIN", f"{sys.executable} {os.path.join(HERE, 'fake_ollama.py')}")
os.environ.setdefault("OLLAMA_PORT", str(free_port()))
os.environ.setdefault("GATEWAY_PORT", "0")
os.environ.setdefault("GATEWAY_HOST", "127.0.0.1")
os.environ.setdefault("FAKE_OLLAMA_LOG", os.path.join(DATA, "fake_ollama.log"))
os.environ.setdefault("HOUSEHOLD_AI_HOSTNAME", "a1b2c3d4-household-ai")
os.environ.setdefault("OLLAMA_REGISTRY_URL", "http://127.0.0.1:9")      # no internet in tests
