"""Backward-compatible re-export. Canonical code is in plugins.marketplace.validator."""

try:
    from plugins.marketplace.validator import (  # noqa: F401
        ValidationIssue,
        ValidationResult,
        PluginValidator,
    )
except (ImportError, ModuleNotFoundError):
    raise ImportError(
        "Marketplace plugin not found. Please install the marketplace plugin project: "
        "'pip install -e ../baselith-marketplace-plugin'"
    ) from None

__all__ = ["ValidationIssue", "ValidationResult", "PluginValidator"]
