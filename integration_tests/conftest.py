"""Tests of the Household Assistant integration (custom_components/household_assistant) in a real Home Assistant core,
through pytest-homeassistant-custom-component (not needed by the apps):

    pip install pytest-homeassistant-custom-component
    python -m pytest integration_tests
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))   # custom_components/

pytest_plugins = "pytest_homeassistant_custom_component"


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations):
    yield
