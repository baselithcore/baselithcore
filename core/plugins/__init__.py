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

from .agent_plugin import AgentPlugin
from .api import router as plugin_management_router
from .api import set_hot_reload_controller
from .app_setup import apply_plugin_app_middleware
from .config_validation import (
    is_config_enforcement_enabled,
    validate_plugin_config,
)
from .declarative import (
    DeclarativeSkillLoader,
    LoadedSkill,
    SkillCard,
    SkillLoadError,
    SkillSandboxError,
    split_frontmatter,
)
from .env import load_plugin_dotenv
from .exporters import (
    BackstageProvider,
    backstage_exporter_router,
    set_backstage_provider,
)
from .graph_plugin import GraphPlugin
from .hotreload import HotReloadController
from .interface import Plugin, PluginMetadata
from .lifecycle import PluginLifecycleHooks, PluginLifecycleManager, PluginState
from .lifecycle_events import (
    PLUGIN_ACTIVATED,
    PLUGIN_DEACTIVATED,
    PLUGIN_FAILED,
    PLUGIN_RELOADED,
    emit_lifecycle_event,
)
from .loader import PluginLoader
from .metrics import PluginMetricsCollector, get_metrics_collector
from .protocols import BackstageExporter, CatalogExporter
from .registry import PluginRegistry
from .result import SkillResult, fail, ok, partial
from .router_plugin import RouterPlugin
from .skills_service import SkillService, make_activation_tool_fn
from .version import (
    SemanticVersion,
    VersionConstraint,
    check_plugin_compatibility,
    check_plugin_dependency,
    check_version_compatibility,
    is_compat_enforcement_enabled,
)

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
    # Lifecycle event topics
    "PLUGIN_ACTIVATED",
    "PLUGIN_DEACTIVATED",
    "PLUGIN_RELOADED",
    "PLUGIN_FAILED",
    "emit_lifecycle_event",
    # Versioning
    "SemanticVersion",
    "VersionConstraint",
    "check_version_compatibility",
    "check_plugin_dependency",
    "check_plugin_compatibility",
    "is_compat_enforcement_enabled",
    # Config schema validation
    "validate_plugin_config",
    "is_config_enforcement_enabled",
    "load_plugin_dotenv",
    # App-level middleware composition
    "apply_plugin_app_middleware",
    # Phase 3: Metrics & Monitoring
    "PluginMetricsCollector",
    "get_metrics_collector",
    # API
    "plugin_management_router",
    "set_hot_reload_controller",
    # Phase 4: Catalog Exporters (Backstage integration)
    "CatalogExporter",
    "BackstageExporter",
    "BackstageProvider",
    "backstage_exporter_router",
    "set_backstage_provider",
    # Skill result envelope
    "SkillResult",
    "ok",
    "fail",
    "partial",
    # Declarative skills (SKILL.md) + catalog service
    "DeclarativeSkillLoader",
    "LoadedSkill",
    "SkillCard",
    "SkillLoadError",
    "SkillSandboxError",
    "SkillService",
    "make_activation_tool_fn",
    "split_frontmatter",
]
