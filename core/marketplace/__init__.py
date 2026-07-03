"""
Unified Plugin and Agent Marketplace.

Provides the discovery and management engine for framework extensions.
Allows users to search for, install, and update plugins from the
Baselith Marketplace Ecosystem.
"""

from core.marketplace.installer import InstallResult, InstallStatus, PluginInstaller
from core.marketplace.models import MarketplacePlugin, PluginCategory, PluginStatus
from core.marketplace.registry import PluginRegistry
from core.marketplace.validator import (
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
