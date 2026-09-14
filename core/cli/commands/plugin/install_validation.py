"""Static installation checks without importing a plugin or its dependencies."""

from __future__ import annotations

import re
from typing import Any

from packaging.requirements import InvalidRequirement, Requirement

from core.plugins.version import SemanticVersion, VersionConstraint


def validate_install_manifest(manifest: dict[str, Any]) -> list[str]:
    """Validate build inputs while preserving optional legacy manifest fields."""
    errors: list[str] = []
    try:
        SemanticVersion(str(manifest.get("version", "")))
    except ValueError:
        errors.append("version must be MAJOR.MINOR.PATCH")
    requirements = manifest.get("python_dependencies") or []
    if not isinstance(requirements, list):
        errors.append("python_dependencies must be a list")
    else:
        for requirement in requirements:
            try:
                if (
                    not isinstance(requirement, str)
                    or "\n" in requirement
                    or "\r" in requirement
                ):
                    raise InvalidRequirement("Invalid requirement")
                Requirement(requirement)
            except InvalidRequirement:
                errors.append("invalid python_dependencies entry")
    dependencies = manifest.get("plugin_dependencies") or {}
    if not isinstance(dependencies, (dict, list)):
        errors.append("plugin_dependencies must be a mapping or legacy list")
    else:
        for name in dependencies:
            if not isinstance(name, str) or not re.fullmatch(
                r"[a-z0-9][a-z0-9_-]*", name
            ):
                errors.append("invalid plugin dependency name")
            if isinstance(dependencies, dict):
                constraint = dependencies[name]
                try:
                    if not isinstance(constraint, str):
                        raise ValueError("Invalid constraint")
                    VersionConstraint(constraint)
                except ValueError:
                    errors.append("invalid plugin dependency version constraint")
    frontend = manifest.get("frontend")
    if frontend is not None and frontend is not False:
        if not isinstance(frontend, dict):
            errors.append("frontend must be a mapping or false")
        else:
            manager = frontend.get("package_manager")
            if manager is not None and manager not in ("npm", "pnpm", "yarn"):
                errors.append("frontend.package_manager must be npm, pnpm or yarn")
            for key in ("path", "directory", "output_dir", "output", "build_command"):
                if key in frontend and (
                    not isinstance(frontend[key], str) or not frontend[key].strip()
                ):
                    errors.append(f"frontend.{key} must be a nonempty string")
    health = manifest.get("health_endpoint")
    if health is not None and (
        not isinstance(health, str)
        or not health.startswith("/")
        or health.startswith("//")
    ):
        errors.append("health_endpoint must be a local HTTP path starting with /")
    return errors
