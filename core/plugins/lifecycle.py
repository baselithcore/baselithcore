"""Plugin lifecycle management."""

from __future__ import annotations

import asyncio
from core.observability.logging import get_logger
from enum import Enum
from typing import Any, Callable, Dict, Optional, Set
from datetime import datetime, timezone

from .interface import Plugin

logger = get_logger(__name__)

# Import metrics collector (lazy to avoid circular imports)
_metrics_collector = None


def _get_metrics():
    """Lazy import metrics collector."""
    global _metrics_collector
    if _metrics_collector is None:
        from .metrics import get_metrics_collector

        _metrics_collector = get_metrics_collector()
    return _metrics_collector


class PluginState(str, Enum):
    """Plugin lifecycle states."""

    DISCOVERED = "discovered"  # Found but not loaded
    LOADING = "loading"  # Currently being loaded
    LOADED = "loaded"  # Loaded but not initialized
    INITIALIZING = "initializing"  # Currently initializing
    ACTIVE = "active"  # Fully initialized and active
    DISABLED = "disabled"  # Disabled by user/config
    FAILED = "failed"  # Failed to load/initialize
    UNLOADING = "unloading"  # Currently being unloaded


class PluginLifecycleHooks:
    """
    Lifecycle hooks for plugin state transitions.

    Plugins can register callbacks to be invoked during state changes.
    """

    def __init__(self):
        """Initialize plugin lifecycle hooks."""
        self._hooks: Dict[str, Dict[str, Set[Callable]]] = {
            "on_before_load": {},
            "on_after_load": {},
            "on_before_init": {},
            "on_after_init": {},
            "on_before_enable": {},
            "on_after_enable": {},
            "on_before_disable": {},
            "on_after_disable": {},
            "on_before_unload": {},
            "on_after_unload": {},
            "on_error": {},
        }

    def register_hook(
        self, plugin_name: str, hook_type: str, callback: Callable
    ) -> None:
        """
        Register a lifecycle hook callback.

        Args:
            plugin_name: Name of plugin to hook
            hook_type: Type of hook (e.g., "on_before_load")
            callback: Async callable to invoke
        """
        if hook_type not in self._hooks:
            raise ValueError(f"Invalid hook type: {hook_type}")

        if plugin_name not in self._hooks[hook_type]:
            self._hooks[hook_type][plugin_name] = set()

        self._hooks[hook_type][plugin_name].add(callback)
        logger.debug(f"Registered {hook_type} hook for {plugin_name}")

    def unregister_hook(
        self, plugin_name: str, hook_type: str, callback: Callable
    ) -> None:
        """
        Unregister a lifecycle hook callback.

        Args:
            plugin_name: Name of plugin
            hook_type: Type of hook
            callback: Callback to remove
        """
        if hook_type in self._hooks and plugin_name in self._hooks[hook_type]:
            self._hooks[hook_type][plugin_name].discard(callback)

    async def invoke_hooks(
        self, plugin_name: str, hook_type: str, *args: Any, **kwargs: Any
    ) -> None:
        """
        Invoke all registered hooks for a plugin.

        Args:
            plugin_name: Name of plugin
            hook_type: Type of hook to invoke
            *args, **kwargs: Arguments to pass to callbacks
        """
        if hook_type not in self._hooks:
            return

        hooks = self._hooks[hook_type].get(plugin_name, set())
        for hook in hooks:
            try:
                if asyncio.iscoroutinefunction(hook):
                    await hook(*args, **kwargs)
                else:
                    hook(*args, **kwargs)
            except Exception as e:
                logger.error(
                    f"Hook {hook_type} failed for {plugin_name}: {e}", exc_info=True
                )


