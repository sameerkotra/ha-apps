# Shared file: edit common/tests/ingress.py and run tools/sync_common.py; don't edit this copy. sha256=b8889c2edb0b319b9b1b8d20187560fceafaed4f0d2c276bb150729bc63cf399
"""Requests the way Home Assistant's ingress proxy makes them (shared: common/tests/ingress.py).

Supervisor's ingress proxy connects from its own address and adds the person's identity:

    X-Remote-User-Id            stable Home Assistant user id
    X-Remote-User-Name          login name (some auth providers don't send it)
    X-Remote-User-Display-Name  display name
    X-Ingress-Path              the ingress prefix (only Supervisor sets it)

    from common_tests.ingress import identity_headers, ingress_client, user_headers
    client = ingress_client(app)                       # a TestClient connecting from 127.0.0.1
    client.get("/api/me", headers=identity_headers("u1", "alice", "Alice"))
"""
from starlette.testclient import TestClient

INGRESS_PEER = ("127.0.0.1", 12345)          # stands in for Supervisor's ingress proxy in tests
INGRESS_PATH = "/api/hassio_ingress/x"       # an X-Ingress-Path value


def identity_headers(user_id, username=None, display_name=None, *, ingress_path=None) -> dict:
    """The identity headers for one person; a header whose value is None or "" is left out
    (as when Home Assistant doesn't send it)."""
    h = {"X-Remote-User-Id": user_id}
    if username:
        h["X-Remote-User-Name"] = username
    if display_name:
        h["X-Remote-User-Display-Name"] = display_name
    if ingress_path:
        h["X-Ingress-Path"] = ingress_path
    return h


def user_headers(user: dict, *, ingress_path=None) -> dict:
    """identity_headers for a test person written as {"id": ..., "name": login, "display": display name}."""
    return identity_headers(user["id"], user.get("name"), user.get("display"), ingress_path=ingress_path)


def ingress_client(app, peer=INGRESS_PEER, **kw) -> TestClient:
    """A TestClient whose requests come from `peer` (the address apps accept as the ingress proxy).
    Use it as is, or as a context manager to run the app's startup and shutdown."""
    return TestClient(app, client=peer, **kw)
