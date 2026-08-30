"""
FastAPI Application Orchestration and Configuration.

Provides a centralized factory for constructing the high-performance
REST/WebSocket API. Configures a multi-layered middleware stack
(Security, Cost Control, Optimization) and registers modular routers
for chat, plugins, and system observability.
"""

from fastapi import Depends, FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import ORJSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.trustedhost import TrustedHostMiddleware

from core._version import __version__
from core.a2a.agent_card import AgentCapabilities, AgentCard
from core.a2a.router import create_wellknown_router
from core.api.lifespan import lifespan
from core.config import AppConfig, get_app_config, get_security_config
from core.middleware.cost_control import CostControlMiddleware
from core.middleware.csrf import CSRFOriginMiddleware
from core.middleware.http_metrics import HTTPMetricsMiddleware
from core.middleware.idempotency import IdempotencyMiddleware
from core.middleware.observability import RequestIdMiddleware
from core.middleware.optimization import SmartGzipMiddleware, StaticCacheMiddleware
from core.middleware.plugin_activation import PluginActivationMiddleware
from core.middleware.plugin_context import PluginContextMiddleware
from core.middleware.quota import QuotaMiddleware
from core.middleware.security import (
    RequestSizeLimitMiddleware,
    SecurityHeadersMiddleware,
    require_user,
)
from core.middleware.tenant import TenantMiddleware
from core.observability.logging import ensure_configured
from core.plugins import apply_plugin_app_middleware, backstage_exporter_router
from core.plugins.api import router as plugin_management_router
from core.routers import chat, console, feedback, index, metrics, status
from core.routers.admin import router as admin_router
from core.routers.tenant import router as tenant_router


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
    #
    # ``DOCS_ENABLED`` is the explicit override (true/false); unset means auto.
    # Auto additionally fails safe for the "smells like prod" shape: auth
    # enforced but ENVIRONMENT/APP_ENV never declared — a real production
    # deployment that forgot the env var would otherwise expose the schema
    # because the runtime environment silently defaults to "development".
    # Local development keeps docs by declaring the environment (or setting
    # DOCS_ENABLED=true, or running with AUTH_REQUIRED=false).
    import os as _os

    from core.config.environment import is_production_env

    _docs_override = _os.getenv("DOCS_ENABLED", "").lower()
    if _docs_override in ("1", "true", "yes", "on"):
        _docs_off = False
    elif _docs_override in ("0", "false", "no", "off"):
        _docs_off = True
    else:
        _env_declared = bool(_os.getenv("ENVIRONMENT") or _os.getenv("APP_ENV"))
        # ``getattr`` keeps the factory compatible with legacy test doubles
        # that stub the security config with a partial namespace (same rule as
        # max_request_size_bytes below).
        if getattr(_security_config, "auth_required", False) and not _env_declared:
            # Smells like prod: auth enforced but the environment was never
            # declared. Arm the global hardened posture so every production
            # gate (plugin signing, unsigned-A2A rejection, SSRF deny) fails
            # closed too — not just /docs. Declaring APP_ENV is the way out.
            from core.observability.logging import get_logger as _get_logger
            from core.utils.runtime_env import assume_production_when_undeclared

            assume_production_when_undeclared()
            _get_logger(__name__).warning(
                "AUTH_REQUIRED is on but APP_ENV/ENVIRONMENT is undeclared: "
                "assuming production posture (signed plugins enforced, "
                "unsigned A2A rejected, /docs off). Set APP_ENV=development "
                "to opt out locally."
            )
        _docs_off = is_production_env() or (
            getattr(_security_config, "auth_required", False) and not _env_declared
        )

    app = FastAPI(
        title="Baselith-Core",
        version=__version__,
        lifespan=lifespan,
        default_response_class=ORJSONResponse,
        docs_url=None if _docs_off else "/docs",
        redoc_url=None if _docs_off else "/redoc",
        openapi_url=None if _docs_off else "/openapi.json",
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
    # === Idempotency-Key replay for mutating requests (pure ASGI) ===
    # Added before Tenant/CORS so it runs *inside* them (tenant context is set)
    # and captures the fully-formed response; streaming responses pass through.
    app.add_middleware(IdempotencyMiddleware)
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
            "Idempotency-Key",
            "Accept",
            "Origin",
        ],
        "expose_headers": ["Idempotency-Replayed", "Retry-After"],
    }

    if use_wildcard:
        cors_params["allow_origins"] = ["*"]
    else:
        cors_params["allow_origins"] = allow_origins_list

    app.add_middleware(CORSMiddleware, **cors_params)

    # === Tenant Middleware (Post-CORS, Pre-Route) ===
    app.add_middleware(TenantMiddleware)

    # === Plugin context: attribute each request to its owning plugin ===
    # Path-derived (router prefix / sub-app mount), so downstream seams — e.g.
    # the central per-plugin LLM policy — know which plugin a call runs for.
    app.add_middleware(PluginContextMiddleware)

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

    # === Perimeter guards: Host + CSRF Origin validation (pure ASGI) ===
    # Registered here (outer to Quota/Idempotency/CORS/Tenant/plugin layers, but
    # inner to RequestSizeLimit + SecurityHeaders) so a spoofed-Host or
    # CSRF-failing request is rejected by a single cheap header compare *before*
    # it can consume a quota unit, take an Idempotency lock, or match a plugin
    # route. Added CSRF-then-TrustedHost so TrustedHost runs outermost of the
    # two, and both stay inside SecurityHeaders so their 400/403s carry CSP/HSTS.
    app.add_middleware(CSRFOriginMiddleware, allow_origins=ALLOW_ORIGINS)
    if TRUSTED_HOSTS:
        app.add_middleware(TrustedHostMiddleware, allowed_hosts=TRUSTED_HOSTS)

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
    # === Security headers (configurable CSP/HSTS) ===
    # Registered near-outermost (inside only RequestId/HTTPMetrics, which never
    # short-circuit) so CSP/HSTS/nosniff also land on responses emitted by the
    # inner guards — TrustedHost 400s, CSRF 403s, 413s and CORS preflights —
    # not just on responses that reach the routes.
    app.add_middleware(SecurityHeadersMiddleware)
    # === Request ID middleware to correlate logs/metrics ===
    # Registered LAST = outermost, so every response — including short-
    # circuited errors from quota/CSRF/TrustedHost layers — carries an
    # X-Request-ID and every inner log line can bind it.
    app.add_middleware(RequestIdMiddleware)

    # === HTTP RED metrics (Rate/Errors/Duration) ===
    # Outermost = measures true end-to-end request latency and captures every
    # response status (including short-circuits from inner guards). Pure ASGI;
    # reads the matched route template from the scope the router mutates in
    # place, so the ``route`` label stays low-cardinality. Emits to the same
    # Prometheus registry exposed at ``/metrics``.
    app.add_middleware(HTTPMetricsMiddleware)

    # === Serve static files (dashboard admin, css, js) ===
    app.mount("/static", StaticFiles(directory="core/static"), name="static")

    @app.get(
        "/api/plugins/frontend-manifest",
        dependencies=[Depends(require_user)],
    )
    async def get_frontend_manifest():
        """Return manifest of all plugin frontend assets for injection.

        Auth-gated: the manifest enumerates installed plugins and their asset
        paths — free recon for an anonymous caller, and every other
        plugin-metadata route already requires auth.
        """
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

    # === MCP Streamable HTTP transport (opt-in, spec 2025-06-18) ===
    from core.config import get_mcp_config

    if get_mcp_config().mcp_http_transport_enabled:
        from core.mcp.http_transport import create_mcp_http_router
        from core.mcp.tools import create_mcp_server_with_tools
        from core.orchestration.autonomy import AutonomyPolicy

        # Fail-closed autonomy gate: HTTP carries no human-approval channel,
        # so side-effecting tool categories are rejected at the default
        # (SUPERVISED) level instead of executing unsupervised.
        mcp_server = create_mcp_server_with_tools(autonomy_policy=AutonomyPolicy())
        app.include_router(create_mcp_http_router(mcp_server))

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
