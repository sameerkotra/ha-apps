"""Admin → App settings: everything an administrator can change without restarting the app."""

from fastapi import APIRouter, Body, Depends, HTTPException

from app import app_settings
from app.auth import CurrentUser, require_admin

router = APIRouter(prefix="/api/admin", tags=["admin"])


@router.get("/settings")
async def get_settings_page(_: CurrentUser = Depends(require_admin)):
    """``{values, defaults, meta, groups, secretsSet}``. Secrets are never sent, only whether each is set."""
    return app_settings.payload()


@router.put("/settings")
async def put_settings(payload: dict = Body(...), admin: CurrentUser = Depends(require_admin)):
    """Change some settings: send only the ones that change. A secret sent as "" is cleared."""
    try:
        app_settings.update(payload, admin.display_name or admin.username or admin.id)
    except app_settings.SettingsError as e:
        raise HTTPException(status_code=422, detail=str(e)) from None
    return app_settings.payload()
