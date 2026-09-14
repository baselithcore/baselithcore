"""Plugin health check and lifecycle management.

Contains health monitoring, reload, and lifecycle methods.

Two reporting surfaces coexist deliberately:

* :meth:`HealthMixin.health_check` — synchronous, unchanged, and callable from
  any thread. It knows only whether ``initialize`` completed, plus a legacy
  ``health_check()`` attribute a plugin may define.
* :meth:`HealthMixin.check_health` — the async counterpart, which awaits the
  optional ``async def health()`` hook on :class:`~core.plugins.interface.Plugin`.
  A plugin can then report what only it knows (a stale upstream, a drained
  queue) instead of the framework guessing from an ``_initialized`` flag.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from core.observability.logging import get_logger

from .nursery import PluginTaskNursery

if TYPE_CHECKING:
    from .interface import Plugin

logger = get_logger(__name__)


@dataclass(slots=True)
class PluginHealth:
    """A plugin's self-reported health.

    Attributes:
        healthy: Whether the plugin considers itself able to serve.
        detail: Short human-readable explanation, shown in health reports.
        data: Optional structured payload (queue depths, last-success
            timestamps) for dashboards; must be JSON-serialisable.
    """

    healthy: bool = True
    detail: str = ""
    data: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        """Serialize for the health endpoint."""
        return {"healthy": self.healthy, "detail": self.detail, "data": dict(self.data)}


class HealthMixin:
    """Mixin providing health check and lifecycle functionality.

    This mixin is designed to be used with PluginRegistry and provides
    methods for checking plugin health and managing plugin lifecycle.
    """

    # These will be provided by the main class
    _plugins: dict[str, Plugin]

    #: Provided by PluginRegistry.__init__. Reached directly rather than
    #: through ``self.cancel_plugin_tasks``: a ``...``-bodied stub declared here
    #: would sit earlier in the MRO than TaskNurseryMixin's real method and
    #: silently swallow every cancel.
    _nursery: PluginTaskNursery

    # These methods must be implemented by the main class/other mixins
    def _cleanup_plugin_components(self, plugin_name: str) -> None:
        """
        Clean up components associated with a specific plugin.

        Args:
            plugin_name: The name of the plugin to clean up.
        """
        ...

    def register_all_components(self, plugin: Plugin) -> None:
        """
        Register all components for a given plugin instance.

        Args:
            plugin: The plugin instance whose components should be registered.
        """
        ...

    async def reload_plugin(
        self,
        plugin_name: str,
        new_config: dict[str, Any] | None = None,
    ) -> bool:
        """
        Hot-reload a plugin without full system restart.

        This method:
        1. Cleans up its components
        2. Cancels and awaits every background task it spawned
        3. Shuts down the existing plugin
        4. Re-initializes with new or existing config
        5. Re-registers all components

        Step 2 matters: without it a reloaded plugin's old poller/cron loop kept
        running against the old code alongside the new instance.

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
            # Closing window spans the shutdown too — see PluginRegistry.
            # unregister: work spawned from inside shutdown() would otherwise
            # survive into the reloaded generation.
            with self._nursery.closing(plugin_name):
                await self._nursery.cancel_all(plugin_name)
                await plugin.shutdown()

            # 2. Re-initialize with config
            config = new_config if new_config is not None else old_config
            await plugin.initialize(config)

            # 3. Re-register components
            self.register_all_components(plugin)

            logger.info(f"Successfully reloaded plugin: {plugin_name}")

            # Emit event if event bus available
            try:
                from core.events import EventNames, get_event_bus

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

    def health_check(self, plugin_name: str | None = None) -> dict[str, Any]:
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
        result: dict[str, Any] = {"healthy": True, "plugins": {}}

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

    async def check_health(self, plugin_name: str | None = None) -> dict[str, Any]:
        """Async health report that consults each plugin's ``health()`` hook.

        Same shape as :meth:`health_check`, with two additions per plugin that
        overrides the hook: ``detail`` and ``data`` from its
        :class:`PluginHealth`. A plugin that does not override it is reported
        exactly as the synchronous path reports it.

        A hook that raises marks that plugin unhealthy and is logged — a broken
        reporter must never take the health endpoint down with it.

        Args:
            plugin_name: Specific plugin to check, or None for all.

        Returns:
            Health status dictionary.
        """
        from .interface import Plugin as _Plugin

        result = self.health_check(plugin_name)

        names = [plugin_name] if plugin_name else list(result["plugins"])
        for name in names:
            plugin = self._plugins.get(name)
            if plugin is None or not _Plugin.has_health_override(type(plugin)):
                continue
            entry = result["plugins"].get(name)
            if entry is None:
                continue
            try:
                report = await plugin.health()
            except Exception as exc:
                logger.warning(f"Plugin '{name}' health() hook failed: {exc}")
                entry["status"] = "unhealthy"
                entry["detail"] = f"health() raised: {exc}"
                result["healthy"] = False
                continue

            entry["detail"] = report.detail
            entry["data"] = dict(report.data)
            if not report.healthy:
                entry["status"] = "unhealthy"
                result["healthy"] = False

        return result

    def get_plugin_version(self, plugin_name: str) -> str | None:
        """
        Get the version of a registered plugin.

        Args:
            plugin_name: Name of the plugin

        Returns:
            Version string or None if not found
        """
        plugin = self._plugins.get(plugin_name)
        return plugin.metadata.version if plugin else None
