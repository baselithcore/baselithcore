"""Plugin runtime mount/activation hooks for the app lifespan.

Extracted from ``core.api.lifespan`` (module size cap): the closure helpers
that mount plugin routers/static/SPA assets on first activation and drive
lazy runtime activation (with dependency auto-activation) through the
hot-reload controller. Behavior is unchanged — the lifespan wires the same
callbacks, now methods on :class:`PluginRuntimeHooks`.
"""

from __future__ import annotations

import asyncio
import re
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from core.api.spa import SPAStaticFiles
from core.observability.logging import get_logger
from core.plugins import PluginState
from core.plugins.config_file import plugin_enabled

logger = get_logger(__name__)

# A plugin name is used verbatim to build URL mount paths (``/{name}`` for the
# SPA, ``/plugins/{name}/static``). Restrict it to a safe slug charset so a
# manifest ``name`` cannot smuggle ``/`` (mount at an arbitrary prefix and
# shadow other routes) or other path-significant characters.
_VALID_PLUGIN_NAME = re.compile(r"^[a-z0-9][a-z0-9._-]{0,63}$")


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
        self._mcp_tools: dict[str, list[str]] = {}
        self._mcp_disable_hooked: set[str] = set()

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

        if not _VALID_PLUGIN_NAME.match(plugin_name):
            logger.error(
                "Refusing to mount static/SPA assets for plugin %r: name is "
                "not a valid slug (^[a-z0-9][a-z0-9._-]{0,63}$)",
                plugin_name,
            )
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
        self.expose_plugin_mcp_tools(plugin)

    def expose_plugin_mcp_tools(self, plugin: Any) -> None:
        """Register the plugin's MCP tools on the HTTP-mounted server, if any.

        The server is built in ``create_app()`` before any plugin is active,
        so its own bulk registration saw none; this runs per activation
        (startup and hot reload) and withdraws the tools on disable.
        """
        server = getattr(self._app.state, "mcp_server", None)
        if server is None:
            return
        from core.mcp.plugin_tools import register_plugin_mcp_tools

        name = plugin.metadata.name
        self.withdraw_plugin_mcp_tools(name)
        self._mcp_tools[name] = register_plugin_mcp_tools(server, plugin)
        if name not in self._mcp_disable_hooked:
            # Bound to the name: the lifecycle may hand the hook ``None``.
            async def _withdraw(*_args: Any) -> None:
                self.withdraw_plugin_mcp_tools(name)

            self._lifecycle.register_hook(name, "on_after_disable", _withdraw)
            self._mcp_disable_hooked.add(name)

    def withdraw_plugin_mcp_tools(self, plugin_name: str) -> None:
        names = self._mcp_tools.pop(plugin_name, [])
        server = getattr(self._app.state, "mcp_server", None)
        if server is None or not names:
            return
        from core.mcp.plugin_tools import unregister_plugin_mcp_tools

        unregister_plugin_mcp_tools(server, names)

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

    async def auto_activate(self, discoveries: Mapping[str, Any]) -> None:
        """Activate every discovered plugin the config file enables.

        Iterates the *discovered* plugins (keyed by canonical manifest name),
        not the raw config keys: a directory/config key (``baselithbot``) may
        differ from the manifest name (``BaselithBot``), and lifecycle state
        is keyed by the canonical name. Enablement goes through
        :func:`core.plugins.config_file.plugin_enabled` — the rule discovery
        already applied — so a block without ``enabled:`` (or no config file
        at all) activates the plugin instead of discovering it and leaving it
        dormant.
        """
        for canonical_name, discovery in discoveries.items():
            if not plugin_enabled(
                self._configs, discovery.directory_name, canonical_name
            ):
                continue
            try:
                activated = await self.activate_plugin_for_runtime(canonical_name)
            except Exception as exc:
                logger.error(
                    "Plugin auto-activation %s raised: %s",
                    canonical_name,
                    exc,
                    exc_info=True,
                )
                continue
            if activated:
                logger.info("✅ Plugin auto-activated: %s", canonical_name)
            else:
                logger.warning("❌ Plugin auto-activation failed: %s", canonical_name)

    async def activate_plugin_for_runtime(self, plugin_name: str) -> bool:
        async with self._activation_lock:
            return await self._activate_locked(plugin_name, set())

    async def _activate_locked(self, plugin_name: str, _in_progress: set[str]) -> bool:
        """Activate ``plugin_name`` after its dependencies — **transitively**.

        Must be called with ``self._activation_lock`` held. Recurses so a
        dependency's *own* dependencies are activated first: e.g. activating a
        plugin that depends on ``resto-graph`` (which in turn depends on the
        ``document-sources`` infra plugin) now pulls ``document-sources`` up
        the chain instead of enabling ``resto-graph`` while its dependency is
        still dormant (which failed ``_check_dependencies`` with "not loaded").
        The lock is a non-reentrant ``asyncio.Lock``, so recursion stays
        lock-free and re-uses the single acquisition from the public entry
        point; ``_in_progress`` guards against dependency cycles.
        """
        if self._lifecycle.get_state(plugin_name) == PluginState.ACTIVE:
            return True
        if plugin_name in _in_progress:
            # Dependency cycle — stop recursing; enable_plugin's own
            # _check_dependencies remains the backstop for a genuine cycle.
            return True
        _in_progress.add(plugin_name)

        discovery = self._registry.get_discovered_plugin(plugin_name)
        if discovery is None:
            # Not in the enabled discovery set: ``discover_plugins`` skips any
            # plugin the operator set ``enabled: false`` (or, under
            # config-filtering, one absent from the config), so ``None`` means
            # "operator did not enable this". Refuse to auto-activate it — even
            # transitively as someone else's dependency — instead of falling
            # through to ``enable_plugin`` (whose ``_do_enable`` accepts a
            # ``None`` lifecycle state and would load it anyway). Fails closed;
            # this path is reachable pre-auth via ``PluginActivationMiddleware``.
            logger.warning(
                "Refusing to auto-activate plugin %s: not in the enabled "
                "discovery set (disabled or absent from config)",
                plugin_name,
            )
            return False

        for dep_name in discovery.metadata.plugin_dependencies.keys():
            if self._lifecycle.get_state(dep_name) == PluginState.ACTIVE:
                continue
            if not await self._activate_locked(dep_name, _in_progress):
                logger.error(
                    "Failed to auto-activate dependency %s for %s",
                    dep_name,
                    plugin_name,
                )
                return False

        enabled: bool = await self._hot_reload.enable_plugin(
            plugin_name, self.get_plugin_runtime_config(plugin_name)
        )
        return enabled


__all__ = ["PluginRuntimeHooks"]
