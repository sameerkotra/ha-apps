"""Optional parts of the app, switched on App settings → Features (SPEC.md section 23).

A part that is off keeps its data but has no nav entry, and its pages answer with a short
"turned off" page (404). request.state.features ({"utilities": bool, "tolls": bool}) is set for
every page by main.py, so templates can hide links.
"""
from fastapi import Depends

from . import settings
from .auth import User, get_current_user

NAMES = {"utilities": "Utilities", "tolls": "Tolls"}


class FeatureOff(Exception):
    def __init__(self, name: str, user: User):
        super().__init__(name)
        self.name, self.user = name, user


def current() -> dict[str, bool]:
    return {name: settings.feature(name) for name in NAMES}


def required(name: str):
    def dependency(current_user: User = Depends(get_current_user)) -> None:
        if not settings.feature(name):
            raise FeatureOff(name, current_user)
    return dependency
