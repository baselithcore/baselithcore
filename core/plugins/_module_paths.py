"""
Synthetic parent-package registration for plugin module loading.

Shared by the async :class:`core.plugins.loader.PluginLoader` and the sync
pre-discovery in :mod:`core.plugins.app_setup` (previously duplicated in
both). Registering ``plugins`` / ``plugins.<name>`` parents in
``sys.modules`` lets relative imports inside a plugin resolve correctly and
keeps ``__package__ == __spec__.parent`` (avoids DeprecationWarning).
"""

from __future__ import annotations

import sys
import types
from pathlib import Path


def ensure_parent_packages(plugin_name: str, plugin_dir: Path) -> None:
    """Register synthetic parent packages so __package__ == __spec__.parent."""
    plugins_root = plugin_dir.parent

    if "plugins" not in sys.modules:
        pkg = types.ModuleType("plugins")
        pkg.__path__ = [str(plugins_root)]
        pkg.__package__ = "plugins"
        sys.modules["plugins"] = pkg

    pkg_fqn = f"plugins.{plugin_name}"
    if pkg_fqn not in sys.modules:
        pkg = types.ModuleType(pkg_fqn)
        pkg.__path__ = [str(plugin_dir)]
        pkg.__package__ = pkg_fqn
        sys.modules[pkg_fqn] = pkg


__all__ = ["ensure_parent_packages"]
