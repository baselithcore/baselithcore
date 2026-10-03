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
import site
import sysconfig
from collections.abc import Iterable
from importlib.metadata import entry_points
from pathlib import Path
from typing import Any

from core.config.plugins import installed_plugins_dir
from core.observability.logging import get_logger
from core.plugins.config_file import PluginConfigs, plugin_enabled
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


def _site_package_roots() -> list[Path]:
    """Every directory an installed distribution's packages can live in."""
    roots = [*site.getsitepackages(), site.getusersitepackages()]
    roots += [sysconfig.get_path("purelib"), sysconfig.get_path("platlib")]
    return [Path(root) for root in roots if root]


def bundled_plugins_root(plugins_dir: Path) -> Path | None:
    """The installed distribution's ``plugins`` package, as a second root.

    A wheel install reads its bundled plugins from ``site-packages`` while
    installs write to the project's own ``./plugins`` (see
    :func:`core.config.plugins.plugin_install_root`); once that directory
    exists it becomes the configured root and, alone, would hide every
    plugin the framework ships.

    Args:
        plugins_dir: The configured root the caller already scans.

    Returns:
        The bundled package directory, or ``None`` when it *is* the configured
        root, does not exist, or is a source checkout — there the configured
        root is the checkout's own tree, and adding it beside an unrelated
        root would leak the repository's plugins into it.
    """
    bundled = _installed_bundle()
    if bundled is None:
        return None
    if plugins_dir.exists() and plugins_dir.resolve() == bundled.resolve():
        return None
    return bundled


def _installed_bundle() -> Path | None:
    """The distribution's ``plugins`` package when it lives in site-packages.

    ``None`` in a source checkout or an editable install, where the package is
    the repository's own tree — the user's root, not a shipped bundle.
    """
    bundled = installed_plugins_dir()
    if not bundled.is_dir():
        return None
    resolved = bundled.resolve()
    installed = any(
        resolved.is_relative_to(root.resolve()) for root in _site_package_roots()
    )
    return bundled if installed else None


def is_bundled_install_dir(plugin_dir: Path) -> bool:
    """Whether ``plugin_dir`` is a plugin shipped inside the installed wheel.

    Such a plugin is opt-in (see
    :func:`core.plugins.config_file.plugin_enabled`): it runs only when the
    plugin configuration names it. True only for a directory under the
    installed distribution's ``plugins`` package in ``site-packages`` — never
    for the user's own root, a source checkout, an overlay entry or a
    ``baselith.plugins`` entry-point package.

    Args:
        plugin_dir: A discovered plugin directory.
    """
    bundled = _installed_bundle()
    if bundled is None:
        return False
    try:
        return plugin_dir.resolve().is_relative_to(bundled.resolve())
    except OSError:
        return False


def bundled_plugin_dir(name: str) -> Path | None:
    """The installed wheel's own copy of plugin ``name``, if it ships one.

    Args:
        name: Directory name, or its ``-``/``_`` variant.

    Returns:
        The bundled plugin directory, or ``None`` when there is no installed
        bundle (a source checkout) or it carries no such plugin.
    """
    bundled = _installed_bundle()
    if bundled is None or not name or "/" in name or name.startswith((".", "_")):
        return None
    for candidate in dict.fromkeys((name, name.replace("-", "_"))):
        path = bundled / candidate
        if path.is_dir() and not path.is_symlink() and has_manifest(path):
            return path
    return None


#: Bundled plugin names already reported as available-but-disabled.
_ANNOUNCED_BUNDLED: set[str] = set()


def announce_disabled_bundled(
    configs: PluginConfigs, plugin_dirs: Iterable[Path]
) -> list[str]:
    """Log once, at INFO, the bundled plugins the configuration leaves off.

    Args:
        configs: The plugin configuration the runtime applies.
        plugin_dirs: The discovered plugin directories.

    Returns:
        The sorted directory names of the bundled plugins that will not run.
    """
    disabled = sorted(
        {
            path.name
            for path in plugin_dirs
            if is_bundled_install_dir(path)
            and not plugin_enabled(configs, path.name, path.name, bundled=True)
        }
    )
    fresh = [name for name in disabled if name not in _ANNOUNCED_BUNDLED]
    if fresh:
        _ANNOUNCED_BUNDLED.update(fresh)
        logger.info(
            "🔌 Bundled plugins are opt-in; available but not enabled: %s. "
            "Enable one with `baselith plugin enable <name>` or an entry "
            "'<name>: {enabled: true}' in configs/plugins.yaml.",
            ", ".join(fresh),
        )
    return disabled


def with_bundled_plugins(plugins_dir: Path, scanned: list[Path]) -> list[Path]:
    """Append the bundled plugins to a scan of ``plugins_dir``.

    Args:
        plugins_dir: The configured root ``scanned`` came from.
        scanned: Plugin directories found under ``plugins_dir``.

    Returns:
        ``scanned``, then each bundled plugin whose name it does not already
        hold — the configured root wins a clash, as it does against an
        entry-point plugin.
    """
    bundled = bundled_plugins_root(plugins_dir)
    if bundled is None:
        return scanned
    shipped = [
        item
        for item in sorted(bundled.iterdir())
        if item.is_dir()
        and not item.is_symlink()
        and not item.name.startswith((".", "_"))
        and ((item / "plugin.py").exists() or (item / "__init__.py").exists())
    ]
    return merge_plugin_dirs(scanned, shipped)


def apply_overlay(directory_dirs: list[Path], overlay_dirs: list[Path]) -> list[Path]:
    """Replace scanned plugin dirs by same-named verified overlay entries.

    Args:
        directory_dirs: Plugin directories found under the plugins root.
        overlay_dirs: Registered overlay entries (``core.plugins.overlay``).

    Returns:
        The scan in its original order with overlaid names swapped for their
        overlay path, followed by overlay entries with no bundled counterpart.
    """
    by_name = {path.name: path for path in overlay_dirs}
    merged = [by_name.pop(path.name, path) for path in directory_dirs]
    return merged + [by_name[name] for name in sorted(by_name)]


__all__ = [
    "ENTRY_POINT_GROUP",
    "MANIFEST_FILENAMES",
    "announce_disabled_bundled",
    "apply_overlay",
    "bundled_plugin_dir",
    "bundled_plugins_root",
    "find_manifest",
    "has_manifest",
    "is_bundled_install_dir",
    "is_entry_point_discovery_enabled",
    "iter_entry_point_plugin_dirs",
    "merge_plugin_dirs",
    "with_bundled_plugins",
]
