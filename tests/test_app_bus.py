"""The shared app bus tests (common/tests/test_app_bus.py), run here against common/ itself.

They live in common/tests so that every app using the bus runs them against its own copies too."""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from common.tests.test_app_bus import *  # noqa: E402,F401,F403
