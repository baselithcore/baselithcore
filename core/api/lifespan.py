"""
API Lifespan Management.

Handles startup and shutdown logic for the FastAPI application, including
resource initialization, plugin loading, and rate limiter setup.
"""

import asyncio
import logging
import os
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import yaml
from fastapi import FastAPI

from core.observability.logging import get_logger

try:
    from fastapi_limiter import FastAPILimiter

    FASTAPI_LIMITER_AVAILABLE = True
except ImportError:
    FastAPILimiter = None  # type: ignore[assignment, misc]
    FASTAPI_LIMITER_AVAILABLE = False
    logging.warning("⚠️ fastapi-limiter not available - rate limiting will be disabled")

import redis.asyncio as redis

from core.api.startup_checks import (
    run_startup_health_checks,
    start_retention_scheduler,
    stop_retention_scheduler,
    warm_auth_singletons,
)
from core.config import get_app_config, get_storage_config
from core.plugins import PluginLoader, PluginRegistry
from core.services.bootstrap import bootstrapper, ensure_startup_bootstrap

logger = get_logger(__name__)

_app_config = get_app_config()
_storage_config = get_storage_config()

INDEX_BOOTSTRAP_BACKGROUND = getattr(_app_config, "index_bootstrap_background", False)
POSTGRES_ENABLED = getattr(_storage_config, "postgres_enabled", False)
CACHE_REDIS_URL = getattr(_storage_config, "cache_redis_url", "")

# Strong references to fire-and-forget startup tasks so the event loop cannot
# garbage-collect them before they finish (see RUF006 / asyncio docs).
_BACKGROUND_TASKS: set[asyncio.Task[Any]] = set()


