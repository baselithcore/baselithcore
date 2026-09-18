"""Plugin-focused checks used by ``baselith doctor``."""

import json
from importlib.metadata import PackageNotFoundError, distribution
from pathlib import Path
from typing import Any

import yaml

from core.cli.commands.doctor_checks import CheckResult


def load_manifest(plugin_dir: Path) -> dict[str, Any] | None:
    """Load a plugin manifest from JSON or YAML."""
    for ext in (".yaml", ".yml", ".json"):
        manifest_path = plugin_dir / f"manifest{ext}"
        if not manifest_path.exists():
            continue
        try:
            if ext == ".json":
                data = json.loads(manifest_path.read_text(encoding="utf-8"))
            else:
                data = yaml.safe_load(manifest_path.read_text(encoding="utf-8"))
            return data if isinstance(data, dict) else None
        except (OSError, ValueError, yaml.YAMLError):
            return None
    return None


def local_plugin_dirs() -> list[Path]:
    """Return visible plugin directories."""
    plugins_path = Path.cwd() / "plugins"
    if not plugins_path.exists():
        return []
    return [
        p
        for p in sorted(plugins_path.iterdir())
        if p.is_dir() and not p.name.startswith(".") and p.name != "__pycache__"
    ]


def check_plugins() -> CheckResult:
    """Check if plugins directory exists and local plugins have basic shape."""
    plugins_path = Path.cwd() / "plugins"
    if not plugins_path.exists():
        return CheckResult("Plugins", False, "plugins/ directory not found")
    plugins = [
        p
        for p in local_plugin_dirs()
        if (p / "plugin.py").exists()
        or any((p / f"manifest{ext}").exists() for ext in (".yaml", ".yml", ".json"))
    ]
    if not plugins:
        return CheckResult("Plugins", True, "No plugins installed (optional)")
    missing_manifest = [
        p.name
        for p in plugins
        if not any(
            (p / f"manifest{ext}").exists() for ext in (".yaml", ".yml", ".json")
        )
    ]
    missing_entrypoint = [p.name for p in plugins if not (p / "plugin.py").exists()]
    issues = []
    if missing_manifest:
        issues.append("missing manifest: " + ", ".join(missing_manifest))
    if missing_entrypoint:
        issues.append("missing plugin.py: " + ", ".join(missing_entrypoint))
    if issues:
        return CheckResult(
            "Plugins",
            False,
            f"{len(plugins)} plugin(s) found with issue(s)",
            "; ".join(issues),
            "warn",
        )
    return CheckResult("Plugins", True, f"{len(plugins)} plugin(s) found")


def check_plugin_dependencies() -> CheckResult:
    """Check manifest-declared Python dependencies for local plugins."""
    try:
        from packaging.requirements import InvalidRequirement, Requirement
    except Exception as e:
        return CheckResult("Plugin Dependencies", False, f"Error: {e}", severity="warn")
    missing: list[str] = []
    invalid: list[str] = []
    for plugin_dir in local_plugin_dirs():
        manifest = load_manifest(plugin_dir)
        if not manifest:
            continue
        for dep in manifest.get("python_dependencies", []) or []:
            if not isinstance(dep, str):
                invalid.append(f"{plugin_dir.name}:{dep!r}")
                continue
            try:
                package_name = Requirement(dep).name
            except InvalidRequirement:
                invalid.append(f"{plugin_dir.name}:{dep}")
                continue
            try:
                distribution(package_name)
            except PackageNotFoundError:
                missing.append(f"{plugin_dir.name}:{dep}")
    if invalid:
        return CheckResult(
            "Plugin Dependencies",
            False,
            "Invalid python_dependencies declaration(s)",
            ", ".join(invalid),
        )
    if missing:
        return CheckResult(
            "Plugin Dependencies",
            False,
            f"{len(missing)} missing plugin dependency declaration(s)",
            "Run: baselith plugin deps install <plugin>. Missing: "
            + ", ".join(missing),
        )
    return CheckResult("Plugin Dependencies", True, "Declared Python deps satisfied")


def frontend_build_output(plugin_dir: Path, frontend: dict[str, Any]) -> Path:
    """Resolve the build output a frontend contract declares.

    Mirrors the resolution ``baselith plugin add --docker`` performs, so the
    doctor check looks where the builder actually writes: ``path`` is relative
    to the plugin directory (default ``ui``) and ``output_dir`` is relative to
    ``path`` (default ``dist``). The legacy ``dist``/``dist_path`` spellings
    stay supported and remain relative to the plugin directory.

    Args:
        plugin_dir: Directory the manifest was loaded from.
        frontend: The manifest's ``frontend`` mapping.

    Returns:
        The directory the frontend build is expected to produce.
    """
    legacy = frontend.get("dist") or frontend.get("dist_path")
    if legacy:
        return plugin_dir / str(legacy)
    path = Path(str(frontend.get("path") or frontend.get("directory") or "ui"))
    output = Path(str(frontend.get("output_dir") or frontend.get("output") or "dist"))
    if output.is_absolute():
        return output
    base = path if path.is_absolute() else plugin_dir / path
    return base / output


def check_plugin_frontends() -> CheckResult:
    """Check whether plugins that declare frontend builds have a built dist."""
    missing: list[str] = []
    for plugin_dir in local_plugin_dirs():
        manifest = load_manifest(plugin_dir)
        if not manifest:
            continue
        frontend = manifest.get("frontend")
        if not isinstance(frontend, dict):
            continue
        dist_path = frontend_build_output(plugin_dir, frontend)
        if not dist_path.exists():
            command = frontend.get("build_command") or "npm run build"
            try:
                label: Path | str = dist_path.relative_to(plugin_dir)
            except ValueError:
                label = dist_path
            missing.append(f"{plugin_dir.name}:{label} (build: {command})")
    if missing:
        return CheckResult(
            "Plugin Frontends",
            False,
            f"{len(missing)} plugin frontend build(s) missing",
            ", ".join(missing),
            "warn",
        )
    return CheckResult("Plugin Frontends", True, "Declared frontend builds present")


__all__ = [
    "check_plugin_dependencies",
    "frontend_build_output",
    "check_plugin_frontends",
    "check_plugins",
]
