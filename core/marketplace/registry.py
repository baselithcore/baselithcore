"""
Unified Plugin and Agent Marketplace Registry.

Provides the discovery and management engine for framework extensions.
Facilitates the registration of remote and local plugin sources,
allowing agents to dynamically expand their capabilities by
installing specialized modules from the Baselith Ecosystem.
"""

try:
    from plugins.marketplace.registry import (  # noqa: F401
        PluginCategory,
        PluginStatus,
        PluginMetadata,
        PluginReview,
        PluginRegistry,
    )
except (ImportError, ModuleNotFoundError):
    raise ImportError(
        "Marketplace plugin not found. Please install the marketplace plugin project: "
        "'pip install -e ../baselith-marketplace-plugin'"
    ) from None

__all__ = [
    "PluginCategory",
    "PluginStatus",
    "PluginMetadata",
    "PluginReview",
    "PluginRegistry",
]
