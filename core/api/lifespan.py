"""
API Lifespan Management.

Handles startup and shutdown logic for the FastAPI application, including
resource initialization, plugin loading, and rate limiter setup.
"""

import asyncio
import logging
from core.observability.logging import get_logger
import os
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
import yaml

try:
    from fastapi_limiter import FastAPILimiter

    FASTAPI_LIMITER_AVAILABLE = True
except ImportError:
    FastAPILimiter = None  # type: ignore[assignment, misc]
    FASTAPI_LIMITER_AVAILABLE = False
    logging.warning("⚠️ fastapi-limiter not available - rate limiting will be disabled")

import redis.asyncio as redis

from core.services.bootstrap import bootstrapper, ensure_startup_bootstrap
from core.config import get_app_config, get_storage_config
from core.plugins import PluginRegistry, PluginLoader

logger = get_logger(__name__)

_app_config = get_app_config()
_storage_config = get_storage_config()

INDEX_BOOTSTRAP_BACKGROUND = getattr(_app_config, "index_bootstrap_background", False)
POSTGRES_ENABLED = getattr(_storage_config, "postgres_enabled", False)
CACHE_REDIS_URL = getattr(_storage_config, "cache_redis_url", "")


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

    # Setup OpenTelemetry tracing
    if getattr(_app_config, "telemetry_enabled", False):
        logger.info("📊 Initializing OpenTelemetry...")
        try:
            from core.observability.tracing import setup_telemetry

            setup_telemetry(
                service_name="baselith-core",
                otlp_endpoint=getattr(_app_config, "telemetry_otel_endpoint", ""),
            )
            logger.info("📊 OpenTelemetry initialized")
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

    # === LAZY LOADING: Analyze plugin requirements first ===
    logger.info("🔌 Initializing plugin system with lazy loading...")

    plugin_configs: dict[str, Any] = {}

    try:
        config_path = Path(os.environ.get("PLUGIN_CONFIG_PATH", "configs/plugins.yaml"))
        if config_path.exists():
            with open(config_path, "r") as f:
                plugin_configs = yaml.safe_load(f) or {}
            logger.info(f"📄 Loaded plugin configurations from {config_path}")
        else:
            logger.warning(f"⚠️ Plugin configuration file not found: {config_path}")
    except Exception as e:
        logger.error(f"❌ Failed to load plugin configurations: {e}")

    try:
        from core.plugins.resource_analyzer import ResourceAnalyzer
        from core.di.lazy_registry import get_lazy_registry
        from core.bootstrap.lazy_init import RESOURCE_FACTORIES

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

        from core.di import ServiceRegistry
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
        PluginLifecycleManager,
        HotReloadController,
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

    loaded_count = await plugin_loader.load_all_plugins(plugin_configs)
    logger.info(f"🔌 Loaded {loaded_count} plugins")

    app.state.plugin_registry = plugin_registry
    app.state.lifecycle_manager = lifecycle_manager
    app.state.hot_reload_controller = hot_reload_controller

    for plugin in plugin_registry.get_all():
        prefix = plugin.get_router_prefix()
        logger.debug(f"Plugin found: {plugin.metadata.name}, prefix: '{prefix}'")
        for router in plugin.get_routers():
            logger.debug(f"Mounting router for {plugin.metadata.name} at {prefix}")
            app.include_router(router, prefix=prefix)
            logger.info(
                f"🔌 Plugin router mounted: {prefix}{router.prefix if hasattr(router, 'prefix') else ''}"
            )

    logger.info(f"🔌 Loaded {len(plugin_registry.get_all())} plugins")

    for plugin_name, static_path in plugin_registry.get_all_static_paths().items():
        mount_path = f"/plugins/{plugin_name}/static"
        app.mount(
            mount_path,
            StaticFiles(directory=str(static_path)),
            name=f"{plugin_name}-static",
        )
        logger.info(f"🔌 Plugin static mounted: {mount_path}")

    @app.get("/api/plugins/frontend-manifest")
    async def get_frontend_manifest():
        """Return manifest of all plugin frontend assets for injection."""
        return plugin_registry.get_frontend_manifest()

    try:
        from core.chat.service import initialize_chat_service_with_plugins

        initialize_chat_service_with_plugins(plugin_registry)
        logger.info("✅ Chat service initialized with plugin registry")
    except ImportError:
        pass

    if "evaluation" in required_resources:
        try:
            logger.info("⚖️ Initializing Evaluation Service (required by plugins)...")
            evaluation_service: Any = await lazy_registry.get_or_create("evaluation")
            app.state.evaluation_service = evaluation_service
        except Exception as e:
            logger.error(f"Failed to start Evaluation Service: {e}")

    if "evolution" in required_resources:
        try:
            logger.info("🧬 Initializing Evolution Service (required by plugins)...")
            evolution_service: Any = await lazy_registry.get_or_create("evolution")
            app.state.evolution_service = evolution_service
        except Exception as e:
            logger.error(f"Failed to start Evolution Service: {e}")

    if INDEX_BOOTSTRAP_BACKGROUND:
        logger.info(
            "📑 Scheduling background bootstrap (non-blocking startup). "
            "Server will be ready immediately."
        )
        asyncio.create_task(ensure_startup_bootstrap())
    else:
        logger.info("📑 Running synchronous bootstrap (blocking startup).")
        await ensure_startup_bootstrap()

    if (
        FASTAPI_LIMITER_AVAILABLE
        and getattr(_storage_config, "cache_backend", "") == "redis"
        and CACHE_REDIS_URL
    ):
        logger.info("🛡️ Initializing Distributed Rate Limiter (Redis)...")
        redis_limiter = redis.from_url(
            CACHE_REDIS_URL, encoding="utf-8", decode_responses=True
        )
        await FastAPILimiter.init(redis_limiter)
        logger.info("🛡️ Rate Limiter initialized.")
    else:
        if not FASTAPI_LIMITER_AVAILABLE:
            logger.warning("🛡️ Rate Limiter skipped (fastapi-limiter not installed).")
        else:
            logger.info("🛡️ Rate Limiter skipped (local cache mode, no Redis).")

    try:
        yield
    finally:
        logger.info("🔻 Lifecycle shutdown: closing connections and bootstrapper.")

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

        await bootstrapper.shutdown()
        logger.info("✅ FastAPI backend stopped successfully.")
