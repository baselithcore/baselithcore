"""Install the declared-permission guards for a loaded plugin registry.

The guards live in :mod:`core.plugins.egress` and :mod:`core.plugins.access`;
what they need is a lookup from a plugin name to its parsed permissions. That
lookup is the registry, and wiring it is plugin machinery — not something the
API lifespan should carry, which is why it lives here rather than inline in
:mod:`core.api.lifespan`.

Call once, after the registry has loaded its plugins::

    from core.plugins.guards import install_plugin_guards

    install_plugin_guards(plugin_registry)

Installing is safe regardless of ``BASELITH_PLUGIN_PERMISSIONS``: the mode is
resolved per call, so a deployment can move from ``warn`` to ``enforce``
without restarting anything, and a plugin that declared nothing is never
refused in either.
"""

from __future__ import annotations

from typing import Any

from core.observability.logging import get_logger
from core.plugins.access import install_secret_guard, uninstall_secret_guard
from core.plugins.egress import (
    install_egress_guard,
    set_permissions_resolver,
    uninstall_egress_guard,
)
from core.plugins.permissions import PluginPermissions

logger = get_logger(__name__)

__all__ = ["install_plugin_guards", "uninstall_plugin_guards"]


def install_plugin_guards(registry: Any) -> None:
    """Point the egress and secret guards at ``registry``.

    Args:
        registry: The loaded plugin registry; anything with a ``get(name)``
            returning a plugin whose ``metadata.permissions`` is a
            :class:`~core.plugins.permissions.PluginPermissions`.
    """

    def _permissions(name: str) -> PluginPermissions | None:
        plugin = registry.get(name)
        return plugin.metadata.permissions if plugin is not None else None

    set_permissions_resolver(_permissions)
    install_egress_guard()
    install_secret_guard()
    logger.info("🔐 Plugin permission guards installed (egress, secrets, tools)")


def uninstall_plugin_guards() -> None:
    """Remove the guards and forget the resolver. Intended for tests."""
    uninstall_egress_guard()
    uninstall_secret_guard()
    set_permissions_resolver(None)
