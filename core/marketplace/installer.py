"""Backward-compatible re-export. Canonical code is in plugins.marketplace.installer."""

try:
    from plugins.marketplace.installer import (  # noqa: F401
        InstallStatus,
        InstallResult,
        PluginInstaller,
    )
except (ImportError, ModuleNotFoundError):
    raise ImportError(
        "Marketplace plugin not found. Please install the marketplace plugin project: "
        "'pip install -e ../baselith-marketplace-plugin'"
    ) from None

__all__ = ["InstallStatus", "InstallResult", "PluginInstaller"]
