"""
FastAPI Application Orchestration and Configuration.

Provides a centralized factory for constructing the high-performance
REST/WebSocket API. Configures a multi-layered middleware stack
(Security, Cost Control, Optimization) and registers modular routers
for chat, plugins, and system observability.
"""

from fastapi import FastAPI
from fastapi.responses import ORJSONResponse
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from starlette.middleware.trustedhost import TrustedHostMiddleware

from core.config import AppConfig, get_app_config, get_security_config
from core.observability.logging import ensure_configured
from core.api.lifespan import lifespan

from core.middleware.observability import RequestIdMiddleware
from core.middleware.cost_control import CostControlMiddleware
from core.middleware.csrf import CSRFOriginMiddleware
from core.middleware.optimization import StaticCacheMiddleware, SmartGzipMiddleware
from core.middleware.plugin_activation import PluginActivationMiddleware
from core.middleware.security import (
    RequestSizeLimitMiddleware,
    SecurityHeadersMiddleware,
)
from core.middleware.quota import QuotaMiddleware
from core.middleware.tenant import TenantMiddleware

from core.routers import chat, index, metrics, status, feedback, console
from core.routers.admin import router as admin_router
from core.routers.tenant import router as tenant_router

from core.plugins.api import router as plugin_management_router
from core.plugins import backstage_exporter_router, apply_plugin_app_middleware

from core._version import __version__
from core.a2a.agent_card import AgentCard, AgentCapabilities
from core.a2a.router import create_wellknown_router


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

    # Disable the interactive API docs in production: /docs, /redoc and the raw
    # OpenAPI schema disclose every route/param/model (including admin, webhooks,
    # privacy) to anonymous callers. Kept on outside production for DX.
    from core.config.environment import is_production_env

    _prod = is_production_env()

    app = FastAPI(
        title="Baselith-Core",
        lifespan=lifespan,
        default_response_class=ORJSONResponse,
        docs_url=None if _prod else "/docs",
        redoc_url=None if _prod else "/redoc",
        openapi_url=None if _prod else "/openapi.json",
    )

    # NOTE on ordering: Starlette executes middleware in REVERSE registration
    # order (last added = outermost). Request-ID and the body-size limit are
    # therefore registered LAST, at the end of this factory, so they wrap
    # every other layer.

    # === Cost Control Middleware (Phase 1) ===
    app.add_middleware(CostControlMiddleware)

    # === Cache-Control for static assets/console ===
    app.add_middleware(StaticCacheMiddleware, max_age=86400)
    # === Smart Gzip Compression (skip streaming) ===
    # Both the unprefixed path and the /v1 alias must be excluded: gzip has
    # no per-chunk flush, so a buffered stream breaks token-by-token output.
    app.add_middleware(
        SmartGzipMiddleware,
        minimum_size=500,
        excluded_paths=["/chat/stream", "/v1/chat/stream"],
    )
    # === Security headers (configurable CSP/HSTS) ===
    app.add_middleware(SecurityHeadersMiddleware)
    # === Host header validation behind reverse proxy/load balancer ===
    if TRUSTED_HOSTS:
        app.add_middleware(TrustedHostMiddleware, allowed_hosts=TRUSTED_HOSTS)

    # === CSRF Origin validation for state-changing requests (pure ASGI) ===
    app.add_middleware(CSRFOriginMiddleware, allow_origins=ALLOW_ORIGINS)
    # === Lazy plugin activation on first request (pure ASGI) ===
    app.add_middleware(PluginActivationMiddleware)

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

    # === Usage-quota enforcement (no-op unless QUOTAS_ENABLED; self-authenticating) ===
    app.add_middleware(QuotaMiddleware)

    # === Plugin app-level middleware composition ===
    # Runs synchronously here so the Starlette stack is finalised before the
    # lifespan starts. Plugins opt in by overriding ``Plugin.setup_app_middleware``;
    # the default is a no-op. Best-effort: a failing plugin never blocks boot.
    try:
        apply_plugin_app_middleware(app)
    except Exception as exc:  # pragma: no cover — defensive
        from core.observability.logging import get_logger as _get_logger

        _get_logger(__name__).warning("Plugin app-middleware discovery failed: %s", exc)

    # === Request body size limit (DoS protection) ===
    # Registered second-to-last = second-outermost: oversized bodies are
    # rejected before any other middleware (auth, quotas, gzip) does work.
    # ``getattr`` keeps the factory compatible with legacy test doubles that
    # stub ``get_security_config`` with a partial namespace; falls back to a
    # 10 MiB default that matches the config.
    app.add_middleware(
        RequestSizeLimitMiddleware,
        max_bytes=getattr(_security_config, "max_request_size_bytes", 10 * 1024 * 1024),
    )
    # === Request ID middleware to correlate logs/metrics ===
    # Registered LAST = outermost, so every response — including short-
    # circuited errors from quota/CSRF/TrustedHost layers — carries an
    # X-Request-ID and every inner log line can bind it.
    app.add_middleware(RequestIdMiddleware)

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

    # === Versioned API aliases (additive) ===
    # Mount the data routers a second time under /v1 while keeping the original
    # unprefixed paths live, so existing clients are unaffected and new clients
    # can pin to a stable version. HTML/admin/discovery routers stay unprefixed.
    import os

    if os.getenv("API_V1_ENABLED", "true").strip().lower() in ("1", "true", "yes"):
        app.include_router(chat.router, prefix="/v1")
        app.include_router(index.router, prefix="/v1")
        app.include_router(metrics.router, prefix="/v1")
        app.include_router(status.router, prefix="/v1")
        if ENABLE_FEEDBACK:
            app.include_router(feedback.router, prefix="/v1")
        app.include_router(tenant_router, prefix="/v1")

    # === Standardized error envelope (additive: only BaselithError + catch-all) ===
    from core.api.errors import install_error_handlers

    install_error_handlers(app)

    return app
