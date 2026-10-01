"""Admin → AI usage: the running total of every AI request, admin only (SPEC.md section 21.3)."""
import asyncio
from pathlib import Path

from fastapi import APIRouter, Depends, Request
from fastapi.templating import Jinja2Templates

from .. import ai_usage
from ..auth import User, get_acting_user, require_admin
from ..version import APP_VERSION
from .accounts import _acting_banner, _acting_qs

router = APIRouter()
templates = Jinja2Templates(directory=str(Path(__file__).parent.parent / "templates"))
templates.env.globals["app_version"] = APP_VERSION


@router.get("/ai-usage")
async def ai_usage_page(request: Request, current: User = Depends(require_admin),
                        acting: User = Depends(get_acting_user)):
    totals = await asyncio.to_thread(ai_usage.totals)
    return templates.TemplateResponse(request, "ai_usage.html", {
        "user": acting, "acting_as_banner": _acting_banner(current, acting), "acting_qs": _acting_qs(current, acting),
        "active_page": "ai_usage", "totals": totals,
    })