@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    FastAPI Lifecycle:
    - initializes DB
    - creates Qdrant collection
    - indexes documents (full scan on first startup)
    """
    logger.info(
        "🚀 Starting FastAPI lifecycle (postgres=%s).",
        "on" if POSTGRES_ENABLED else "off",
    )

    # Setup OpenTelemetry tracing + metrics (centralized in observability.otel:
    # rich resource, sampling, OTLP traces/metrics, propagators, shutdown).
    if getattr(_app_config, "telemetry_enabled", False):
        logger.info("📊 Initializing OpenTelemetry...")
        try:
            from core.observability.otel import setup_telemetry

            if setup_telemetry(
                service_name="baselith-core",
                otlp_endpoint=getattr(_app_config, "telemetry_otel_endpoint", None),
            ):
                logger.info("📊 OpenTelemetry initialized")
            else:
                logger.info("📊 OpenTelemetry inactive (SDK unavailable)")
        except Exception as e:
            logger.warning(f"📊 OpenTelemetry initialization skipped: {e}")
    else:
        logger.info("📊 OpenTelemetry disabled by configuration")

    # Setup Sentry Tracking
    try:
        from core.observability.sentry import init_sentry

        init_sentry()
    except ImportError:
        logger.warning("📊 Sentry initialization skipped: module not found.")

    logger.info(
        "⏩ Skipping eager service initialization (using lazy loading based on plugin requirements)"
    )

    # Import ServiceRegistry early so it's always available
    from core.di import ServiceRegistry

    # === LAZY LOADING: Analyze plugin requirements first ===
    logger.info("🔌 Initializing plugin system with lazy loading...")

    plugin_configs: dict[str, Any] = {}

    try:
        raw_config_path = os.environ.get("PLUGIN_CONFIG_PATH", "configs/plugins.yaml")
        config_path = Path(raw_config_path).resolve()
        cwd = Path.cwd().resolve()
        if not config_path.is_relative_to(cwd):
            raise ValueError(
                f"PLUGIN_CONFIG_PATH must resolve inside {cwd}; got {config_path}"
            )
        if config_path.exists():
            with open(config_path) as f:
                plugin_configs = yaml.safe_load(f) or {}
            logger.info(f"📄 Loaded plugin configurations from {config_path}")
        else:
            logger.warning(f"⚠️ Plugin configuration file not found: {config_path}")
    except Exception as e:
        logger.error(f"❌ Failed to load plugin configurations: {e}")

    analyzer = None
    try:
        from core.bootstrap.lazy_init import RESOURCE_FACTORIES
        from core.di.lazy_registry import get_lazy_registry
        from core.plugins.resource_analyzer import ResourceAnalyzer

        analyzer = ResourceAnalyzer(Path("plugins/"))
        resource_requirements = analyzer.analyze_requirements(plugin_configs)
        required_resources = resource_requirements["required"]
        optional_resources = resource_requirements["optional"]

        lazy_registry = get_lazy_registry()

        all_resources = required_resources | optional_resources
        init_order = analyzer.get_resource_init_order(all_resources)

        logger.info(f"📋 Resource initialization order: {init_order}")

        for resource in init_order:
            if resource in RESOURCE_FACTORIES:
                factory = RESOURCE_FACTORIES[resource]
                lazy_registry.register_factory(resource, factory)
                logger.debug(f"Registered lazy factory for: {resource}")

        core_resources_initialized = set()

        if "postgres" in required_resources:
            logger.info("🗄️ Initializing Postgres (required by plugins)...")
            core_storage: Any = await lazy_registry.get_or_create("postgres")
            app.state.core_storage = core_storage
            core_resources_initialized.add("postgres")

        if "vectorstore" in required_resources:
            logger.info("📦 Initializing Qdrant (required by plugins)...")
            vectorstore_service: Any = await lazy_registry.get_or_create("vectorstore")
            core_resources_initialized.add("vectorstore")

        from core.interfaces.services import LLMServiceProtocol, VectorStoreProtocol

        if "llm" in required_resources or "llm" in optional_resources:

            async def get_llm():
                """
                Dependency for retrieving the LLM service instance.

                Returns:
                    The LLM service instance from app state.
                """
                return await lazy_registry.get_or_create("llm")

            ServiceRegistry.register(LLMServiceProtocol, get_llm)  # type: ignore[type-abstract]
            logger.debug("Registered LLM service (lazy)")

        if "vectorstore" in core_resources_initialized:
            ServiceRegistry.register(VectorStoreProtocol, vectorstore_service)  # type: ignore[type-abstract]
            logger.debug("Registered VectorStore service (eager)")

    except ImportError as e:
        logger.warning(f"Feature not fully available: {e}")
        required_resources = set()
        optional_resources = set()

    from core.plugins import (
        BackstageProvider,
        HotReloadController,
        PluginLifecycleManager,
        set_backstage_provider,
        set_hot_reload_controller,
    )

    lifecycle_manager = PluginLifecycleManager()
    plugin_registry = PluginRegistry()
    ServiceRegistry.register(PluginRegistry, plugin_registry)
    plugin_loader = PluginLoader(
        Path("plugins/"), plugin_registry, lifecycle_manager=lifecycle_manager
    )

    hot_reload_controller = HotReloadController(
        plugin_loader, plugin_registry, lifecycle_manager
    )

    set_hot_reload_controller(hot_reload_controller)
    logger.info("🔄 Hot-reload controller initialized")

    # === Backstage Software Catalog integration ===
    _backstage_base_url = os.environ.get(
        "BASELITH_BASE_URL", f"http://localhost:{_app_config.port}"
    )
    # No fake default: an unconfigured docs site must yield NO Documentation
    # links in the catalog (a link to a non-resolving host is a broken link).
    _backstage_docs_url = os.environ.get("BASELITH_DOCS_URL")
    _backstage_source_location = os.environ.get(
        "BASELITH_CATALOG_SOURCE_LOCATION",
        "url:https://github.com/baselith/core/blob/main/",
    )
    # Optional human-facing "Manage Plugin" link ({plugin} placeholder), e.g.
    # http://<host>:8000/baselithcontrol/#/plugin/{plugin} on deployments that
    # ship a control-plane UI.
    _backstage_plugin_link = os.environ.get("BASELITH_PLUGIN_LINK_TEMPLATE")
    # When set to the repo root on the portal backend's filesystem, TechDocs
    # reads each plugin's docs from disk (a ``dir:`` ref) instead of a git host
    # — the fix for a self-hosted portal whose repo is private/unpushed.
    _backstage_local_root = os.environ.get("BASELITH_CATALOG_LOCAL_ROOT")
    backstage_provider = BackstageProvider(
        lifecycle_manager=lifecycle_manager,
        base_url=_backstage_base_url,
        docs_base_url=_backstage_docs_url,
        catalog_source_location=_backstage_source_location,
        catalog_local_root=_backstage_local_root,
        # Lazy: called at export time, after all plugin routers are mounted —
        # lets each plugin API entity embed its route-scoped OpenAPI contract.
        openapi_supplier=app.openapi,
        plugin_link_template=_backstage_plugin_link,
    )
    set_backstage_provider(backstage_provider, plugin_registry)
    hot_reload_controller.set_backstage_exporter(backstage_provider)
    app.state.backstage_provider = backstage_provider
    logger.info("📦 Backstage exporter initialized (base_url=%s)", _backstage_base_url)

    # Mount/lazy-activation hooks (extracted to _plugin_runtime for the
    # module size cap; same callbacks, now methods on PluginRuntimeHooks).
    from core.api._plugin_runtime import PluginRuntimeHooks

    hooks = PluginRuntimeHooks(
        app, plugin_registry, plugin_configs, lifecycle_manager, hot_reload_controller
    )
    _mount_plugin_static = hooks.mount_plugin_static
    _get_plugin_runtime_config = hooks.get_plugin_runtime_config
    _activate_plugin_for_runtime = hooks.activate_plugin_for_runtime

    plugin_registry.set_activation_callback(_activate_plugin_for_runtime)
    hot_reload_controller.set_runtime_activation_hook(hooks.on_plugin_activated)

    discoveries = analyzer.discover_plugins(plugin_configs) if analyzer else {}
    for plugin_name, discovery in discoveries.items():
        plugin_registry.register_discovered_plugin(discovery)
        await lifecycle_manager.transition_to_discovered(
            plugin_name,
            metadata={
                "directory_name": discovery.directory_name,
                "version": discovery.metadata.version,
                "description": discovery.metadata.description,
            },
        )

    logger.info("🔌 Discovered %s plugins in lazy-import mode", len(discoveries))

    # Attach pattern-detection hooks for all plugins now active (and future ones
    # that are enabled at runtime via the hot-reload controller).
    backstage_provider.attach_lifecycle_hooks(lifecycle_manager, plugin_registry)

    app.state.plugin_registry = plugin_registry
    app.state.lifecycle_manager = lifecycle_manager
    app.state.hot_reload_controller = hot_reload_controller

    for plugin_name, static_path in plugin_registry.get_all_static_paths().items():
        _mount_plugin_static(plugin_name, static_path)

    # Auto-activation at startup. The hot-reload controller exposes plugins
    # on-demand (POST /api/plugins/<name>/enable), but with ``PLUGIN_AUTO_LOAD``
    # set (default true) we eagerly activate every plugin marked ``enabled:
    # true`` in ``configs/plugins.yaml`` so their routers/handlers are mounted
    # before the first HTTP request lands. Without this the bare ``baselith
    # run`` only exposes core routes and every plugin endpoint is 404 until
    # an authenticated admin call flips the lifecycle state manually.
    # Iterate the *discovered* plugins (keyed by canonical manifest name) rather
    # than the raw config keys: a plugin's directory/config key (``baselithbot``)
    # may differ from its manifest name (``BaselithBot``), and the loader keys
    # lifecycle state by the canonical name. ``_get_plugin_runtime_config``
    # resolves the matching ``configs/plugins.yaml`` entry across name/dir
    # variants, so we read ``enabled`` from there.
    try:
        from core.config.plugins import get_plugin_config

        if get_plugin_config().auto_load:
            for canonical_name in discoveries.keys():
                plugin_conf = _get_plugin_runtime_config(canonical_name)
                if not plugin_conf.get("enabled", False):
                    continue
                try:
                    activated = await _activate_plugin_for_runtime(canonical_name)
                    if activated:
                        logger.info("✅ Plugin auto-activated: %s", canonical_name)
                    else:
                        logger.warning(
                            "❌ Plugin auto-activation failed: %s", canonical_name
                        )
                except Exception as exc:
                    logger.error(
                        "Plugin auto-activation %s raised: %s",
                        canonical_name,
                        exc,
                        exc_info=True,
                    )
    except Exception as exc:
        logger.warning("Plugin auto-activation setup failed: %s", exc)

    try:
        from core.chat.service import initialize_chat_service_with_plugins

        initialize_chat_service_with_plugins(plugin_registry)
        logger.info("✅ Chat service initialized with plugin registry")
    except ImportError as exc:
        logger.warning("Chat service unavailable (init skipped): %s", exc)

    if "evaluation" in required_resources:
        try:
            logger.info("⚖️ Initializing Evaluation Service (required by plugins)...")
            evaluation_service: Any = await lazy_registry.get_or_create("evaluation")
            app.state.evaluation_service = evaluation_service
        except Exception as e:
            logger.error(f"Failed to start Evaluation Service: {e}", exc_info=True)

    if "evolution" in required_resources:
        try:
            logger.info("🧬 Initializing Evolution Service (required by plugins)...")
            evolution_service: Any = await lazy_registry.get_or_create("evolution")
            app.state.evolution_service = evolution_service
        except Exception as e:
            logger.error(f"Failed to start Evolution Service: {e}", exc_info=True)

    if INDEX_BOOTSTRAP_BACKGROUND:
        logger.info(
            "📑 Scheduling background bootstrap (non-blocking startup). "
            "Server will be ready immediately."
        )
        _bootstrap_task = asyncio.create_task(ensure_startup_bootstrap())
        _BACKGROUND_TASKS.add(_bootstrap_task)
        _bootstrap_task.add_done_callback(_BACKGROUND_TASKS.discard)
    else:
        logger.info("📑 Running synchronous bootstrap (blocking startup).")
        await ensure_startup_bootstrap()

    if (
        FASTAPI_LIMITER_AVAILABLE
        and getattr(_storage_config, "cache_backend", "") == "redis"
        and CACHE_REDIS_URL
    ):
        logger.info("🛡️ Initializing Distributed Rate Limiter (Redis)...")
        try:
            redis_limiter = redis.from_url(
                CACHE_REDIS_URL, encoding="utf-8", decode_responses=True
            )
            await FastAPILimiter.init(redis_limiter)
            logger.info("🛡️ Rate Limiter initialized.")
        except Exception as exc:
            logger.warning(
                "🛡️ Rate Limiter initialization skipped: Redis unavailable (%s: %s).",
                type(exc).__name__,
                exc,
            )
    else:
        if not FASTAPI_LIMITER_AVAILABLE:
            logger.warning("🛡️ Rate Limiter skipped (fastapi-limiter not installed).")
        else:
            logger.info("🛡️ Rate Limiter skipped (local cache mode, no Redis).")

    # === Eager auth/security singleton warmup + health checks ===
    # (see core.api.startup_checks — extracted for the 500-line cap)
    warm_auth_singletons()
    await run_startup_health_checks()

    # Enforce the storage-limitation principle (GDPR Art. 5(1)(e)): background
    # retention sweep. No-op unless PRIVACY_ENABLED and PRIVACY_RETENTION_DAYS>0.
    start_retention_scheduler(app)

    try:
        yield
    finally:
        logger.info("🔻 Lifecycle shutdown: closing connections and bootstrapper.")

        await stop_retention_scheduler(app)

        if hasattr(app.state, "plugin_registry"):
            logger.info("🔌 Shutdown plugin system...")
            for plugin in app.state.plugin_registry.get_all():
                try:
                    await plugin.shutdown()
                except Exception as e:
                    logger.error(
                        f"Error shutting down plugin {plugin.metadata.name}: {e}"
                    )

        try:
            from core.di.lazy_registry import get_lazy_registry

            lazy_registry = get_lazy_registry()
            await lazy_registry.shutdown_all()
        except ImportError:
            pass

        try:
            from core.middleware.security import get_security_manager

            await get_security_manager().rate_limiter.close()
        except Exception as e:
            logger.error(f"Error closing rate limiter Redis connection: {e}")

        try:
            from core.observability.otel import shutdown_telemetry

            shutdown_telemetry()
        except Exception as e:
            logger.debug("OpenTelemetry shutdown skipped: %s", e)

        await bootstrapper.shutdown()

        # Drain shared connection pools explicitly instead of relying on GC —
        # in-flight work is already done (uvicorn drains requests before
        # running lifespan shutdown), so this is safe and makes rolling
        # deploys release DB/Redis server-side resources promptly.
        try:
            from core.db.connection import close_async_pool

            await close_async_pool()
        except Exception as e:
            logger.debug("Postgres pool close skipped: %s", e)

        try:
            from core.cache.redis_cache import close_redis_pools

            await close_redis_pools()
        except Exception as e:
            logger.debug("Redis pool close skipped: %s", e)

        logger.info("✅ FastAPI backend stopped successfully.")
