"""
Unified Plugin and Agent Marketplace.

Provides the discovery and management engine for framework extensions.
Allows users to search for, install, and update plugins from the
Baselith Marketplace Ecosystem.
"""

from core.marketplace.registry import PluginRegistry
from core.marketplace.models import MarketplacePlugin, PluginCategory, PluginStatus
from core.marketplace.installer import PluginInstaller, InstallResult, InstallStatus
from core.marketplace.validator import (
    PluginValidator,
    ValidationResult,
    ValidationIssue,
)

__all__ = [
    "PluginRegistry",
    "MarketplacePlugin",
    "PluginCategory",
    "PluginStatus",
    "PluginInstaller",
    "InstallResult",
    "InstallStatus",
    "PluginValidator",
    "ValidationResult",
    "ValidationIssue",
]
