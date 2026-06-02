"""
FastAPI Application Orchestration and Configuration.

Provides a centralized factory for constructing the high-performance
REST/WebSocket API. Configures a multi-layered middleware stack
(Security, Cost Control, Optimization) and registers modular routers
for chat, plugins, and system observability.
"""

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, ORJSONResponse
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from starlette.middleware.trustedhost import TrustedHostMiddleware

from core.config import AppConfig, get_app_config, get_security_config
from core.observability.logging import ensure_configured
from core.api.lifespan import lifespan

from core.middleware.observability import RequestIdMiddleware
from core.middleware.cost_control import CostControlMiddleware
from core.middleware.optimization import StaticCacheMiddleware, SmartGzipMiddleware
from core.middleware.security import (
    RequestSizeLimitMiddleware,
    SecurityHeadersMiddleware,
)
from core.middleware.tenant import TenantMiddleware

from core.routers import chat, index, metrics, status, feedback, console
from core.routers.admin import router as admin_router
from core.routers.tenant import router as tenant_router

from core.plugins.api import router as plugin_management_router
from core.plugins import backstage_exporter_router

from core._version import __version__
from core.a2a.agent_card import AgentCard, AgentCapabilities
from core.a2a.router import create_wellknown_router


_STATE_CHANGING_METHODS = {"POST", "PUT", "DELETE", "PATCH"}


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
        agentCapabilities=AgentCapabilities(streaming=True),
    )


def _build_csrf_middleware(allow_origins: list[str]):
    """
    Return a Starlette-compatible middleware that validates the Origin header
    on state-changing requests.

    Rationale: the main API uses Bearer / API-key auth (not browser cookies),
    so CSRF only matters for the admin endpoints that rely on HTTP Basic Auth.
    Browsers automatically include Basic Auth credentials on same-origin
    requests; rejecting cross-origin state-changing requests without an
    allowed Origin prevents CSRF on those endpoints.

    Requests without an Origin header (e.g. direct curl calls, server-to-
    server) are passed through — they cannot be forged by a malicious page.
    """

    async def csrf_middleware(request: Request, call_next):
        if request.method in _STATE_CHANGING_METHODS:
            origin = request.headers.get("origin")
            if origin:
                wildcard = "*" in allow_origins
                if not wildcard and origin not in allow_origins:
                    return JSONResponse(
                        status_code=403,
                        content={"detail": "CSRF check failed: origin not allowed."},
                    )
        return await call_next(request)

    return csrf_middleware


def _build_plugin_activation_middleware(app: FastAPI):
    """
    Return middleware that activates lazy plugins on first matching request.

    The middleware is registered during app construction so Starlette's
    middleware stack remains immutable after startup. The plugin registry
    itself is populated later during lifespan startup and read from app state.
    """

    async def plugin_activation_middleware(request: Request, call_next):
        plugin_registry = getattr(app.state, "plugin_registry", None)
        if plugin_registry is None:
            return await call_next(request)

        plugin_name = plugin_registry.match_plugin_route(request.url.path)
        if not plugin_name:
            return await call_next(request)

        try:
            activated = await plugin_registry.ensure_plugin_active(plugin_name)
        except RuntimeError:
            return JSONResponse(
                status_code=503,
                content={"detail": "Plugin system is not ready yet."},
            )

        if not activated:
            return JSONResponse(
                status_code=503,
                content={"detail": f"Plugin '{plugin_name}' failed to activate."},
            )

        return await call_next(request)

    return plugin_activation_middleware


def create_app() -> FastAPI:
    """
    Factory function to create and configure the FastAPI application.
    """
    _app_config = get_app_config()
    _security_config = get_security_config()

    ALLOW_ORIGINS = _security_config.allow_origins
    TRUSTED_HOSTS = _security_config.trusted_hosts
    ENABLE_FEEDBACK = _app_config.enable_feedback

    ensure_configured()

    app = FastAPI(
        title="Baselith-Core", lifespan=lifespan, default_response_class=ORJSONResponse
    )

    # === Request ID middleware to correlate logs/metrics ===
    app.add_middleware(RequestIdMiddleware)
    # === Request body size limit (DoS protection) ===
    # Added early so oversized bodies are rejected before any other middleware
    # parses or buffers them. ``getattr`` keeps the factory compatible with
    # legacy test doubles that stub ``get_security_config`` with a partial
    # namespace; falls back to a 10 MiB default that matches the config.
    app.add_middleware(
        RequestSizeLimitMiddleware,
        max_bytes=getattr(_security_config, "max_request_size_bytes", 10 * 1024 * 1024),
    )
    # === Cost Control Middleware (Phase 1) ===
    app.add_middleware(CostControlMiddleware)

    # === Cache-Control for static assets/console ===
    app.add_middleware(StaticCacheMiddleware, max_age=86400)
    # === Smart Gzip Compression (skip streaming) ===
    app.add_middleware(
        SmartGzipMiddleware, minimum_size=500, excluded_paths=["/chat/stream"]
    )
    # === Security headers (configurable CSP/HSTS) ===
    app.add_middleware(SecurityHeadersMiddleware)
    # === Host header validation behind reverse proxy/load balancer ===
    if TRUSTED_HOSTS:
        app.add_middleware(TrustedHostMiddleware, allowed_hosts=TRUSTED_HOSTS)

    # === CSRF Origin validation for state-changing requests ===
    app.middleware("http")(_build_csrf_middleware(ALLOW_ORIGINS))
    # === Lazy plugin activation on first request ===
    app.middleware("http")(_build_plugin_activation_middleware(app))

    # === Middleware CORS (Last added = First executed) ===
    allow_origins_list = ALLOW_ORIGINS
    # Standard CORS convention: credentials cannot be used with wildcard origins.
    # We allow credentials for specific listed origins, but disable them for '*'.
    use_wildcard = "*" in allow_origins_list

    cors_params = {
        "allow_credentials": not use_wildcard,
        "allow_methods": ["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
        "allow_headers": [
            "Content-Type",
            "Authorization",
            "X-Requested-With",
            "X-Request-ID",
            "Accept",
            "Origin",
        ],
    }

    if use_wildcard:
        cors_params["allow_origins"] = ["*"]
    else:
        cors_params["allow_origins"] = allow_origins_list

    app.add_middleware(CORSMiddleware, **cors_params)

    # === Tenant Middleware (Post-CORS, Pre-Route) ===
    app.add_middleware(TenantMiddleware)

    # === Serve static files (dashboard admin, css, js) ===
    app.mount("/static", StaticFiles(directory="core/static"), name="static")

    @app.get("/api/plugins/frontend-manifest")
    async def get_frontend_manifest():
        """Return manifest of all plugin frontend assets for injection."""
        plugin_registry = getattr(app.state, "plugin_registry", None)
        if plugin_registry is None:
            return {"plugins": {}}
        return plugin_registry.get_frontend_manifest()

    # === Routers ===
    app.include_router(chat.router)
    app.include_router(index.router)
    app.include_router(metrics.router)
    app.include_router(status.router)
    app.include_router(console.router)

    # === Plugin Management API ===
    if plugin_management_router:
        app.include_router(plugin_management_router)

    # === Backstage Exporter API ===
    app.include_router(backstage_exporter_router)

    # === A2A discovery (/.well-known/agent.json) ===
    app.include_router(create_wellknown_router(_build_agent_card(_app_config)))

    if ENABLE_FEEDBACK:
        app.include_router(feedback.router)
        app.include_router(admin_router)

    app.include_router(tenant_router)

    return app