class PluginLifecycleManager:
    """
    Manages plugin lifecycle state transitions.

    Tracks state, handles transitions, and invokes lifecycle hooks.
    """

    def __init__(self):
        """Initialize the plugin lifecycle manager."""
        self._states: Dict[str, PluginState] = {}
        self._plugins: Dict[str, Plugin] = {}
        self._hooks = PluginLifecycleHooks()
        self._metadata: Dict[str, Dict[str, Any]] = {}  # Additional tracking info
        self._lock = asyncio.Lock()

    def register_hook(
        self, plugin_name: str, hook_type: str, callback: Callable
    ) -> None:
        """
        Register a lifecycle hook callback.

        Args:
            plugin_name: Name of plugin to hook
            hook_type: Type of hook (e.g., "on_before_load")
            callback: Async callable to invoke
        """
        self._hooks.register_hook(plugin_name, hook_type, callback)

    def get_state(self, plugin_name: str) -> Optional[PluginState]:
        """
        Get the current lifecycle state of a specific plugin.

        Args:
            plugin_name: Name of the plugin to query

        Returns:
            The current PluginState or None if not found
        """
        return self._states.get(plugin_name)

    def get_all_states(self) -> Dict[str, PluginState]:
        """
        Get the current states of all tracked plugins.

        Returns:
            A dictionary mapping plugin names to their PluginState
        """
        return self._states.copy()

    def is_active(self, plugin_name: str) -> bool:
        """
        Check if a plugin is currently in the ACTIVE state.

        Args:
            plugin_name: Name of the plugin to check

        Returns:
            True if active, False otherwise
        """
        return self._states.get(plugin_name) == PluginState.ACTIVE

    def get_active_plugins(self) -> Set[str]:
        """
        Get the names of all plugins currently in the ACTIVE state.

        Returns:
            A set of active plugin names
        """
        return {
            name for name, state in self._states.items() if state == PluginState.ACTIVE
        }

    async def transition_to_loading(self, plugin_name: str) -> None:
        """Transition plugin to loading state."""
        async with self._lock:
            await self._hooks.invoke_hooks(plugin_name, "on_before_load")
            self._states[plugin_name] = PluginState.LOADING
            self._metadata[plugin_name] = {
                "load_started_at": datetime.now(timezone.utc)
            }
            logger.debug(f"Plugin {plugin_name}: → LOADING")

    async def transition_to_loaded(self, plugin_name: str, plugin: Plugin) -> None:
        """Transition plugin to loaded state."""
        async with self._lock:
            self._plugins[plugin_name] = plugin
            self._states[plugin_name] = PluginState.LOADED
            self._metadata[plugin_name]["loaded_at"] = datetime.now(timezone.utc)
            await self._hooks.invoke_hooks(plugin_name, "on_after_load", plugin)
            logger.debug(f"Plugin {plugin_name}: LOADING → LOADED")

    async def transition_to_initializing(self, plugin_name: str) -> None:
        """Transition plugin to initializing state."""
        async with self._lock:
            await self._hooks.invoke_hooks(plugin_name, "on_before_init")
            self._states[plugin_name] = PluginState.INITIALIZING
            self._metadata[plugin_name]["init_started_at"] = datetime.now(timezone.utc)
            logger.debug(f"Plugin {plugin_name}: LOADED → INITIALIZING")

    async def transition_to_active(self, plugin_name: str) -> None:
        """Transition plugin to active state."""
        async with self._lock:
            old_state = self._states.get(plugin_name)
            self._states[plugin_name] = PluginState.ACTIVE
            self._metadata[plugin_name]["activated_at"] = datetime.now(timezone.utc)
            plugin = self._plugins.get(plugin_name)
            await self._hooks.invoke_hooks(plugin_name, "on_after_init", plugin)

            # Record state change in metrics
            _get_metrics().record_state_change(
                plugin_name, old_state, PluginState.ACTIVE
            )

            logger.info(f"Plugin {plugin_name}: INITIALIZING → ACTIVE ✅")

    async def transition_to_disabled(self, plugin_name: str) -> None:
        """Transition plugin to disabled state."""
        async with self._lock:
            await self._hooks.invoke_hooks(plugin_name, "on_before_disable")
            old_state = self._states.get(plugin_name)
            self._states[plugin_name] = PluginState.DISABLED
            self._metadata[plugin_name]["disabled_at"] = datetime.now(timezone.utc)
            plugin = self._plugins.get(plugin_name)
            await self._hooks.invoke_hooks(plugin_name, "on_after_disable", plugin)
            logger.info(f"Plugin {plugin_name}: {old_state} → DISABLED")

    async def transition_to_failed(self, plugin_name: str, error: Exception) -> None:
        """Transition plugin to failed state."""
        async with self._lock:
            old_state = self._states.get(plugin_name)
            self._states[plugin_name] = PluginState.FAILED
            self._metadata[plugin_name]["failed_at"] = datetime.now(timezone.utc)
            self._metadata[plugin_name]["error"] = str(error)
            await self._hooks.invoke_hooks(plugin_name, "on_error", error)

            # Record state change and error in metrics
            _get_metrics().record_state_change(
                plugin_name, old_state, PluginState.FAILED
            )
            _get_metrics().record_error(plugin_name, error)

            logger.error(f"Plugin {plugin_name}: → FAILED ({error})")

    async def transition_to_unloading(self, plugin_name: str) -> None:
        """Transition plugin to unloading state."""
        async with self._lock:
            await self._hooks.invoke_hooks(plugin_name, "on_before_unload")
            self._states[plugin_name] = PluginState.UNLOADING
            self._metadata[plugin_name]["unload_started_at"] = datetime.now(
                timezone.utc
            )
            logger.debug(f"Plugin {plugin_name}: → UNLOADING")

    async def remove_plugin(self, plugin_name: str) -> None:
        """Remove plugin from lifecycle tracking."""
        async with self._lock:
            plugin = self._plugins.get(plugin_name)
            await self._hooks.invoke_hooks(plugin_name, "on_after_unload", plugin)

            if plugin_name in self._states:
                del self._states[plugin_name]
            if plugin_name in self._plugins:
                del self._plugins[plugin_name]
            if plugin_name in self._metadata:
                del self._metadata[plugin_name]

            logger.info(f"Plugin {plugin_name}: UNLOADING → REMOVED")

    def get_plugin_metadata(self, plugin_name: str) -> Optional[Dict[str, Any]]:
        """
        Retrieve lifecycle metadata for a specific plugin.

        Args:
            plugin_name: Name of the plugin

        Returns:
            Dictionary of metadata (e.g., activated_at, error) or None
        """
        return self._metadata.get(plugin_name)

    def get_plugin_instance(self, plugin_name: str) -> Optional[Plugin]:
        """
        Get the loaded plugin instance by name.

        Args:
            plugin_name: Name of the plugin

        Returns:
            The Plugin instance or None if not loaded
        """
        return self._plugins.get(plugin_name)

    def get_lifecycle_summary(self) -> Dict[str, Any]:
        """
        Generate a summary of all managed plugins and their states.

        Returns:
            A summary dictionary containing counts and details per plugin.
        """
        return {
            "total_plugins": len(self._states),
            "active": len(
                [s for s in self._states.values() if s == PluginState.ACTIVE]
            ),
            "disabled": len(
                [s for s in self._states.values() if s == PluginState.DISABLED]
            ),
            "failed": len(
                [s for s in self._states.values() if s == PluginState.FAILED]
            ),
            "loading": len(
                [
                    s
                    for s in self._states.values()
                    if s in (PluginState.LOADING, PluginState.INITIALIZING)
                ]
            ),
            "plugins": {
                name: {
                    "state": state.value,
                    "metadata": self._metadata.get(name, {}),
                }
                for name, state in self._states.items()
            },
        }
