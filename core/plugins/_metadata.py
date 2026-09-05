"""Plugin identity metadata.

Extracted from ``interface.py`` to keep that module under the 500-LOC cap. The
public import path is unchanged: ``from core.plugins.interface import
PluginMetadata`` still works (``interface`` re-exports this class).
"""

from pathlib import Path
from typing import Any


def _normalize_env_declarations(declared: Any) -> list[str]:
    """Coerce ``environment_variables`` to the plain key list consumers expect.

    Manifests use two shapes in the wild: the documented list of names
    (``[FOO_TOKEN, FOO_URL]``) and a richer list of mappings carrying operator
    documentation (``[{name: FOO_TOKEN, description: ..., required: true}]``).
    Every consumer (the loader's env namespacing, the CLI deps report) wants
    the names, and the rich form previously reached ``str.upper()`` as a dict
    and aborted plugin load with ``AttributeError``. Normalizing once here
    keeps both shapes valid and every consumer on one type.

    Entries that are neither a string nor a mapping with a ``name`` are
    dropped: a malformed declaration must not break plugin loading.
    """
    if not declared:
        return []
    keys: list[str] = []
    for entry in declared:
        if isinstance(entry, str):
            name = entry
        elif isinstance(entry, dict):
            name = str(entry.get("name") or "")
        else:
            continue
        name = name.strip()
        if name:
            keys.append(name)
    return keys


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
        dependencies: list[str] | None = None,
        required_resources: list[str] | None = None,
        optional_resources: list[str] | None = None,
        python_dependencies: list[str] | None = None,
        plugin_dependencies: dict[str, str] | None = None,
        min_core_version: str | None = None,
        max_core_version: str | None = None,
        homepage: str = "",
        license: str = "",
        tags: list[str] | None = None,
        icon: str = "",
        screenshots: list[str] | None = None,
        category: str = "Generic",
        environment_variables: list[str] | None = None,
        readiness: str = "stable",
        system: bool = False,
        tenancy: str = "shared",
        integrity_sha256: str | None = None,
        signature_ed25519: str | None = None,
        subcomponent_of: str = "",
        llm_scopes: list[dict[str, str]] | None = None,
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
        self.environment_variables = _normalize_env_declarations(environment_variables)
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

        # Optional Ed25519 publisher signature over integrity_sha256 (hex).
        # Verified against BASELITH_PLUGIN_TRUST_ROOTS when
        # BASELITH_REQUIRE_PLUGIN_SIGNATURES is enabled (core.plugins.signing).
        self.signature_ed25519 = signature_ed25519

        # Optional parent plugin/component name: when set, the Backstage export
        # emits ``spec.subcomponentOf`` so this plugin renders as a subcomponent
        # of its parent (and the parent gains a "Has subcomponents" relation).
        self.subcomponent_of = subcomponent_of or ""

        # Optional named LLM sub-policies for a plugin with more than one
        # distinct LLM pipeline (e.g. wikigen: "ingestion" vs "chat"). Each entry
        # is ``{"id": <stable-key>, "label": <display>}``; the central LLM-policy
        # console renders one provider/model selector per declared scope (in
        # addition to the plugin default), and the plugin resolves each pipeline
        # via ``resolve_governed_client_config(name, scope=<id>)`` — a scope with
        # no pin falls back to the plugin's default pin. Empty ⇒ single default
        # selector, i.e. behaviour identical to a plugin that declares nothing.
        self.llm_scopes = self._normalize_llm_scopes(llm_scopes)

    @staticmethod
    def _normalize_llm_scopes(
        raw: list[dict[str, str]] | None,
    ) -> list[dict[str, str]]:
        """Coerce declared LLM scopes to a clean ``[{"id","label"}]`` list.

        Drops entries without a non-empty ``id``; defaults a missing ``label``
        to the id, and de-duplicates on ``id`` (first wins) so the console
        renders one selector per distinct scope. Tolerant of malformed manifest
        input — a bad row is skipped, never raised.
        """
        out: list[dict[str, str]] = []
        seen: set[str] = set()
        for entry in raw or []:
            if not isinstance(entry, dict):
                continue
            scope_id = str(entry.get("id") or "").strip()
            if not scope_id or scope_id in seen:
                continue
            seen.add(scope_id)
            label = str(entry.get("label") or "").strip() or scope_id
            out.append({"id": scope_id, "label": label})
        return out

    def to_dict(self) -> dict[str, Any]:
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
            "signature_ed25519": self.signature_ed25519,
            "subcomponent_of": self.subcomponent_of,
            "llm_scopes": self.llm_scopes,
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

            with open(path, encoding="utf-8") as mf:
                data = yaml.safe_load(mf)
        else:
            import json

            with open(path, encoding="utf-8") as mf:
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
            signature_ed25519=data.get("signature_ed25519"),
            subcomponent_of=str(data.get("subcomponent_of", "")),
            llm_scopes=data.get("llm_scopes"),
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
