"""Plugin health check and lifecycle management.

Contains health monitoring, reload, and lifecycle methods.
"""

from __future__ import annotations

from core.observability.logging import get_logger
from typing import TYPE_CHECKING, Any, Dict, Optional

if TYPE_CHECKING:
    from .interface import Plugin

logger = get_logger(__name__)


class HealthMixin:
    """Mixin providing health check and lifecycle functionality.

    This mixin is designed to be used with PluginRegistry and provides
    methods for checking plugin health and managing plugin lifecycle.
    """

    # These will be provided by the main class
    _plugins: Dict[str, "Plugin"]

    # These methods must be implemented by the main class/other mixins
    def _cleanup_plugin_components(self, plugin_name: str) -> None:
        """
        Clean up components associated with a specific plugin.

        Args:
            plugin_name: The name of the plugin to clean up.
        """
        ...

    def register_all_components(self, plugin: "Plugin") -> None:
        """
        Register all components for a given plugin instance.

        Args:
            plugin: The plugin instance whose components should be registered.
        """
        ...

    async def reload_plugin(
        self,
        plugin_name: str,
        new_config: Optional[Dict[str, Any]] = None,
    ) -> bool:
        """
        Hot-reload a plugin without full system restart.

        This method:
        1. Shuts down the existing plugin
        2. Cleans up its components
        3. Re-initializes with new or existing config
        4. Re-registers all components

        Args:
            plugin_name: Name of plugin to reload
            new_config: Optional new configuration (uses existing if None)

        Returns:
            True if reload successful, False otherwise
        """
        if plugin_name not in self._plugins:
            logger.error(f"Cannot reload: Plugin '{plugin_name}' not registered")
            return False

        plugin = self._plugins[plugin_name]
        old_config = plugin._config.copy()

        try:
            # 1. Shutdown and cleanup
            self._cleanup_plugin_components(plugin_name)
            await plugin.shutdown()

            # 2. Re-initialize with config
            config = new_config if new_config is not None else old_config
            await plugin.initialize(config)

            # 3. Re-register components
            self.register_all_components(plugin)

            logger.info(f"Successfully reloaded plugin: {plugin_name}")

            # Emit event if event bus available
            try:
                from core.events import get_event_bus, EventNames

                get_event_bus().emit_sync(
                    EventNames.PLUGIN_LOADED,
                    {"name": plugin_name, "action": "reload"},
                )
            except ImportError:
                pass

            return True

        except Exception as e:
            logger.error(f"Failed to reload plugin '{plugin_name}': {e}")
            # Try to restore old state
            try:
                await plugin.initialize(old_config)
                self.register_all_components(plugin)
            except Exception:
                pass  # nosec B110
            return False

    def health_check(self, plugin_name: Optional[str] = None) -> Dict[str, Any]:
        """
        Check health status of plugins.

        Args:
            plugin_name: Specific plugin to check, or None for all

        Returns:
            Health status dictionary with format:
            {
                "healthy": bool,
                "plugins": {
                    "plugin_name": {
                        "initialized": bool,
                        "version": str,
                        "status": "healthy" | "unhealthy" | "not_found"
                    }
                }
            }
        """
        result: Dict[str, Any] = {"healthy": True, "plugins": {}}

        plugins_to_check = [plugin_name] if plugin_name else list(self._plugins.keys())

        for name in plugins_to_check:
            if name not in self._plugins:
                result["plugins"][name] = {
                    "status": "not_found",
                    "initialized": False,
                    "version": None,
                }
                result["healthy"] = False
                continue

            plugin = self._plugins[name]
            is_initialized = plugin.is_initialized()

            # Basic health check - plugin should be initialized
            status = "healthy" if is_initialized else "unhealthy"

            # Call plugin's custom health check if available
            if hasattr(plugin, "health_check"):
                try:
                    custom_health = plugin.health_check()
                    if not custom_health.get("healthy", True):
                        status = "unhealthy"
                except Exception as e:
                    status = "unhealthy"
                    logger.warning(f"Plugin '{name}' health check failed: {e}")

            result["plugins"][name] = {
                "status": status,
                "initialized": is_initialized,
                "version": plugin.metadata.version,
            }

            if status != "healthy":
                result["healthy"] = False

        return result

    def get_plugin_version(self, plugin_name: str) -> Optional[str]:
        """
        Get the version of a registered plugin.

        Args:
            plugin_name: Name of the plugin

        Returns:
            Version string or None if not found
        """
        plugin = self._plugins.get(plugin_name)
        return plugin.metadata.version if plugin else None
