"""
Marketplace Module — backward-compatible re-export.

The canonical implementation now lives in ``plugins.marketplace``.
This shim exists so that existing ``from core.marketplace import …`` imports
continue to work.
"""

try:
    from plugins.marketplace import (  # noqa: F401
        PluginRegistry,
        PluginMetadata,
        PluginInstaller,
        InstallResult,
        PluginValidator,
    )
except (ImportError, ModuleNotFoundError):
    raise ImportError(
        "Marketplace plugin not found. Please install the marketplace plugin project: "
        "'pip install -e ../baselith-marketplace-plugin'"
    ) from None

__all__ = [
    "PluginRegistry",
    "PluginMetadata",
    "PluginInstaller",
    "InstallResult",
    "PluginValidator",
]
