"""Request-level protections that apply to every route.

1. Ingress only. Home Assistant's Supervisor proxies every ingress request from
   172.30.32.2, and it is the only thing that sets the X-Ingress-Path /
   X-Remote-User-* headers auth.py trusts. Anything else on the internal
   network could send those headers itself, so any other client address is
   refused. Extra addresses can be allowed with the app option
   `trusted_client_ips` (bootstrap.py passes it as TRUSTED_CLIENT_IPS);
   ALLOW_ANY_CLIENT=1 turns the check off for local development (DEV.md).

2. Cross-site form posts. Browsers label every request with Sec-Fetch-Site.
   A state-changing request that a browser marks as coming from another site
   is refused. Requests without the header (older WebViews, curl) are allowed:
   the random per-session ingress URL is still a second barrier there.
"""
import logging
import os

from starlette.responses import PlainTextResponse

logger = logging.getLogger(__name__)

SUPERVISOR_INGRESS_IP = "172.30.32.2"
_SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}


def trusted_client_ips() -> set[str]:
    extra = {ip.strip() for ip in os.environ.get("TRUSTED_CLIENT_IPS", "").split(",") if ip.strip()}
    return {SUPERVISOR_INGRESS_IP} | extra


def install(app) -> None:
    trusted = trusted_client_ips()
    allow_any = os.environ.get("ALLOW_ANY_CLIENT") == "1"

    @app.middleware("http")
    async def _guard(request, call_next):
        host = request.client.host if request.client else ""
        if not allow_any and host not in trusted:
            logger.warning("Refused request from %s (not Home Assistant ingress)", host)
            return PlainTextResponse(
                f"Forbidden: this app only accepts requests through Home Assistant ingress "
                f"(this connection came from {host}). If that address really is your ingress proxy, "
                f"add it to trusted_client_ips on the app's Configuration tab.",
                status_code=403,
            )
        if request.method not in _SAFE_METHODS and request.headers.get("sec-fetch-site") in ("cross-site", "same-site"):
            logger.warning("Refused cross-site %s %s", request.method, request.url.path)
            return PlainTextResponse("Forbidden: cross-site request", status_code=403)
        return await call_next(request)
