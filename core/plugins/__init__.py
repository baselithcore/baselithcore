"""
Plugin system for extending the baselith-core platform.

This module provides the infrastructure for loading and managing plugins
that extend the core functionality with domain-specific features.

New in Phase 2 (Plugin Packaging):
- Hot-reload support for runtime plugin management
- Semantic versioning with dependency constraints
- Plugin lifecycle management with state tracking
- Enhanced metadata with Python and plugin dependencies
"""

from .interface import Plugin, PluginMetadata
from .agent_plugin import AgentPlugin
from .router_plugin import RouterPlugin
from .graph_plugin import GraphPlugin
from .registry import PluginRegistry
from .loader import PluginLoader
from .lifecycle import PluginLifecycleManager, PluginState, PluginLifecycleHooks
from .hotreload import HotReloadController
from .version import (
    SemanticVersion,
    VersionConstraint,
    check_version_compatibility,
    check_plugin_dependency,
)
from .api import router as plugin_management_router, set_hot_reload_controller
from .metrics import PluginMetricsCollector, get_metrics_collector

__all__ = [
    # Core plugin system
    "Plugin",
    "PluginMetadata",
    "AgentPlugin",
    "RouterPlugin",
    "GraphPlugin",
    "PluginRegistry",
    "PluginLoader",
    # Phase 2: Hot-reload & lifecycle
    "PluginLifecycleManager",
    "PluginState",
    "PluginLifecycleHooks",
    "HotReloadController",
    # Versioning
    "SemanticVersion",
    "VersionConstraint",
    "check_version_compatibility",
    "check_plugin_dependency",
    # Phase 3: Metrics & Monitoring
    "PluginMetricsCollector",
    "get_metrics_collector",
    # API
    "plugin_management_router",
    "set_hot_reload_controller",
]
