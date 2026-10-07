"""The shared sub-path link tests (common/tests/test_deeplinks.py), run here against common/ itself."""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from common.tests.test_deeplinks import *  # noqa: E402,F401,F403
