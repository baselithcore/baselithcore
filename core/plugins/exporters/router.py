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

import hashlib
import json
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from core.marketplace.publisher import PluginPublisher
from core.middleware.security import require_admin_or_job
from core.plugins.registry import PluginRegistry

from .backstage_provider import BackstageProvider

router = APIRouter(prefix="/api/backstage", tags=["Backstage Integration"])

# Absolute paths to the Scaffolder templates — resolved relative to this file
# so endpoints work regardless of the working directory at startup.
_TEMPLATE_PATH = (
    Path(__file__).parents[3] / "templates" / "backstage" / "software-template.yaml"
)
_PUBLISH_TEMPLATE_PATH = (
    Path(__file__).parents[3] / "templates" / "backstage" / "publish-template.yaml"
)

# Global instances — set once at startup via set_backstage_provider()
_provider: BackstageProvider | None = None
_registry: PluginRegistry | None = None


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
    summary="Full Entity Provider payload",
    description=(
        "Returns the complete Backstage entity graph (System + Components + "
        "APIs) in the EntityProvider full-mutation format.  Poll this "
        "endpoint from a Backstage CustomEntityProvider or a scheduled sync "
        "job; it supports conditional requests via ETag / If-None-Match so "
        "an unchanged catalog costs a 304 instead of a full body."
    ),
)
async def get_all_entities(
    request: Request,
    _: str = Depends(require_admin_or_job),
) -> Response:
    """
    Return the full Backstage Entity Provider payload for all plugins.

    Compatible with Backstage's EntityProvider.applyMutation() contract.
    Emits a weak ETag over the canonical payload; a matching
    ``If-None-Match`` yields ``304 Not Modified``.  Requires admin or
    job-level credentials.
    """
    provider = _get_provider()
    registry = _get_registry()
    # Pass the host app's routes so mounted-sub-app plugins (wikigen, docheck,
    # …) also export an API entity built from their own OpenAPI (see .mounts).
    payload = await provider.get_provider_payload(registry, routes=request.app.routes)

    digest = hashlib.sha256(
        json.dumps(payload, sort_keys=True, default=str).encode("utf-8")
    ).hexdigest()
    etag = f'W/"{digest[:32]}"'
    if request.headers.get("if-none-match") == etag:
        return Response(
            status_code=status.HTTP_304_NOT_MODIFIED, headers={"ETag": etag}
        )
    return JSONResponse(content=payload, headers={"ETag": etag})


@router.get(
    "/entities/{plugin_name}",
    response_model=dict[str, Any],
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
) -> dict[str, Any]:
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
    response_model=list[str],
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
) -> list[str]:
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
    response_model=dict[str, Any],
    summary="Backstage integration health",
    description="Returns the operational status of the Backstage exporter.",
)
async def get_backstage_health(
    _: str = Depends(require_admin_or_job),
) -> dict[str, Any]:
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


@router.get(
    "/publish-template.yaml",
    response_class=Response,
    summary="Backstage Publish Template",
    description=(
        "Returns the Backstage Scaffolder template that submits an existing "
        "plugin directly to the Baselith Marketplace hub."
    ),
)
async def get_publish_template(
    _: str = Depends(require_admin_or_job),
) -> Response:
    """Return the Backstage publish-template YAML."""
    if not _PUBLISH_TEMPLATE_PATH.exists():
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Publish template not found in the framework.",
        )

    content = _PUBLISH_TEMPLATE_PATH.read_text(encoding="utf-8")
    return Response(content=content, media_type="application/x-yaml")


