"""Sub-path links into a page of the app (APP_MESSAGES_SPEC §6.5; the browser half is common/static/deeplink.js).

Shared by the household apps (common/python/deeplinks.py, copied by tools/sync_common.py). A notification or the
Household Assistant links to "/<full slug>/<route>". Home Assistant usually hands the route to the page (deeplink.js
reads it); when its frontend asks the app for the sub-path itself instead, these routes answer with a relative
redirect to the page's own "#/<route>", which keeps the request inside Ingress. Nothing is looked up here, and only the
routes the app names are answered, so its static files and /api keep their own paths.

    from .common import deeplinks
    deeplinks.add(app, {"group": 1, "dashboard": 0})     # /group/<id> → ../../#/group/<id>, /dashboard → ../#/dashboard
"""
import re

from fastapi import HTTPException
from fastapi.responses import RedirectResponse

ARG_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")


def redirect(parts: list[str]) -> RedirectResponse:
    """The relative redirect from "/<parts…>" to the page's "#/<parts…>"."""
    if not parts or any(not ARG_RE.match(p or "") for p in parts):
        raise HTTPException(404, "Not found.")
    return RedirectResponse("../" * len(parts) + "#/" + "/".join(parts), status_code=307,
                            headers={"Cache-Control": "no-store", "Referrer-Policy": "no-referrer"})


def add(app, routes: dict) -> None:
    """`routes`: {first path part: how many arguments follow it (0, 1 or 2)}."""
    for name, n in routes.items():
        if not ARG_RE.match(name) or n not in (0, 1, 2):
            raise ValueError(f"bad deep-link route {name!r}")
        if n == 0:
            app.add_api_route(f"/{name}", (lambda nm: lambda: redirect([nm]))(name), methods=["GET"],
                              include_in_schema=False)
        elif n == 1:
            app.add_api_route(f"/{name}/{{a}}", (lambda nm: lambda a: redirect([nm, a]))(name), methods=["GET"],
                              include_in_schema=False)
        else:
            app.add_api_route(f"/{name}/{{a}}/{{b}}", (lambda nm: lambda a, b: redirect([nm, a, b]))(name),
                              methods=["GET"], include_in_schema=False)
