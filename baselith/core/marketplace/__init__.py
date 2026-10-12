"""
Unified Plugin and Agent Marketplace.

Provides the discovery and management engine for framework extensions.
Allows users to search for, install, and update plugins from the
Baselith Marketplace Ecosystem.
"""

from baselith.core.marketplace.installer import (
    InstallResult,
    InstallStatus,
    PluginInstaller,
)
from baselith.core.marketplace.models import (
    MarketplacePlugin,
    PluginCategory,
    PluginStatus,
)
from baselith.core.marketplace.registry import PluginRegistry
from baselith.core.marketplace.validator import (
    PluginValidator,
    ValidationIssue,
    ValidationResult,
)

__all__ = [
    "InstallResult",
    "InstallStatus",
    "MarketplacePlugin",
    "PluginCategory",
    "PluginInstaller",
    "PluginRegistry",
    "PluginStatus",
    "PluginValidator",
    "ValidationIssue",
    "ValidationResult",
]
