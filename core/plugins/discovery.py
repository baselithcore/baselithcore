"""Plugin discovery sources beyond the ``plugins/`` directory scan.

A plugin installed as a wheel has no directory under ``plugins/``, so the
filesystem scan never saw it and the only supported distribution channel was
"copy the tree in". Packages can now advertise themselves through the standard
``baselith.plugins`` entry-point group; each entry points at an importable
package whose directory contains a manifest.

The directory scan stays authoritative: on a name clash the local tree wins and
the installed one is ignored with a warning. An operator who has dropped a
plugin into ``plugins/`` is patching it there deliberately, and a wheel on
``sys.path`` must not silently take that over.

Kept out of ``loader.py`` to respect the 500-line module cap.
"""

from __future__ import annotations

import importlib.util
import os
from collections.abc import Iterable
from importlib.metadata import entry_points
from pathlib import Path
from typing import Any

from core.observability.logging import get_logger
from core.utils.logsafe import sanitize_log_value

logger = get_logger(__name__)

#: Entry-point group a distribution declares to publish a BaselithCore plugin.
ENTRY_POINT_GROUP = "baselith.plugins"

#: Manifest filenames a plugin directory may use, in preference order.
MANIFEST_FILENAMES = ("manifest.yaml", "manifest.yml", "manifest.json")

#: Explicit kill switch for entry-point discovery. Truthy values here stop the
#: loader from considering any installed distribution, leaving only the
#: directory scan — useful for a locked-down deployment that wants exactly the
#: trees it shipped and nothing a transitive dependency might advertise.
_DISABLE_ENV = "BASELITH_DISABLE_PLUGIN_ENTRY_POINTS"


def is_entry_point_discovery_enabled() -> bool:
    """Whether installed distributions may contribute plugins.

    Returns:
        False only when ``BASELITH_DISABLE_PLUGIN_ENTRY_POINTS`` is truthy.
    """
    raw = os.environ.get(_DISABLE_ENV, "").strip().lower()
    return raw not in ("1", "true", "yes", "on")


def find_manifest(plugin_dir: Path) -> Path | None:
    """Return the plugin's manifest path, in preference order.

    Args:
        plugin_dir: The plugin directory to inspect.

    Returns:
        The first existing ``manifest.{yaml,yml,json}``, or ``None`` when the
        directory ships no manifest at all. The distinction matters to the
        loader: *no manifest* is a legacy plugin, while *a manifest that does
        not parse* is a broken one that must be refused.
    """
    for name in MANIFEST_FILENAMES:
        candidate = plugin_dir / name
        if candidate.exists():
            return candidate
    return None


def has_manifest(plugin_dir: Path) -> bool:
    """True when ``plugin_dir`` contains a plugin manifest."""
    return find_manifest(plugin_dir) is not None


def _entry_point_module(value: str) -> str:
    """Extract the module path from an entry-point value.

    ``pkg.plugin``, ``pkg.plugin:Class`` and ``pkg.plugin [extra]`` all name the
    same module; only the module half is meaningful here, because the entry
    points at a *package directory* holding a manifest.
    """
    return value.split("[")[0].split(":")[0].strip()


def _resolve_package_dir(module_name: str) -> Path | None:
    """Locate the on-disk package directory for an importable module name.

    ``find_spec`` on a dotted name imports the *parent* packages (not the module
    itself). That is acceptable here — the distribution is already installed in
    the interpreter's environment and opted in by declaring the entry point —
    but it is why the plugin's own integrity/signature verification still
    happens later, in ``PluginLoader.load_plugin``, before its code is executed.
    """
    spec = importlib.util.find_spec(module_name)
    if spec is None:
        return None
    locations = list(spec.submodule_search_locations or [])
    if locations:
        return Path(locations[0])
    if spec.origin and spec.origin != "built-in":
        return Path(spec.origin).parent
    return None


def iter_entry_point_plugin_dirs(
    group: str = ENTRY_POINT_GROUP,
) -> list[Path]:
    """Discover plugin directories advertised by installed distributions.

    Every failure mode is contained: a distribution with broken metadata, an
    entry point naming a module that no longer imports, or a package without a
    manifest is logged and skipped. Discovery must never be the reason the
    process cannot start.

    Args:
        group: Entry-point group to read. Defaults to ``baselith.plugins``.

    Returns:
        Plugin directories, in entry-point name order, each holding a manifest.
    """
    if not is_entry_point_discovery_enabled():
        logger.debug("Plugin entry-point discovery disabled via %s", _DISABLE_ENV)
        return []

    try:
        found: Iterable[Any] = entry_points(group=group)
    except Exception as exc:  # broken/partial distribution metadata
        logger.warning("Could not read '%s' entry points: %s", group, exc)
        return []

    dirs: list[Path] = []
    for entry in sorted(found, key=lambda e: getattr(e, "name", "")):
        safe_name = sanitize_log_value(str(getattr(entry, "name", "?")))
        module_name = _entry_point_module(str(getattr(entry, "value", "")))
        if not module_name:
            logger.warning("Entry point %s declares no module; skipping", safe_name)
            continue
        try:
            package_dir = _resolve_package_dir(module_name)
        except Exception as exc:
            logger.warning(
                "Entry point %s could not be resolved to a package: %s",
                safe_name,
                exc,
            )
            continue
        if package_dir is None or not package_dir.is_dir():
            logger.warning(
                "Entry point %s resolves to no package directory; skipping", safe_name
            )
            continue
        if not has_manifest(package_dir):
            logger.warning(
                "Entry point %s has no plugin manifest in %s; skipping",
                safe_name,
                package_dir,
            )
            continue
        dirs.append(package_dir)

    return dirs


def merge_plugin_dirs(
    directory_dirs: list[Path],
    entry_point_dirs: list[Path],
) -> list[Path]:
    """Merge the directory scan with entry-point discovery, directory first.

    Args:
        directory_dirs: Plugin directories found under the plugins root.
        entry_point_dirs: Plugin directories advertised by installed packages.

    Returns:
        The scanned directories, followed by every entry-point directory whose
        name does not collide with one of them. A collision keeps the scanned
        directory and logs a warning naming the shadowed package.
    """
    merged = list(directory_dirs)
    by_name = {path.name: path for path in directory_dirs}

    for candidate in entry_point_dirs:
        existing = by_name.get(candidate.name)
        if existing is not None:
            if existing.resolve() != candidate.resolve():
                logger.warning(
                    "Plugin '%s' is provided by both the plugins directory (%s) and "
                    "an installed package (%s); the directory wins.",
                    sanitize_log_value(candidate.name),
                    existing,
                    candidate,
                )
            continue
        by_name[candidate.name] = candidate
        merged.append(candidate)

    return merged


__all__ = [
    "ENTRY_POINT_GROUP",
    "MANIFEST_FILENAMES",
    "find_manifest",
    "has_manifest",
    "is_entry_point_discovery_enabled",
    "iter_entry_point_plugin_dirs",
    "merge_plugin_dirs",
]