class PublishRequest(BaseModel):
    """Body for ``POST /api/backstage/publish``."""

    plugin_path: str = Field(
        ...,
        min_length=1,
        max_length=4096,
        description=(
            "Absolute path (on the framework host) to the plugin directory "
            "to zip and submit. Typically mounted from the Backstage "
            "Scaffolder workspace."
        ),
    )
    auth_token: str | None = Field(
        default=None,
        description=(
            "Pre-issued JWT session token for the marketplace hub. "
            "Preferred: use ``github_token`` instead so the marketplace "
            "hub owns the exchange flow."
        ),
    )
    github_token: str | None = Field(
        default=None,
        description=(
            "GitHub OAuth access token for the submitting user. The "
            "framework exchanges it against the marketplace's "
            "``/auth/github/exchange`` endpoint to obtain a JWT, matching "
            "the browser login flow (``/auth/login/github``). Typically "
            "forwarded by the Backstage Scaffolder via "
            "``${{ secrets.USER_OAUTH_TOKEN }}``."
        ),
    )
    admin_key: str | None = Field(
        default=None,
        description="Legacy admin API key.",
    )
    registry_url: str | None = Field(
        default=None,
        description=(
            "Deprecated and ignored. Both the token exchange and the "
            "submission always target the framework's "
            "OFFICIAL_MARKETPLACE_URL; accepting a caller-supplied hub URL "
            "would let a job-role key redirect the forwarded GitHub token "
            "(SSRF / credential exfiltration)."
        ),
    )


@router.post(
    "/publish",
    summary="Submit a plugin directly to the marketplace hub",
    description=(
        "Thin wrapper around ``PluginPublisher.publish`` so Backstage "
        "Scaffolder steps can submit a plugin bundle without shelling out "
        "to the ``baselith`` CLI. The plugin must live on the framework "
        "host; the Scaffolder is responsible for staging the source via "
        "``fetch:plain`` + ``fetch:template`` before calling this "
        "endpoint."
    ),
)
async def submit_to_marketplace(
    body: PublishRequest,
    _: str = Depends(require_admin_or_job),
) -> dict[str, Any]:
    """Validate + zip + POST the plugin to the marketplace hub."""
    if not (body.auth_token or body.admin_key or body.github_token):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="github_token, auth_token, or admin_key is required",
        )

    _enforce_publish_workspace_root(body.plugin_path)

    auth_token = body.auth_token
    if not auth_token and body.github_token:
        auth_token = await _exchange_github_for_jwt(github_token=body.github_token)

    publisher = PluginPublisher()
    result = await publisher.publish(
        plugin_path=body.plugin_path,
        admin_key=body.admin_key,
        auth_token=auth_token,
        registry_url=body.registry_url,
    )
    if result.get("status") == "error":
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=result)
    return result


def _enforce_publish_workspace_root(plugin_path: str) -> None:
    """Reject plugin paths outside the configured Scaffolder workspace.

    Opt-in via ``PLUGIN_PUBLISH_WORKSPACE_ROOT``: when unset any host path is
    accepted (legacy behavior); when set, packaging is confined to that root
    so a job-role key cannot point the publisher at arbitrary host
    directories. ``resolve()`` collapses ``..`` and symlinks before the
    containment check.
    """
    from core.config import get_plugin_config

    root = get_plugin_config().publish_workspace_root
    if root is None:
        return
    resolved = Path(plugin_path).resolve()
    if not resolved.is_relative_to(root.resolve()):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=("plugin_path is outside the configured publish workspace root"),
        )


async def _exchange_github_for_jwt(*, github_token: str) -> str:
    """Exchange a GitHub OAuth access token for a marketplace JWT.

    Always hits ``{OFFICIAL_MARKETPLACE_URL}/auth/github/exchange`` — the
    hub URL is deliberately not caller-overridable because the request
    forwards the user's GitHub token (see ``PublishRequest.registry_url``).
    The marketplace validates the GH token via the GitHub REST API and
    issues a JWT bound to the user's GitHub login — identical identity
    model to the interactive ``/auth/login/github`` flow.
    """
    import httpx

    from core.config import get_plugin_config

    base = get_plugin_config().OFFICIAL_MARKETPLACE_URL
    url = f"{base.rstrip('/')}/auth/github/exchange"

    async with httpx.AsyncClient(timeout=15.0) as client:
        try:
            resp = await client.post(url, json={"access_token": github_token})
        except httpx.HTTPError as exc:
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail=f"marketplace auth exchange failed: {exc}",
            ) from exc

    if resp.status_code != 200:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={
                "error": "github_exchange_failed",
                "status": resp.status_code,
                "body": resp.text[:512],
            },
        )
    payload = resp.json()
    token = payload.get("access_token") or payload.get("token")
    if not token:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="marketplace exchange did not return a token",
        )
    return token
