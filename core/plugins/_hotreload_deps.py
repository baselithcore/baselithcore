"""Dependency checks used by the hot-reload controller.

Split out of :mod:`core.plugins.hotreload` (500-line module cap). Every helper
takes the registry and lifecycle manager explicitly so it stays a pure function
of the runtime state it inspects.
"""

from __future__ import annotations

import graphlib
from typing import TYPE_CHECKING

from core.observability.logging import get_logger

from .lifecycle import PluginLifecycleManager, PluginState
from .version import check_plugin_dependency

if TYPE_CHECKING:  # pragma: no cover - typing only
    from .interface import Plugin
    from .registry import PluginRegistry

logger = get_logger(__name__)


def check_dependencies(
    plugin: Plugin,
    registry: PluginRegistry,
    lifecycle: PluginLifecycleManager,
) -> bool:
    """Check whether a plugin's dependencies are satisfied.

    Args:
        plugin: Plugin instance to check.
        registry: Registry holding the currently registered plugins.
        lifecycle: Lifecycle manager tracking plugin states.

    Returns:
        True if every declared dependency is registered, active and (for the
        versioned system) within its version constraint.
    """
    name = plugin.metadata.name
    for dep_name, version_constraint in plugin.metadata.plugin_dependencies.items():
        dep_plugin = registry.get(dep_name)

        if not dep_plugin:
            logger.error(f"Plugin {name} requires {dep_name} which is not loaded")
            return False

        if not lifecycle.is_active(dep_name):
            logger.error(f"Plugin {name} requires {dep_name} which is not active")
            return False

        if not check_plugin_dependency(dep_plugin.metadata.version, version_constraint):
            logger.error(
                f"Plugin {name} requires {dep_name} {version_constraint}, "
                f"but found {dep_plugin.metadata.version}"
            )
            return False

    # Legacy dependencies support
    for dep_name in plugin.metadata.dependencies:
        if dep_name == "core":
            continue

        if not registry.get(dep_name):
            logger.error(f"Plugin {name} requires {dep_name} (legacy dependency)")
            return False

    return True


def find_dependent_plugins(
    plugin_name: str,
    registry: PluginRegistry,
    lifecycle: PluginLifecycleManager,
) -> list[str]:
    """Find the active plugins that depend on the given plugin.

    Args:
        plugin_name: Name of plugin to check.
        registry: Registry holding the currently registered plugins.
        lifecycle: Lifecycle manager tracking plugin states.

    Returns:
        Names of active plugins declaring ``plugin_name`` as a dependency.
    """
    dependents = []

    for name, state in lifecycle.get_all_states().items():
        if state != PluginState.ACTIVE:
            continue

        plugin = registry.get(name)
        if not plugin:
            continue

        if (
            plugin_name in plugin.metadata.plugin_dependencies
            or plugin_name in plugin.metadata.dependencies
        ):
            dependents.append(name)

    return dependents


def sort_names_by_dependencies(
    plugin_names: list[str], registry: PluginRegistry
) -> list[str]:
    """Sort plugin names so dependencies come first (topological sort).

    Args:
        plugin_names: Names of plugins to sort.
        registry: Registry holding the currently registered plugins.

    Returns:
        Sorted list with dependencies first.
    """
    graph: dict[str, set[str]] = {}
    for name in plugin_names:
        plugin = registry.get(name)
        if not plugin:
            continue

        deps = set(plugin.metadata.plugin_dependencies.keys())
        deps.update(plugin.metadata.dependencies)
        graph[name] = {d for d in deps if d in plugin_names and d != "core"}

    ts = graphlib.TopologicalSorter(graph)
    return list(ts.static_order())


def build_dependency_graph(registry: PluginRegistry) -> dict[str, list[str]]:
    """Build the plugin dependency graph for visualization.

    Args:
        registry: Registry holding the currently registered plugins.

    Returns:
        Mapping of plugin name to the names it depends on.
    """
    graph = {}

    for plugin in registry.get_all():
        deps = list(plugin.metadata.plugin_dependencies.keys())
        deps.extend([d for d in plugin.metadata.dependencies if d != "core"])
        graph[plugin.metadata.name] = deps

    return graph


__all__ = [
    "build_dependency_graph",
    "check_dependencies",
    "find_dependent_plugins",
    "sort_names_by_dependencies",
]
