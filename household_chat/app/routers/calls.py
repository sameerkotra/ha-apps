"""Voice calls (SPEC §15.12): start, offer, answer, decline, end, network candidates, and my call."""
from typing import Any

from fastapi import APIRouter, Depends
from pydantic import Field

from .. import calls, settings
from ..auth import require_admin, require_user
from .common import Strict

router = APIRouter(prefix="/api", tags=["calls"])


class StartIn(Strict):
    conversationId: str = Field(max_length=64)


class SdpIn(Strict):
    sdp: str = Field(max_length=calls.MAX_SDP)


class CandidateIn(Strict):
    candidate: dict[str, Any]


class EndIn(Strict):
    reason: str | None = Field(default=None, pattern="^(failed|no_microphone)$")


@router.post("/calls", status_code=201)
def start(body: StartIn, user: dict = Depends(require_user)):
    return calls.start(user, body.conversationId)


@router.get("/calls/current")
def current(user: dict = Depends(require_user)):
    return {"call": calls.current(user)}


@router.post("/calls/{call_id}/offer")
def offer(call_id: str, body: SdpIn, user: dict = Depends(require_user)):
    return calls.offer(user, call_id, body.sdp)


@router.post("/calls/{call_id}/answer")
def answer(call_id: str, body: SdpIn, user: dict = Depends(require_user)):
    return calls.answer(user, call_id, body.sdp)


@router.post("/calls/{call_id}/candidate")
def candidate(call_id: str, body: CandidateIn, user: dict = Depends(require_user)):
    return calls.candidate(user, call_id, body.candidate)


@router.post("/calls/{call_id}/decline")
def decline(call_id: str, user: dict = Depends(require_user)):
    return calls.decline(user, call_id)


@router.get("/admin/calls/ice-servers")
def test_servers(admin: dict = Depends(require_admin)):
    """Test calling (App settings): the STUN and relay addresses as a call would get them, so the admin's browser
    can check it reaches them (§15.13)."""
    values = settings.all_values()
    servers = calls.ice_servers(values, "test")
    return {"iceServers": servers, "stun": bool(values["calls_stun"]), "relay": values["calls_relay"],
            "relayOk": values["calls_relay"] == "none" or any("username" in s for s in servers),
            "relayError": calls.relay_error() if values["calls_relay"] == "cloudflare" else None}


@router.post("/calls/{call_id}/end")
def end(call_id: str, body: EndIn | None = None, user: dict = Depends(require_user)):
    return calls.hang_up(user, call_id, body.reason if body else None)
