"""App metadata the factory attaches: the A2A agent card and OpenAPI auth schemes.

Split out of :mod:`baselith.core.api.factory` for the module size cap; the factory
re-exports both helpers under their old names.
"""

from __future__ import annotations

from typing import Any

from fastapi import FastAPI

from baselith.core._version import __version__
from baselith.core.a2a.agent_card import AgentCapabilities, AgentCard
from baselith.core.config import AppConfig


def _build_agent_card(app_config: AppConfig) -> AgentCard:
    """
    Build the A2A discovery card advertised at /.well-known/agent.json.

    Sourced from app config + the framework version so peer agents can
    discover this instance without bespoke integration.
    """
    return AgentCard(
        name=getattr(app_config, "app_name", "Baselith-Core"),
        description="BaselithCore orchestration engine for production agentic AI.",
        version=__version__,
        # The default app mounts only this discovery card, not the A2A
        # JSON-RPC endpoint (baselith.core.a2a.router.create_a2a_router), so it must not
        # promise message streaming a peer could not reach.
        agentCapabilities=AgentCapabilities(streaming=False),
    )


def _declare_security_schemes(app: FastAPI) -> None:
    """Advertise the credential types the auth dependencies actually accept.

    ``require_user`` / ``require_admin`` / ``require_admin_or_job`` read the
    ``X-API-Key`` header or an ``Authorization: Bearer`` token inside the
    dependency, so FastAPI cannot infer a scheme from the signature and Swagger
    UI would offer HTTP Basic only. Declaring both in
    ``components.securitySchemes`` makes them selectable in the Authorize dialog
    without asserting a per-operation requirement, so public routes stay public.
    """
    base_openapi = app.openapi

    def openapi_with_schemes() -> dict[str, Any]:
        schema = base_openapi()
        schemes = schema.setdefault("components", {}).setdefault("securitySchemes", {})
        schemes.setdefault(
            "ApiKeyAuth",
            {
                "type": "apiKey",
                "in": "header",
                "name": "X-API-Key",
                "description": (
                    "Operator-minted key from API_KEYS_USER / _ADMIN / _JOB / _SCOPED."
                ),
            },
        )
        schemes.setdefault(
            "BearerAuth",
            {
                "type": "http",
                "scheme": "bearer",
                "bearerFormat": "JWT",
                "description": "Access token issued by baselith.core.auth (AuthManager.create_token).",
            },
        )
        annotated: dict[str, Any] = schema
        return annotated

    app.openapi = openapi_with_schemes  # type: ignore[method-assign]


__all__ = ["_build_agent_card", "_declare_security_schemes"]
