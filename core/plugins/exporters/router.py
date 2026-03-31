"""
FastAPI endpoints for the Backstage Software Catalog integration.

Exposes three surfaces:

1. ``GET  /api/backstage/entities``
   Returns the full Entity Provider payload (all plugins as Backstage
   Component entities).  Designed to be polled by a Backstage Custom Entity
   Provider or a cron-based sync job.

2. ``GET  /api/backstage/entities/{plugin_name}``
   Returns the catalog-info.yaml payload for a single plugin.  Useful for
   per-plugin catalog-info.yaml generation during CI.

3. ``GET  /api/backstage/entities/{plugin_name}/patterns``
   Returns only the detected Agentic Design Pattern labels for a plugin.

Mount in lifespan.py after BackstageProvider is constructed:
    from core.plugins.exporters import backstage_exporter_router, set_backstage_provider
    set_backstage_provider(provider, registry)
    app.include_router(backstage_exporter_router)
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, status, Response

from core.middleware.security import require_admin_or_job
from .backstage_provider import BackstageProvider
from core.plugins.registry import PluginRegistry

router = APIRouter(prefix="/api/backstage", tags=["Backstage Integration"])

# Absolute path to the software template — resolved relative to this file so
# the endpoint works regardless of the working directory at startup.
_TEMPLATE_PATH = (
    Path(__file__).parents[3] / "templates" / "backstage" / "software-template.yaml"
)

# Global instances — set once at startup via set_backstage_provider()
_provider: Optional[BackstageProvider] = None
_registry: Optional[PluginRegistry] = None


def set_backstage_provider(
    provider: BackstageProvider, registry: PluginRegistry
) -> None:
    """
    Inject the BackstageProvider and PluginRegistry used by all endpoints.

    Call this once during application startup (e.g. inside lifespan.py)
    before the first request is served.
    """
    global _provider, _registry
    _provider = provider
    _registry = registry


def _get_provider() -> BackstageProvider:
    if _provider is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Backstage exporter not initialised — call set_backstage_provider() at startup.",
        )
    return _provider


def _get_registry() -> PluginRegistry:
    if _registry is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Plugin registry not available in Backstage exporter.",
        )
    return _registry


# ── Endpoints ─────────────────────────────────────────────────────────────────


@router.get(
    "/entities",
    response_model=Dict[str, Any],
    summary="Full Entity Provider payload",
    description=(
        "Returns all BaselithCore plugins as Backstage Component entities "
        "in the EntityProvider full-mutation format.  Poll this endpoint "
        "from a Backstage CustomEntityProvider or a scheduled sync job."
    ),
)
async def get_all_entities(
    _: str = Depends(require_admin_or_job),
) -> Dict[str, Any]:
    """
    Return the full Backstage Entity Provider payload for all plugins.

    Compatible with Backstage's EntityProvider.applyMutation() contract.
    Requires admin or job-level credentials.
    """
    provider = _get_provider()
    registry = _get_registry()
    return await provider.get_provider_payload(registry)


@router.get(
    "/entities/{plugin_name}",
    response_model=Dict[str, Any],
    summary="Single plugin catalog-info entity",
    description=(
        "Returns the Backstage Component entity dict for one plugin.  "
        "The response body is valid YAML-serialisable content for a "
        "catalog-info.yaml file."
    ),
)
async def get_entity(
    plugin_name: str,
    _: str = Depends(require_admin_or_job),
) -> Dict[str, Any]:
    """
    Return the Backstage catalog-info entity for a single plugin.
    Requires admin or job-level credentials.
    """
    provider = _get_provider()
    registry = _get_registry()

    plugin = registry.get(plugin_name)
    if plugin is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Plugin '{plugin_name}' not found in the registry.",
        )

    return await provider.export_entity(plugin)


@router.get(
    "/entities/{plugin_name}/patterns",
    response_model=List[str],
    summary="Detected Agentic Design Pattern labels",
    description=(
        "Returns the Backstage label keys for all Agentic Design Patterns "
        "detected in the plugin's source code, manifest tags, and resource "
        "declarations.  Cached after first call; invalidated on hot-reload."
    ),
)
async def get_plugin_patterns(
    plugin_name: str,
    _: str = Depends(require_admin_or_job),
) -> List[str]:
    """
    Return the detected Agentic Design Pattern labels for a plugin.
    Requires admin or job-level credentials.
    """
    provider = _get_provider()
    registry = _get_registry()

    plugin = registry.get(plugin_name)
    if plugin is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Plugin '{plugin_name}' not found in the registry.",
        )

    return await provider.detect_agentic_patterns(plugin)


@router.get(
    "/health",
    response_model=Dict[str, Any],
    summary="Backstage integration health",
    description="Returns the operational status of the Backstage exporter.",
)
async def get_backstage_health(
    _: str = Depends(require_admin_or_job),
) -> Dict[str, Any]:
    """
    Return a health summary for the Backstage integration module.
    Requires admin or job-level credentials.
    """
    registry = _get_registry()
    plugins = registry.get_all()

    return {
        "status": "ok",
        "exporter": "BackstageProvider",
        "registered_plugins": len(plugins),
        "entity_provider_endpoint": "/api/backstage/entities",
    }


@router.get(
    "/software-template.yaml",
    response_class=Response,
    summary="Backstage Software Template",
    description="Returns the standard Baselith plugin scaffolding template for Backstage.",
)
async def get_software_template(
    _: str = Depends(require_admin_or_job),
) -> Response:
    """
    Return the Backstage Software Template YAML.
    Requires admin or job-level credentials.
    """
    if not _TEMPLATE_PATH.exists():
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Software template not found in the framework.",
        )

    content = _TEMPLATE_PATH.read_text(encoding="utf-8")
    return Response(content=content, media_type="application/x-yaml")
