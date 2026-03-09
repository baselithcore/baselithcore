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

from core.config import get_app_config, get_security_config
from core.observability.logging import ensure_configured
from core.api.lifespan import lifespan

from core.middleware.observability import RequestIdMiddleware
from core.middleware.cost_control import CostControlMiddleware
from core.middleware.optimization import StaticCacheMiddleware, SmartGzipMiddleware
from core.middleware.security import SecurityHeadersMiddleware
from core.middleware.tenant import TenantMiddleware

from core.routers import chat, index, metrics, status, feedback, console
from core.routers.admin import router as admin_router
from core.routers.tenant import router as tenant_router

try:
    from plugins.marketplace.router import router as plugin_management_router
except (ImportError, ModuleNotFoundError):
    plugin_management_router = None


def create_app() -> FastAPI:
    """
    Factory function to create and configure the FastAPI application.
    """
    _app_config = get_app_config()
    _security_config = get_security_config()

    ALLOW_ORIGINS = _security_config.allow_origins
    ENABLE_FEEDBACK = _app_config.enable_feedback

    ensure_configured()

    app = FastAPI(
        title="Baselith-Core", lifespan=lifespan, default_response_class=ORJSONResponse
    )

    # === Request ID middleware to correlate logs/metrics ===
    app.add_middleware(RequestIdMiddleware)
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

    # === Middleware CORS (Last added = First executed) ===
    allow_origins_list = ALLOW_ORIGINS
    cors_params = {
        "allow_credentials": True,
        "allow_methods": ["*"],
        "allow_headers": [
            "Content-Type",
            "Authorization",
            "X-Requested-With",
            "X-Request-ID",
            "Accept",
            "Origin",
        ],
    }

    if "*" in allow_origins_list:
        cors_params["allow_origin_regex"] = ".*"
    else:
        cors_params["allow_origins"] = allow_origins_list

    app.add_middleware(CORSMiddleware, **cors_params)

    # === Tenant Middleware (Post-CORS, Pre-Route) ===
    app.add_middleware(TenantMiddleware)

    # === Serve static files (dashboard admin, css, js) ===
    app.mount("/static", StaticFiles(directory="core/static"), name="static")

    # === Routers ===
    app.include_router(chat.router)
    app.include_router(index.router)
    app.include_router(metrics.router)
    app.include_router(status.router)
    app.include_router(console.router)

    # === Plugin Management API ===
    if plugin_management_router:
        app.include_router(plugin_management_router)

    if ENABLE_FEEDBACK:
        app.include_router(feedback.router)
        app.include_router(admin_router)

    app.include_router(tenant_router)

    return app
