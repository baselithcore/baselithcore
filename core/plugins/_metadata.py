"""Plugin identity metadata.

Extracted from ``interface.py`` to keep that module under the 500-LOC cap. The
public import path is unchanged: ``from core.plugins.interface import
PluginMetadata`` still works (``interface`` re-exports this class).
"""

from pathlib import Path
from typing import Any, Dict, List, Optional


class PluginMetadata:
    """
    Identity and dependency container for a plugin.

    This class encapsulates all static information about a plugin,
    including its versioning constraints, required core resources,
    and external Python dependencies.
    """

    def __init__(
        self,
        name: str,
        version: str,
        description: str = "",
        author: str = "",
        dependencies: Optional[List[str]] = None,
        required_resources: Optional[List[str]] = None,
        optional_resources: Optional[List[str]] = None,
        python_dependencies: Optional[List[str]] = None,
        plugin_dependencies: Optional[Dict[str, str]] = None,
        min_core_version: Optional[str] = None,
        max_core_version: Optional[str] = None,
        homepage: str = "",
        license: str = "",
        tags: Optional[List[str]] = None,
        icon: str = "",
        screenshots: Optional[List[str]] = None,
        category: str = "Generic",
        environment_variables: Optional[List[str]] = None,
        readiness: str = "stable",
        system: bool = False,
        tenancy: str = "shared",
        integrity_sha256: Optional[str] = None,
    ):
        """
        Initialize plugin metadata.

        Args:
            name: Unique identifier for the plugin (e.g., "auth-provider").
            version: Semantic version string (e.g., "1.0.0").
            description: Short summary of the plugin's purpose.
            author: Name or entity responsible for the plugin.
            dependencies: [Legacy] List of required plugin names.
            required_resources: Core system components needed (e.g., ["postgres", "llm"]).
            optional_resources: Components that enhance the plugin if present.
            python_dependencies: Pip-installable packages required (e.g., ["requests>=2.0.0"]).
            plugin_dependencies: Map of plugin names to version constraints.
            min_core_version: Minimum compatible framework version.
            max_core_version: Maximum compatible framework version.
            homepage: URL to documentation or source code.
            license: SPDX license identifier (e.g., "MIT").
            tags: Keywords for categorization in the UI or registry.
            icon: Relative path to icon image or URL.
            screenshots: List of relative paths to feature screenshots.
            category: Primary category (e.g., "AI", "Security", "Utilities").
            environment_variables: List of required ENV vars.
            readiness: Development stage (e.g., "alpha", "beta", "stable").
            system: Marks the plugin as platform **infrastructure** (e.g. ``auth``)
                rather than a user-facing app. System plugins are hidden from the
                user-facing navigation/catalog and their UI tabs default to
                admin-only (effective-admin / wildcard) instead of default-allow.
                Their public backend routes (e.g. the login screen) are
                unaffected — this governs *visibility*, not route reachability.
            tenancy: Per-plugin tenancy model — ``"shared"`` (default) scopes the
                plugin's data by the deployment-derived tenant
                (``get_current_tenant_id``); ``"personal"`` forces **1 user = 1
                tenant** regardless of how the deployment resolves tenancy. The
                plugin's store resolves its effective scope key via
                ``core.context.resolve_plugin_tenant(self.metadata.tenancy)``.
                Unknown values normalize to ``"shared"``.
        """
        self.name = name
        self.version = version
        self.description = description
        self.author = author

        # Legacy support for 'dependencies'
        self.dependencies = dependencies or []

        # New dependency system
        self.python_dependencies = python_dependencies or []
        self.plugin_dependencies = plugin_dependencies or {}

        # Resources
        self.required_resources = required_resources or []
        self.optional_resources = optional_resources or []

        # Versioning
        self.min_core_version = min_core_version
        self.max_core_version = max_core_version

        # Metadata
        self.homepage = homepage
        self.license = license
        self.tags = tags or []

        # Professional Upgrade Fields
        self.icon = icon
        self.screenshots = screenshots or []
        self.category = category
        self.environment_variables = environment_variables or []
        self.readiness = readiness
        # Platform-infrastructure marker (auth, …): hidden from user-facing nav,
        # tabs default to admin-only. See constructor docstring.
        self.system = system

        # Per-plugin tenancy model ("shared" | "personal"). Drives the scope key
        # a plugin's store resolves via core.context.resolve_plugin_tenant.
        # Normalize unknown values to "shared" (deployment-derived tenant).
        self.tenancy = tenancy if tenancy in ("shared", "personal") else "shared"

        # Optional SHA-256 of the plugin's executable surface (manifest + .py/.pyi).
        # When set, the loader verifies the digest before exec_module.
        self.integrity_sha256 = integrity_sha256

    def to_dict(self) -> Dict[str, Any]:
        """
        Serialize metadata to a dictionary for API or logging export.
        """
        return {
            "name": self.name,
            "version": self.version,
            "description": self.description,
            "author": self.author,
            "dependencies": self.dependencies,  # Legacy
            "python_dependencies": self.python_dependencies,
            "plugin_dependencies": self.plugin_dependencies,
            "required_resources": self.required_resources,
            "optional_resources": self.optional_resources,
            "min_core_version": self.min_core_version,
            "max_core_version": self.max_core_version,
            "homepage": self.homepage,
            "license": self.license,
            "tags": self.tags,
            "icon": self.icon,
            "screenshots": self.screenshots,
            "category": self.category,
            "environment_variables": self.environment_variables,
            "readiness": self.readiness,
            "system": self.system,
            "tenancy": self.tenancy,
            "integrity_sha256": self.integrity_sha256,
        }

    @classmethod
    def from_file(cls, path: Path) -> "PluginMetadata":
        """
        Load metadata from a manifest file.

        Args:
            path: Path to the manifest file (.yaml, .yml, or .json).

        Returns:
            PluginMetadata instance.
        """
        if path.suffix in (".yaml", ".yml"):
            import yaml

            with open(path, "r", encoding="utf-8") as mf:
                data = yaml.safe_load(mf)
        else:
            import json

            with open(path, "r", encoding="utf-8") as mf:
                data = json.load(mf)

        return cls(
            name=data.get("name", ""),
            version=data.get("version", "0.1.0"),
            description=data.get("description", ""),
            author=data.get("author", ""),
            dependencies=data.get("dependencies"),
            required_resources=data.get("required_resources"),
            optional_resources=data.get("optional_resources"),
            python_dependencies=data.get("python_dependencies"),
            plugin_dependencies=data.get("plugin_dependencies"),
            min_core_version=data.get("min_core_version"),
            max_core_version=data.get("max_core_version"),
            homepage=data.get("homepage", ""),
            license=data.get("license", ""),
            tags=data.get("tags"),
            icon=data.get("icon", ""),
            screenshots=data.get("screenshots"),
            category=data.get("category", "Generic"),
            environment_variables=data.get("environment_variables"),
            readiness=data.get("readiness", "stable"),
            system=bool(data.get("system", False)),
            tenancy=str(data.get("tenancy", "shared")),
            integrity_sha256=data.get("integrity_sha256"),
        )

    def to_json_file(self, path: Path) -> None:
        """
        Save metadata to a manifest file.

        Args:
            path: Path to the manifest JSON file to write.
        """
        import json

        data = self.to_dict()
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=4)
