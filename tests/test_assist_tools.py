"""The shared Household Assistant tools tests (common/tests/test_assist_tools.py), run here against common/ itself.

They live in common/tests so that every app answering the assistant runs them against its own copies too."""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from common.tests.test_assist_tools import *  # noqa: E402,F401,F403
