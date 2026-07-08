"""Plugin introspection helpers for the Backstage exporter.

Pure functions that inspect a loaded plugin — where its source lives, whether
it ships TechDocs, and which Agentic Design Patterns its manifest/resources/
source imply.  Kept free of provider state so they stay unit-testable and the
provider module stays within the repository's file-size cap.
"""

from __future__ import annotations

import asyncio
import inspect
from pathlib import Path

from core.plugins.interface import Plugin, PluginMetadata

from .patterns import RESOURCE_TO_PATTERN, TAG_ALIASES, scan_source_files

__all__ = [
    "detect_from_resources",
    "detect_from_source",
    "detect_from_tags",
    "plugin_has_techdocs",
    "resolve_plugin_dir",
]


def resolve_plugin_dir(plugin: Plugin) -> Path | None:
    """Directory the plugin class was loaded from (None if unresolvable)."""
    try:
        plugin_module = inspect.getmodule(plugin.__class__)
        if not plugin_module or not getattr(plugin_module, "__file__", None):
            return None
        return Path(plugin_module.__file__).parent  # type: ignore[arg-type]
    except Exception:
        return None


def plugin_has_techdocs(plugin: Plugin) -> bool:
    """True when the plugin directory ships an mkdocs.yml (TechDocs-ready)."""
    plugin_dir = resolve_plugin_dir(plugin)
    if plugin_dir is None:
        return False
    try:
        return (plugin_dir / "mkdocs.yml").is_file() or (
            plugin_dir / "mkdocs.yaml"
        ).is_file()
    except OSError:
        return False


def detect_from_tags(meta: PluginMetadata) -> list[str]:
    """Return pattern labels whose short name matches a manifest tag."""
    tag_set = {t.lower().replace(" ", "-") for t in meta.tags}
    return [label for alias, label in TAG_ALIASES.items() if alias in tag_set]


def detect_from_resources(meta: PluginMetadata) -> list[str]:
    """Return pattern labels implied by required/optional resources."""
    resources = set(meta.required_resources + meta.optional_resources)
    return [
        label
        for resource, label in RESOURCE_TO_PATTERN.items()
        if resource in resources
    ]


async def detect_from_source(plugin: Plugin) -> list[str]:
    """Scan the plugin's source files for core-import patterns (in a thread)."""
    plugin_dir = resolve_plugin_dir(plugin)
    if plugin_dir is None:
        return []
    return await asyncio.to_thread(scan_source_files, plugin_dir)
