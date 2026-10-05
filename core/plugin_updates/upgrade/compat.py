"""Installed plugins whose declared core bounds exclude a target release."""

from __future__ import annotations

from pathlib import Path

import yaml

from core.plugins.discovery import apply_overlay
from core.plugins.integrity import find_manifest_file
from core.plugins.overlay import registered_overlay_dirs
from core.plugins.version import check_version_compatibility

from ..upgrade_models import PluginBoundsIssue, PluginCompatibility


def _text(value: object) -> str | None:
    if value is None:
        return None
    return str(value).strip() or None


def _bounds(plugin_dir: Path) -> PluginBoundsIssue | None:
    """The plugin's declared version and core bounds, or None without bounds."""
    manifest = find_manifest_file(plugin_dir)
    if manifest is None:
        return None
    try:
        data = yaml.safe_load(manifest.read_text(encoding="utf-8"))
    except (yaml.YAMLError, UnicodeDecodeError, OSError):
        return None
    if not isinstance(data, dict):
        return None
    low = _text(data.get("min_core_version"))
    high = _text(data.get("max_core_version"))
    if low is None and high is None:
        return None
    return PluginBoundsIssue(
        plugin=plugin_dir.name,
        version=_text(data.get("version")),
        min_core_version=low,
        max_core_version=high,
    )


def installed_bounds(plugin_root: Path) -> list[PluginBoundsIssue]:
    """Declared core bounds of every installed plugin (overlay entries win)."""
    scanned = (
        sorted(p for p in plugin_root.iterdir() if p.is_dir())
        if plugin_root.is_dir()
        else []
    )
    found = (_bounds(d) for d in apply_overlay(scanned, registered_overlay_dirs()))
    return [b for b in found if b is not None]


def plugin_compatibility(
    target: str,
    bounds: list[PluginBoundsIssue] | None,
) -> PluginCompatibility:
    """Which installed plugins would refuse to load on core ``target``.

    Plugin manifests declare ``min_core_version``/``max_core_version`` against
    the public core release (``core._core_version.CORE_VERSION``) in every
    distribution, the same number the loader's gate compares with, so the
    bounds compare directly with the public release the upgrade installs.

    Args:
        target: The public core release the upgrade installs.
        bounds: The installed plugins' declared bounds, or None when the
            manifests could not be read (reported as not computed).
    """
    if bounds is None:
        return PluginCompatibility(
            checked=False,
            target_version=target,
            reason="the installed plugins' manifests could not be read; check "
            "the plugins' release notes instead",
        )
    incompatible = [
        b
        for b in bounds
        if not check_version_compatibility(
            target, b.min_core_version, b.max_core_version
        )
    ]
    return PluginCompatibility(
        checked=True, target_version=target, incompatible=incompatible
    )


__all__ = ["installed_bounds", "plugin_compatibility"]
