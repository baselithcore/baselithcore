"""Admin API for plugin updates: the cached report and an on-demand check."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from core.middleware.security import require_admin

from .models import CheckReport
from .service import get_plugin_update_service

router = APIRouter(
    prefix="/api/plugins/updates",
    tags=["Plugin Updates"],
    dependencies=[Depends(require_admin)],
)


class PluginUpdatesResponse(BaseModel):
    """The last update report, and whether the feature is configured."""

    enabled: bool
    report: CheckReport | None = None


@router.get("", response_model=PluginUpdatesResponse)
async def get_updates() -> PluginUpdatesResponse:
    """Return the last saved check report (no network access)."""
    service = get_plugin_update_service()
    if service is None:
        return PluginUpdatesResponse(enabled=False, report=None)
    return PluginUpdatesResponse(enabled=True, report=service.report())


@router.post("/check", response_model=CheckReport)
async def check_updates() -> CheckReport:
    """Run an update check now (throttled per process) and return the report."""
    service = get_plugin_update_service()
    if service is None:
        raise HTTPException(status_code=503, detail="plugin updates are not configured")
    return await service.request_check()


__all__ = ["PluginUpdatesResponse", "router"]
