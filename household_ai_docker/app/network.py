"""Is the gateway on the network? On a Docker host it is, on the port docker publishes (PUBLIC_GATEWAY_PORT): the page
says so, and warns while *Require an access key* is off.
"""
from . import config


async def published_port(force: bool = False) -> dict:
    """{"known": True, "port": the published port}."""
    return {"known": True, "port": config.PUBLIC_GATEWAY_PORT}
