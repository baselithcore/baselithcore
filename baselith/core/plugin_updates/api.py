"""Admin API for plugin updates: the cached report, a check and a pre-flight."""

from __future__ import annotations

import asyncio

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel

from baselith.core.middleware.security import require_admin

from .models import CheckReport
from .preflight import PreflightItem
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


@router.get("/preflight", response_model=list[PreflightItem])
async def get_preflight(
    subject: str = Query(..., min_length=1, max_length=80),
    version: str | None = Query(None, max_length=64),
) -> list[PreflightItem]:
    """Whether ``subject`` (``core`` or ``plugin:<name>``) can be installed now.

    Read-only: it reads the saved report, the run store, the task queue and
    the disk, and never writes or contacts GitHub.
    """
    service = get_plugin_update_service()
    if service is None:
        raise HTTPException(status_code=503, detail="plugin updates are not configured")
    return await asyncio.to_thread(service.preflight, subject, version)


__all__ = ["PluginUpdatesResponse", "router"]
