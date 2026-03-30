"""
FastAPI router for ${{ values.pluginName }}.

Dogma I  — Separation of Concerns: HTTP transport only. Business logic
             belongs in the agent or a dedicated service layer.
Dogma III— Async Everything: all route handlers are async.
"""

from fastapi import APIRouter
from typing import Any, Dict

router = APIRouter(
    prefix="/${{ values.pluginName }}",
    tags=["${{ values.pluginName }}"],
)


@router.get("/health")
async def health_check() -> Dict[str, str]:
    """Plugin liveness probe."""
    return {"status": "ok", "plugin": "${{ values.pluginName }}"}


@router.get("/info")
async def get_info() -> Dict[str, Any]:
    """Return plugin identity metadata."""
    return {
        "name": "${{ values.pluginName }}",
        "version": "${{ values.version }}",
        "description": "${{ values.description }}",
    }

# TODO: Add domain-specific endpoints below.
