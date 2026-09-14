"""Docker runtime reconciliation for enabled local plugins."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from core.cli.ui import print_error, print_info, print_success

from .add_docker import (
    _build_frontends,
    _compose,
    _load_manifest,
    _plugin_enabled,
    _probe_plugin,
    _wait_for_http,
    _write_plugin_requirements,
)


def sync_plugins_into_docker() -> int:
    """Reconcile enabled local plugins with the Docker core runtime."""
    try:
        manifests = _enabled_plugin_manifests()
        _write_plugin_requirements()
    except (OSError, ValueError) as exc:
        print_error("Cannot prepare Docker plugin runtime", str(exc))
        return 1
    if not manifests:
        print_info("No enabled plugins found; Docker runtime is already clean.")
    for plugin_name, manifest in manifests.items():
        if not isinstance(manifest.get("frontend"), dict):
            continue
        if _build_frontends(plugin_name, manifest) != 0:
            return 1
    if _compose(["build", "api"]) != 0:
        return 1
    print_success("Python dependencies installed in Docker image")
    if _compose(["up", "-d", "api"]) != 0:
        return 1
    if not _wait_for_http("/health", 200):
        print_error("Docker API did not become healthy after plugin sync.")
        return 1
    for plugin_name, manifest in manifests.items():
        if not _has_http_contract(manifest):
            continue
        if not _probe_plugin(plugin_name, manifest):
            return 1
    print_success("Plugin HTTP endpoints reachable")
    print_success("Health check passed")
    print_success("Plugin runtime synced")
    return 0


def _enabled_plugin_manifests() -> dict[str, dict[str, Any]]:
    from .add import _check_core_compatibility
    from .install_validation import validate_install_manifest

    manifests: dict[str, dict[str, Any]] = {}
    plugins_dir = Path("plugins")
    if not plugins_dir.is_dir():
        return manifests
    for plugin_dir in sorted(plugins_dir.iterdir()):
        if not plugin_dir.is_dir() or not _plugin_enabled(plugin_dir.name):
            continue
        manifest_path = _manifest_path(plugin_dir)
        if manifest_path is None:
            raise ValueError(f"Enabled plugin '{plugin_dir.name}' has no manifest")
        manifest = _load_manifest(manifest_path)
        if not manifest:
            raise ValueError(f"Invalid manifest: {manifest_path}")
        problems = validate_install_manifest(manifest)
        if problems:
            raise ValueError(
                f"Invalid installation manifest: {manifest_path}: "
                + "; ".join(problems)
            )
        if not _check_core_compatibility(manifest):
            raise ValueError(f"Core compatibility failed: {manifest_path}")
        manifests[plugin_dir.name] = manifest
    return manifests


def _manifest_path(plugin_dir: Path) -> Path | None:
    return next(
        (
            plugin_dir / f"manifest{ext}"
            for ext in (".yaml", ".yml", ".json")
            if (plugin_dir / f"manifest{ext}").is_file()
        ),
        None,
    )


def _has_http_contract(manifest: dict[str, Any]) -> bool:
    return bool(manifest.get("health_endpoint")) or isinstance(
        manifest.get("frontend"), dict
    )


__all__ = ["sync_plugins_into_docker"]
