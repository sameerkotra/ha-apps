"""Shared bits of the API routers. (Path errors, vanished files and refused file-system calls are turned into
422 / 404 / 500 answers by main.py's exception handlers.)"""
from pydantic import BaseModel, ConfigDict


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")
