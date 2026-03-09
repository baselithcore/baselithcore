"""Hot-reload controller for runtime plugin management."""

from __future__ import annotations

import asyncio
from core.observability.logging import get_logger

from typing import Any, Dict, List, Optional

from .interface import Plugin
from .lifecycle import PluginLifecycleManager, PluginState
from .loader import PluginLoader
from .registry import PluginRegistry
from .version import check_plugin_dependency
from .metrics import get_metrics_collector

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

    async def enable_plugin(
        self, plugin_name: str, config: Optional[Dict[str, Any]] = None
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
            state = self.lifecycle.get_state(plugin_name)

            if state == PluginState.ACTIVE:
                logger.info(f"Plugin {plugin_name} is already active")
                return True

            if state not in (PluginState.DISABLED, PluginState.FAILED, None):
                logger.error(f"Cannot enable plugin {plugin_name} in state {state}")
                return False

            # Start timing
            start_time = self._metrics.record_load_start(plugin_name)

            try:
                # If plugin was never loaded, load it
                if state is None:
                    plugin_dir = self.loader.plugins_dir / plugin_name
                    if not plugin_dir.exists():
                        logger.error(f"Plugin directory not found: {plugin_dir}")
                        return False

                    await self.lifecycle.transition_to_loading(plugin_name)

                    plugin = await self.loader.load_plugin(
                        plugin_dir, config=config, initialize=False
                    )

                    if not plugin:
                        await self.lifecycle.transition_to_failed(
                            plugin_name, Exception("Failed to load plugin")
                        )
                        return False

                    await self.lifecycle.transition_to_loaded(plugin_name, plugin)

                # Get plugin instance
                plugin = self.lifecycle.get_plugin_instance(plugin_name)
                if not plugin:
                    logger.error(f"Plugin {plugin_name} instance not found")
                    return False

                # Check dependencies
                if not await self._check_dependencies(plugin):
                    await self.lifecycle.transition_to_failed(
                        plugin_name, Exception("Unmet dependencies")
                    )
                    return False

                # Initialize
                await self.lifecycle.transition_to_initializing(plugin_name)
                await plugin.initialize(config or {})

                # Register
                self.registry.register(plugin)

                # Activate
                await self.lifecycle.transition_to_active(plugin_name)

                # Record success
                self._metrics.record_load_complete(
                    plugin_name, start_time, success=True
                )
                self._metrics.record_enable(plugin_name)

                logger.info(f"Successfully enabled plugin: {plugin_name}")
                return True

            except Exception as e:
                logger.error(
                    f"Failed to enable plugin {plugin_name}: {e}", exc_info=True
                )
                await self.lifecycle.transition_to_failed(plugin_name, e)

                # Record failure
                self._metrics.record_load_complete(
                    plugin_name, start_time, success=False
                )
                self._metrics.record_error(plugin_name, e)

                return False

    async def disable_plugin(self, plugin_name: str) -> bool:
        """
        Disable an active plugin.

        Args:
            plugin_name: Name of plugin to disable

        Returns:
            True if successfully disabled
        """
        async with self._reload_lock:
            state = self.lifecycle.get_state(plugin_name)

            if state == PluginState.DISABLED:
                logger.info(f"Plugin {plugin_name} is already disabled")
                return True

            if state != PluginState.ACTIVE:
                logger.error(f"Cannot disable plugin {plugin_name} in state {state}")
                return False

            try:
                # Check if other plugins depend on this one
                dependent_plugins = self._find_dependent_plugins(plugin_name)
                if dependent_plugins:
                    logger.error(
                        f"Cannot disable {plugin_name}: required by {dependent_plugins}"
                    )
                    return False

                # Unregister from registry
                await self.registry.unregister(plugin_name)

                # Transition to disabled
                await self.lifecycle.transition_to_disabled(plugin_name)

                # Record metrics
                self._metrics.record_disable(plugin_name)

                logger.info(f"Successfully disabled plugin: {plugin_name}")
                return True

            except Exception as e:
                logger.error(
                    f"Failed to disable plugin {plugin_name}: {e}", exc_info=True
                )
                self._metrics.record_error(plugin_name, e)
                return False

    async def reload_plugin(
        self, plugin_name: str, config: Optional[Dict[str, Any]] = None
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

            # Backup current state for rollback
            was_active = state == PluginState.ACTIVE

            # Start timing reload
            start_time = self._metrics.record_reload_start(plugin_name)

            try:
                # Disable if active
                if was_active:
                    if not await self.disable_plugin(plugin_name):
                        return False

                # Unload completely
                await self.lifecycle.transition_to_unloading(plugin_name)
                await self.lifecycle.remove_plugin(plugin_name)

                # Clear from loader's module cache
                if plugin_name in self.loader._loaded_modules:
                    import sys

                    module_name = f"plugins.{plugin_name}"
                    if module_name in sys.modules:
                        del sys.modules[module_name]
                    del self.loader._loaded_modules[plugin_name]

                # Re-enable
                success = await self.enable_plugin(plugin_name, config)

                # Record reload metrics
                self._metrics.record_reload_complete(
                    plugin_name, start_time, success=success
                )

                if success:
                    logger.info(f"Successfully reloaded plugin: {plugin_name}")
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

    def _find_dependent_plugins(self, plugin_name: str) -> List[str]:
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
            if plugin_name in plugin.metadata.plugin_dependencies:
                dependents.append(name)
            # Check legacy dependencies
            elif plugin_name in plugin.metadata.dependencies:
                dependents.append(name)

        return dependents

    async def reload_all_plugins(
        self, configs: Optional[Dict[str, Dict[str, Any]]] = None
    ) -> Dict[str, bool]:
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

    def _sort_by_dependencies(self, plugin_names: List[str]) -> List[str]:
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

    def get_reload_status(self) -> Dict[str, Any]:
        """
        Get status of all plugins for reload operations.

        Returns:
            Status dictionary with plugin states and dependencies
        """
        return {
            "lifecycle": self.lifecycle.get_lifecycle_summary(),
            "dependency_graph": self._build_dependency_graph(),
        }

    def _build_dependency_graph(self) -> Dict[str, List[str]]:
        """Build dependency graph for visualization."""
        graph = {}

        for plugin in self.registry.get_all():
            deps = list(plugin.metadata.plugin_dependencies.keys())
            deps.extend([d for d in plugin.metadata.dependencies if d != "core"])
            graph[plugin.metadata.name] = deps

        return graph
