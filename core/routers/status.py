"""Backward-compatible shim for the API Routers status module."""

import sys

from fastapi import Response

from core.utils.optional_import import optional_router

_status, router = optional_router("plugins.api_routers.status")
if _status is not None:
    # Register self as the plugin module for runtime compatibility
    sys.modules[__name__] = _status
else:

    @router.get("/health")
    async def health_check() -> dict[str, str]:
        return {"status": "ok"}

    @router.get("/health/ready")
    async def readiness(response: Response) -> dict[str, object]:
        response.status_code = 200
        return {"status": "ready", "services": {}, "cached": False}

__all__ = ["router"]
