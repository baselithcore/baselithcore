"""Plugin runtime mount/activation hooks for the app lifespan.

Extracted from ``core.api.lifespan`` (module size cap): the closure helpers
that mount plugin routers/static/SPA assets on first activation and drive
lazy runtime activation (with dependency auto-activation) through the
hot-reload controller. Behavior is unchanged — the lifespan wires the same
callbacks, now methods on :class:`PluginRuntimeHooks`.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from core.api.spa import SPAStaticFiles
from core.observability.logging import get_logger
from core.plugins import PluginState

logger = get_logger(__name__)


class PluginRuntimeHooks:
    """Mount + lazy-activation seams shared by startup and hot reload."""

    def __init__(
        self,
        app: FastAPI,
        plugin_registry: Any,
        plugin_configs: dict[str, dict[str, Any]],
        lifecycle_manager: Any,
        hot_reload_controller: Any,
    ) -> None:
        self._app = app
        self._registry = plugin_registry
        self._configs = plugin_configs
        self._lifecycle = lifecycle_manager
        self._hot_reload = hot_reload_controller
        self._activation_lock = asyncio.Lock()
        self._mounted_routes: set[str] = set()
        self._mounted_static: set[str] = set()

    def mount_plugin_routes(self, plugin: Any) -> None:
        plugin_name = plugin.metadata.name
        if plugin_name in self._mounted_routes:
            return

        prefix = plugin.get_router_prefix()
        logger.debug("Plugin found: %s, prefix: '%s'", plugin_name, prefix)
        for router in plugin.get_routers():
            logger.debug("Mounting router for %s at %s", plugin_name, prefix)
            self._app.include_router(router, prefix=prefix)
            logger.info(
                "🔌 Plugin router mounted: %s%s",
                prefix,
                router.prefix if hasattr(router, "prefix") else "",
            )

        self._mounted_routes.add(plugin_name)

    def mount_plugin_static(self, plugin_name: str, static_path: Path) -> None:
        if plugin_name in self._mounted_static:
            return

        mount_path = f"/plugins/{plugin_name}/static"
        self._app.mount(
            mount_path,
            StaticFiles(directory=str(static_path)),
            name=f"{plugin_name}-static",
        )

        spa_index = static_path / "index.html"
        if spa_index.exists():
            self._app.mount(
                f"/{plugin_name}",
                SPAStaticFiles(directory=str(static_path), html=True),
                name=f"{plugin_name}-spa",
            )
            logger.info("🔌 Plugin SPA mounted: /%s", plugin_name)

        self._mounted_static.add(plugin_name)
        logger.info("🔌 Plugin static mounted: %s", mount_path)

    async def on_plugin_activated(self, plugin: Any) -> None:
        self.mount_plugin_routes(plugin)
        static_path = self._registry.get_all_static_paths().get(plugin.metadata.name)
        if static_path:
            self.mount_plugin_static(plugin.metadata.name, static_path)

    def get_plugin_runtime_config(self, plugin_name: str) -> dict[str, Any]:
        discovery = self._registry.get_discovered_plugin(plugin_name)
        if discovery is not None:
            candidates = (
                plugin_name,
                discovery.directory_name,
                discovery.directory_name.replace("_", "-"),
                discovery.directory_name.replace("-", "_"),
            )
            for candidate in candidates:
                if candidate in self._configs:
                    return self._configs[candidate]

        return self._configs.get(plugin_name, {})

    async def activate_plugin_for_runtime(self, plugin_name: str) -> bool:
        async with self._activation_lock:
            state = self._lifecycle.get_state(plugin_name)
            if state == PluginState.ACTIVE:
                return True

            discovery = self._registry.get_discovered_plugin(plugin_name)
            if discovery is not None:
                for dep_name in discovery.metadata.plugin_dependencies.keys():
                    dep_state = self._lifecycle.get_state(dep_name)
                    if dep_state == PluginState.ACTIVE:
                        continue
                    dep_ok = await self._hot_reload.enable_plugin(
                        dep_name, self.get_plugin_runtime_config(dep_name)
                    )
                    if not dep_ok:
                        logger.error(
                            "Failed to auto-activate dependency %s for %s",
                            dep_name,
                            plugin_name,
                        )
                        return False

            return await self._hot_reload.enable_plugin(
                plugin_name, self.get_plugin_runtime_config(plugin_name)
            )


__all__ = ["PluginRuntimeHooks"]
