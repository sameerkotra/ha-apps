"""No login: on a Docker host the settings page is for whoever can reach it (keep its port to your own network).
Everyone is the app's admin. The gateway on 11434 has its own protection: the allow-list, and access keys when
*Require an access key* is on.
"""
from fastapi import Request

from . import config

LOCAL = {"id": "local", "name": "Admin", "username": "admin", "is_admin": True}


def no_admins() -> bool:
    return False


async def get_current_user(request: Request) -> dict:
    return dict(LOCAL)


async def require_admin(request: Request) -> dict:
    return dict(LOCAL)


__all__ = ["config", "get_current_user", "require_admin", "no_admins"]
