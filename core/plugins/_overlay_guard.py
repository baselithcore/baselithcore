"""Version guard for overlay entries (update-apply spec §3).

A verified overlay entry that is not newer than the bundled plugin would
shadow it after an image or checkout upgrade: the new image ships 1.5.0, the
overlay still holds a signed 1.3.0, and 1.3.0 loads. Registration asks
:func:`overlay_refusal` after the signature check and keeps the bundled plugin
when it answers. An entry whose core bounds exclude the running core is
refused too: the loader would refuse it anyway, and refusing here lets the
bundled plugin load instead of nothing.

Imported from ``plugins/__init__.py`` through :mod:`core.plugins.overlay`, so
heavier imports stay inside the functions.
"""

from __future__ import annotations

import sys
from enum import StrEnum
from pathlib import Path

_MANIFEST_FILENAMES = ("manifest.yaml", "manifest.yml", "manifest.json")
_DEFAULT_VERSION = "0.1.0"  # PluginManifestModel's default for a missing version


def read_manifest_mapping(plugin_dir: Path) -> dict[str, object] | None:
    """The parsed manifest of ``plugin_dir``; None without one, {} if not a mapping."""
    import yaml

    for name in _MANIFEST_FILENAMES:
        path = plugin_dir / name
        if path.is_file():
            data = yaml.safe_load(path.read_text(encoding="utf-8"))
            return data if isinstance(data, dict) else {}
    return None


def default_bundled_root() -> Path | None:
    """Directory of the imported ``plugins`` package (the bundled tree), if any."""
    package_file = getattr(sys.modules.get("plugins"), "__file__", None)
    return Path(package_file).resolve().parent if package_file else None


def bundled_version(bundled_root: Path | None, name: str) -> str | None:
    """Manifest version of the bundled plugin ``name``, or None when absent."""
    if bundled_root is None:
        return None
    plugin_dir = bundled_root / name
    if not plugin_dir.is_dir():
        return None
    data = read_manifest_mapping(plugin_dir)
    if data is None:
        return None
    return str(data.get("version") or _DEFAULT_VERSION)


def _bound(value: object) -> str | None:
    return None if value is None else str(value)


class OverlayRefusal(StrEnum):
    """Why an overlay entry may not shadow the bundled plugin."""

    VERSION_INVALID = "version_invalid"
    INCOMPATIBLE_CORE = "incompatible_core"
    NOT_NEWER = "not_newer"
    #: The bundled version cannot be read, so "newer" cannot be decided.
    BUNDLED_UNREADABLE = "bundled_unreadable"


def overlay_refusal_code(
    entry: Path, bundled_root: Path | None, core_version: str
) -> tuple[OverlayRefusal, str] | None:
    """Structured :func:`overlay_refusal`: ``(code, detail)``, or None when it may."""
    from core.plugins.version import SemanticVersion, check_plugin_compatibility

    data = read_manifest_mapping(entry.resolve()) or {}
    raw = str(data.get("version") or "")
    try:
        version = SemanticVersion(raw)
    except ValueError:
        return OverlayRefusal.VERSION_INVALID, repr(raw)
    problems = check_plugin_compatibility(
        core_version=core_version,
        min_core_version=_bound(data.get("min_core_version")),
        max_core_version=_bound(data.get("max_core_version")),
    )
    if problems:
        return OverlayRefusal.INCOMPATIBLE_CORE, "; ".join(problems)
    bundled = bundled_version(bundled_root, entry.name)
    if bundled is None:
        return None
    try:
        if version > SemanticVersion(bundled):
            return None
    except ValueError:
        return (
            OverlayRefusal.BUNDLED_UNREADABLE,
            f"bundled version {bundled!r} is unreadable",
        )
    return OverlayRefusal.NOT_NEWER, f"overlay {raw} <= bundled {bundled}"


def overlay_refusal(
    entry: Path, bundled_root: Path | None, core_version: str
) -> str | None:
    """Why ``entry`` must not shadow the bundled plugin, or None when it may.

    Returns:
        ``"version_invalid: ..."``, ``"incompatible_core: ..."`` or
        ``"not_newer: ..."`` (an unreadable bundled version included); None
        when the entry is newer than the bundled plugin (or there is none) and
        accepts ``core_version``.
    """
    refused = overlay_refusal_code(entry, bundled_root, core_version)
    if refused is None:
        return None
    code, detail = refused
    prefix = (
        OverlayRefusal.NOT_NEWER if code is OverlayRefusal.BUNDLED_UNREADABLE else code
    )
    return f"{prefix.value}: {detail}"


__all__ = [
    "OverlayRefusal",
    "bundled_version",
    "default_bundled_root",
    "overlay_refusal",
    "overlay_refusal_code",
    "read_manifest_mapping",
]
