"""Hot-reload controller for runtime plugin management."""

from __future__ import annotations

import asyncio
import inspect
from typing import Any

from core.observability.logging import get_logger

from .interface import Plugin
from .lifecycle import PluginLifecycleManager, PluginState
from .loader import PluginLoader
from .metrics import get_metrics_collector
from .registry import PluginRegistry
from .version import check_plugin_dependency

logger = get_logger(__name__)


class HotReloadController:
    """
    Manages runtime plugin enable/disable/reload without server restart.

    Features:
    - Hot-reload individual plugins
    - Safe enable/disable with dependency checking
    - Resource cleanup on unload
    - Rollback on failure
    """

    def __init__(
        self,
        loader: PluginLoader,
        registry: PluginRegistry,
        lifecycle_manager: PluginLifecycleManager,
    ):
        """
        Initialize hot-reload controller.

        Args:
            loader: Plugin loader instance
            registry: Plugin registry instance
            lifecycle_manager: Lifecycle manager instance
        """
        self.loader = loader
        self.registry = registry
        self.lifecycle = lifecycle_manager
        self._reload_lock = asyncio.Lock()
        self._metrics = get_metrics_collector()
        # Optional: BackstageProvider for pattern-cache invalidation on reload.
        # Set via set_backstage_exporter() after construction.
        self._backstage_exporter: Any = None
        self._runtime_activation_hook: Any = None

    def set_backstage_exporter(self, exporter: Any) -> None:
        """
        Register a BackstageProvider so its pattern cache is invalidated
        automatically whenever a plugin is reloaded or disabled.

        Args:
            exporter: A BackstageProvider instance (typed as Any to avoid a
                      circular import between hotreload and exporters).
        """
        self._backstage_exporter = exporter

    def set_runtime_activation_hook(self, hook: Any) -> None:
        """Register an app-specific callback executed after a plugin is enabled."""
        self._runtime_activation_hook = hook

    async def _do_enable(
        self, plugin_name: str, config: dict[str, Any] | None = None
    ) -> bool:
        """
        Inner enable logic — must be called with ``_reload_lock`` already held.
        """
        state = self.lifecycle.get_state(plugin_name)

        if state == PluginState.ACTIVE:
            logger.info(f"Plugin {plugin_name} is already active")
            return True

        if state not in (
            PluginState.DISCOVERED,
            PluginState.DISABLED,
            PluginState.FAILED,
            PluginState.LOADED,
            None,
        ):
            logger.error(f"Cannot enable plugin {plugin_name} in state {state}")
            return False

        start_time = self._metrics.record_load_start(plugin_name)

        try:
            existing_instance = self.lifecycle.get_plugin_instance(plugin_name)
            needs_load = state in (PluginState.DISCOVERED, None) or (
                state == PluginState.FAILED and existing_instance is None
            )
            plugin = existing_instance
            if needs_load:
                try:
                    plugin_dir = self.loader.resolve_plugin_dir(plugin_name)
                except FileNotFoundError:
                    logger.error("Plugin directory not found for %s", plugin_name)
                    return False

                plugin = await self.loader.load_plugin(
                    plugin_dir, config=config, initialize=False
                )
                if not plugin:
                    await self.lifecycle.transition_to_failed(
                        plugin_name, Exception("Failed to load plugin")
                    )
                    return False

            # The loader keys lifecycle state by the plugin's *manifest* name,
            # which may differ from the config/directory key used to enable it
            # (e.g. dir ``baselithbot`` vs manifest ``BaselithBot``, or
            # ``browser_agent`` vs ``browser-agent``). Prefer the instance we
            # just loaded / already hold; only fall back to a name lookup.
            if plugin is None:
                plugin = self.lifecycle.get_plugin_instance(plugin_name)
            if not plugin:
                logger.error(f"Plugin {plugin_name} instance not found")
                return False

            # Lifecycle state/metadata is keyed by the manifest name used at
            # load time (``plugin.metadata.name``), which may differ from the
            # config/dir key (``plugin_name``) — e.g. dir ``browser_agent`` vs
            # manifest ``browser-agent``. Use the canonical name for state
            # transitions so we mutate the same metadata record the loader
            # created instead of KeyError-ing on a missing one.
            lifecycle_name = (
                getattr(getattr(plugin, "metadata", None), "name", None) or plugin_name
            )

            if not await self._check_dependencies(plugin):
                await self.lifecycle.transition_to_failed(
                    lifecycle_name, Exception("Unmet dependencies")
                )
                return False

            await self.lifecycle.transition_to_initializing(lifecycle_name)
            await plugin.initialize(config or {})
            if self.registry.get(plugin_name) is None:
                self.registry.register(plugin)
            self.registry.unsuppress_discovered_plugin(plugin_name)

            if self._backstage_exporter is not None:
                self._backstage_exporter.register_plugin_hook(self.lifecycle, plugin)

            if self._runtime_activation_hook is not None:
                hook_result = self._runtime_activation_hook(plugin)
                if inspect.isawaitable(hook_result):
                    await hook_result

            await self.lifecycle.transition_to_active(lifecycle_name)
            self._metrics.record_load_complete(plugin_name, start_time, success=True)
            self._metrics.record_enable(plugin_name)
            logger.info(f"Successfully enabled plugin: {plugin_name}")
            return True

        except Exception as e:
            logger.error(f"Failed to enable plugin {plugin_name}: {e}", exc_info=True)
            await self.lifecycle.transition_to_failed(plugin_name, e)
            self._metrics.record_load_complete(plugin_name, start_time, success=False)
            self._metrics.record_error(plugin_name, e)
            return False

    async def _do_disable(self, plugin_name: str) -> bool:
        """
        Inner disable logic — must be called with ``_reload_lock`` already held.
        """
        state = self.lifecycle.get_state(plugin_name)

        if state == PluginState.DISABLED:
            logger.info(f"Plugin {plugin_name} is already disabled")
            return True

        if state not in (PluginState.ACTIVE, PluginState.LOADED):
            logger.error(f"Cannot disable plugin {plugin_name} in state {state}")
            return False

        try:
            dependent_plugins = self._find_dependent_plugins(plugin_name)
            if state == PluginState.ACTIVE and dependent_plugins:
                logger.error(
                    f"Cannot disable {plugin_name}: required by {dependent_plugins}"
                )
                return False

            await self.registry.unregister(plugin_name)
            self.registry.suppress_discovered_plugin(plugin_name)
            await self.lifecycle.transition_to_disabled(plugin_name)
            self._metrics.record_disable(plugin_name)

            if self._backstage_exporter is not None:
                self._backstage_exporter.invalidate_pattern_cache(plugin_name)

            logger.info(f"Successfully disabled plugin: {plugin_name}")
            return True

        except Exception as e:
            logger.error(f"Failed to disable plugin {plugin_name}: {e}", exc_info=True)
            self._metrics.record_error(plugin_name, e)
            return False

    async def enable_plugin(
        self, plugin_name: str, config: dict[str, Any] | None = None
    ) -> bool:
        """
        Enable a disabled plugin.

        Args:
            plugin_name: Name of plugin to enable
            config: Optional configuration override

        Returns:
            True if successfully enabled
        """
        async with self._reload_lock:
            return await self._do_enable(plugin_name, config)

    async def disable_plugin(self, plugin_name: str) -> bool:
        """
        Disable an active plugin.

        Args:
            plugin_name: Name of plugin to disable

        Returns:
            True if successfully disabled
        """
        async with self._reload_lock:
            return await self._do_disable(plugin_name)

    async def reload_plugin(
        self, plugin_name: str, config: dict[str, Any] | None = None
    ) -> bool:
        """
        Reload a plugin (disable + re-enable).

        Args:
            plugin_name: Name of plugin to reload
            config: Optional new configuration

        Returns:
            True if successfully reloaded
        """
        async with self._reload_lock:
            state = self.lifecycle.get_state(plugin_name)

            if state not in (
                PluginState.ACTIVE,
                PluginState.DISABLED,
                PluginState.FAILED,
            ):
                logger.error(f"Cannot reload plugin {plugin_name} in state {state}")
                return False

            was_active = state == PluginState.ACTIVE
            start_time = self._metrics.record_reload_start(plugin_name)

            try:
                if was_active:
                    if not await self._do_disable(plugin_name):
                        return False

                await self.lifecycle.transition_to_unloading(plugin_name)
                await self.lifecycle.remove_plugin(plugin_name)

                if plugin_name in self.loader._loaded_modules:
                    self.loader._unload_module(plugin_name)

                success = await self._do_enable(plugin_name, config)

                self._metrics.record_reload_complete(
                    plugin_name, start_time, success=success
                )

                if success:
                    logger.info(f"Successfully reloaded plugin: {plugin_name}")
                    if self._backstage_exporter is not None:
                        self._backstage_exporter.invalidate_pattern_cache(plugin_name)
                else:
                    logger.error(f"Failed to reload plugin: {plugin_name}")

                return success

            except Exception as e:
                logger.error(
                    f"Error reloading plugin {plugin_name}: {e}", exc_info=True
                )
                self._metrics.record_reload_complete(
                    plugin_name, start_time, success=False
                )
                self._metrics.record_error(plugin_name, e)
                return False

    async def _check_dependencies(self, plugin: Plugin) -> bool:
        """
        Check if plugin dependencies are satisfied.

        Args:
            plugin: Plugin instance to check

        Returns:
            True if all dependencies satisfied
        """
        # Check plugin dependencies (new system)
        for dep_name, version_constraint in plugin.metadata.plugin_dependencies.items():
            dep_plugin = self.registry.get(dep_name)

            if not dep_plugin:
                logger.error(
                    f"Plugin {plugin.metadata.name} requires {dep_name} which is not loaded"
                )
                return False

            if not self.lifecycle.is_active(dep_name):
                logger.error(
                    f"Plugin {plugin.metadata.name} requires {dep_name} which is not active"
                )
                return False

            # Check version constraint
            if not check_plugin_dependency(
                dep_plugin.metadata.version, version_constraint
            ):
                logger.error(
                    f"Plugin {plugin.metadata.name} requires {dep_name} {version_constraint}, "
                    f"but found {dep_plugin.metadata.version}"
                )
                return False

        # Legacy dependencies support
        for dep_name in plugin.metadata.dependencies:
            if dep_name == "core":
                continue

            if not self.registry.get(dep_name):
                logger.error(
                    f"Plugin {plugin.metadata.name} requires {dep_name} (legacy dependency)"
                )
                return False

        return True

    def _find_dependent_plugins(self, plugin_name: str) -> list[str]:
        """
        Find plugins that depend on the given plugin.

        Args:
            plugin_name: Name of plugin to check

        Returns:
            List of plugin names that depend on this plugin
        """
        dependents = []

        for name, state in self.lifecycle.get_all_states().items():
            if state != PluginState.ACTIVE:
                continue

            plugin = self.registry.get(name)
            if not plugin:
                continue

            # Check new dependency system
            if (
                plugin_name in plugin.metadata.plugin_dependencies
                or plugin_name in plugin.metadata.dependencies
            ):
                dependents.append(name)

        return dependents

    async def reload_all_plugins(
        self, configs: dict[str, dict[str, Any]] | None = None
    ) -> dict[str, bool]:
        """
        Reload all active plugins.

        Args:
            configs: Optional new configurations per plugin

        Returns:
            Dictionary mapping plugin names to reload success status
        """
        configs = configs or {}
        results = {}

        active_plugins = list(self.lifecycle.get_active_plugins())

        # Sort by dependencies (reload dependencies first)
        sorted_plugins = self._sort_by_dependencies(active_plugins)

        for plugin_name in sorted_plugins:
            config = configs.get(plugin_name)
            success = await self.reload_plugin(plugin_name, config)
            results[plugin_name] = success

        return results

    def _sort_by_dependencies(self, plugin_names: list[str]) -> list[str]:
        """
        Sort plugin names by dependencies (topological sort).

        Args:
            plugin_names: List of plugin names to sort

        Returns:
            Sorted list with dependencies first
        """
        import graphlib

        graph = {}
        for name in plugin_names:
            plugin = self.registry.get(name)
            if not plugin:
                continue

            # Get all dependencies (new + legacy)
            deps = set(plugin.metadata.plugin_dependencies.keys())
            deps.update(plugin.metadata.dependencies)

            # Filter to only dependencies in our list
            deps = {d for d in deps if d in plugin_names and d != "core"}
            graph[name] = deps

        ts = graphlib.TopologicalSorter(graph)
        return list(ts.static_order())

    def get_reload_status(self) -> dict[str, Any]:
        """
        Get status of all plugins for reload operations.

        Returns:
            Status dictionary with plugin states and dependencies
        """
        return {
            "lifecycle": self.lifecycle.get_lifecycle_summary(),
            "dependency_graph": self._build_dependency_graph(),
        }

    def _build_dependency_graph(self) -> dict[str, list[str]]:
        """Build dependency graph for visualization."""
        graph = {}

        for plugin in self.registry.get_all():
            deps = list(plugin.metadata.plugin_dependencies.keys())
            deps.extend([d for d in plugin.metadata.dependencies if d != "core"])
            graph[plugin.metadata.name] = deps

        return graph
