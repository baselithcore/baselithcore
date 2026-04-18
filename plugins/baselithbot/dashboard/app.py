"""Dashboard router assembly — ``create_dashboard_router`` composition."""

from __future__ import annotations

from typing import TYPE_CHECKING

from fastapi import APIRouter, Request

from ..policies import DashboardAuth, RateLimiter
from .routes import (
    register_agents_routes,
    register_approvals_routes,
    register_audit_routes,
    register_canvas_routes,
    register_channels_routes,
    register_computer_use_routes,
    register_diagnostics_routes,
    register_events_routes,
    register_models_routes,
    register_provider_keys_routes,
    register_registry_routes,
    register_run_task_routes,
    register_session_routes,
    register_stealth_routes,
    register_workspaces_routes,
)

if TYPE_CHECKING:
    from ..plugin import BaselithbotPlugin


def create_dashboard_router(
    plugin: "BaselithbotPlugin",
    auth: DashboardAuth | None = None,
) -> APIRouter:
    """Create the REST+SSE router powering the dashboard UI.

    Args:
        plugin: Owning ``BaselithbotPlugin`` instance (state source).
        auth: Optional bearer-token guard. When provided, every *write*
            endpoint requires the token; read-only endpoints stay open.
    """
    effective_auth: DashboardAuth = auth or DashboardAuth()
    router = APIRouter(prefix="/dash", tags=["Baselithbot Dashboard"])

    # Per-router rate limiters — keeps state scoped so tests and multi-mount
    # deployments do not bleed counters into each other.
    session_rate_limit = RateLimiter(window_seconds=60.0, max_events=30)
    token_rate_limit = RateLimiter(window_seconds=60.0, max_events=5)
    delete_rate_limit = RateLimiter(window_seconds=60.0, max_events=20)

    def _guard(request: Request) -> None:
        effective_auth.check(request)

    register_diagnostics_routes(router, plugin)
    register_agents_routes(
        router,
        plugin,
        guard=_guard,
        token_rate_limit=token_rate_limit,
        delete_rate_limit=delete_rate_limit,
    )
    register_session_routes(
        router,
        plugin,
        guard=_guard,
        session_rate_limit=session_rate_limit,
        delete_rate_limit=delete_rate_limit,
    )
    register_registry_routes(
        router,
        plugin,
        guard=_guard,
        token_rate_limit=token_rate_limit,
        delete_rate_limit=delete_rate_limit,
    )
    register_channels_routes(
        router,
        plugin,
        guard=_guard,
        token_rate_limit=token_rate_limit,
        delete_rate_limit=delete_rate_limit,
    )
    register_run_task_routes(router, plugin)
    register_models_routes(
        router,
        plugin,
        guard=_guard,
        token_rate_limit=token_rate_limit,
    )
    register_provider_keys_routes(
        router,
        plugin,
        guard=_guard,
        token_rate_limit=token_rate_limit,
        delete_rate_limit=delete_rate_limit,
    )
    register_workspaces_routes(
        router,
        plugin,
        guard=_guard,
        token_rate_limit=token_rate_limit,
        delete_rate_limit=delete_rate_limit,
    )
    register_canvas_routes(
        router,
        plugin,
        guard=_guard,
        token_rate_limit=token_rate_limit,
    )
    register_computer_use_routes(
        router,
        plugin,
        guard=_guard,
        token_rate_limit=token_rate_limit,
    )
    register_stealth_routes(
        router,
        plugin,
        guard=_guard,
        token_rate_limit=token_rate_limit,
    )
    register_audit_routes(router, plugin)
    register_approvals_routes(
        router,
        plugin,
        guard=_guard,
        token_rate_limit=token_rate_limit,
    )
    register_events_routes(router)

    return router


__all__ = ["create_dashboard_router"]
