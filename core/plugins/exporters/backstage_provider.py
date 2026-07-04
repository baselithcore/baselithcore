"""
Backstage Software Catalog integration for BaselithCore.

Transforms the PluginRegistry into Backstage Entity Provider payloads,
enabling automatic synchronisation of plugin metadata with the Backstage
Software Catalog.

Architecture
------------
- Separation of Concerns: lives in core/plugins/exporters/, never imported
  by agent execution paths.  Entity-format rules and auxiliary entity
  builders live in entity_model.py; pattern tables in patterns.py.
- Async Everything: pattern detection runs in an executor thread (no blocking
  I/O on the event loop).  Lifecycle-hook attachment fires during LOADING →
  ACTIVE transition.
- Strong Contracts: satisfies the BackstageExporter Protocol defined in
  core/plugins/protocols.py without inheriting from it (structural typing).

Entity graph
------------
``get_provider_payload`` emits a *complete* graph — the ``baselith-core``
System, one Component per plugin, and one API entity per plugin that exposes
routers — so no reference (``spec.system``, ``spec.providesApis``,
``spec.owner``) ever dangles in the catalog.  Every entity carries the
``backstage.io/managed-by-location`` annotations required for Entity
Provider ingestion.

Lifecycle Integration
---------------------
Call ``provider.attach_lifecycle_hooks(lifecycle_manager, registry)`` once at
application startup (e.g. inside core/api/lifespan.py).  The hook fires
``detect_agentic_patterns`` asynchronously for each plugin as it reaches ACTIVE
state, pre-warming the cache so the first catalog export is instant.
"""

from __future__ import annotations

import asyncio
import inspect
from pathlib import Path
from typing import Any

from core.observability.logging import get_logger
from core.plugins.interface import Plugin, PluginMetadata
from core.plugins.lifecycle import PluginLifecycleManager, PluginState
from core.plugins.registry import PluginRegistry

from .entity_model import (
    BACKSTAGE_API_VERSION,
    DEFAULT_NAMESPACE,
    ENTITIES_PATH,
    SYSTEM_NAME,
    api_name,
    build_api_entity,
    build_system_entity,
    component_ref,
    location_annotations,
    owner_ref,
    sanitize_entity_name,
    sanitize_label_value,
)
from .patterns import (
    PATTERN_MAP,
    RESOURCE_TO_PATTERN,
    TAG_ALIASES,
    scan_source_files,
)

logger = get_logger(__name__)

# Back-compat alias — tests and downstream callers import the scan helper
# from this module.
_scan_source_files = scan_source_files

# PluginState → Backstage lifecycle string
# Backstage treats lifecycle as freeform; we use the Well-Known values.
_STATE_TO_LIFECYCLE: dict[PluginState, str] = {
    PluginState.ACTIVE: "production",
    PluginState.LOADING: "experimental",
    PluginState.INITIALIZING: "experimental",
    PluginState.LOADED: "experimental",
    PluginState.DISCOVERED: "experimental",
    PluginState.DISABLED: "deprecated",
    PluginState.FAILED: "deprecated",
    PluginState.UNLOADING: "deprecated",
}


class BackstageProvider:
    """
    Transforms BaselithCore's PluginRegistry into Backstage Entity Provider
    payloads.

    Each registered plugin becomes a Backstage Component entity (Kind: Component,
    apiVersion: backstage.io/v1alpha1).  Labels carry detected Agentic Design
    Patterns; annotations bridge to health endpoints and MkDocs documentation.

    Parameters
    ----------
    lifecycle_manager:
        The active PluginLifecycleManager — used for health-status mapping and
        lifecycle-hook registration.
    base_url:
        Base URL of the running BaselithCore instance (used to build annotation
        URLs for health and plugin API endpoints).
    docs_base_url:
        Base URL of the documentation site (MkDocs / TechDocs).
    catalog_source_location:
        Backstage source-location prefix pointing to the repository root
        (e.g. "url:https://github.com/org/baselithcore/blob/main/").
    """

    def __init__(
        self,
        lifecycle_manager: PluginLifecycleManager,
        base_url: str = "http://localhost:8000",
        docs_base_url: str = "https://docs.baselith.internal",
        catalog_source_location: str = "url:https://github.com/baselith/core/blob/main/",
    ) -> None:
        self._lifecycle = lifecycle_manager
        self._base_url = base_url.rstrip("/")
        self._docs_base_url = docs_base_url.rstrip("/")
        self._catalog_source_location = catalog_source_location
        self._entities_url = f"{self._base_url}{ENTITIES_PATH}"
        # plugin_name → detected pattern label list (pre-warmed by lifecycle hooks)
        self._pattern_cache: dict[str, list[str]] = {}

    # ── Public API (satisfies BackstageExporter protocol) ─────────────────────

    async def export_entity(self, plugin: Plugin) -> dict[str, Any]:
        """Serialize a single plugin to a Backstage Component entity dict."""
        return await self.to_catalog_info(plugin)

    async def export_all(self, registry: PluginRegistry) -> list[dict[str, Any]]:
        """Serialize all registered plugins concurrently (Components only)."""
        tasks = [self.export_entity(p) for p in registry.get_all()]
        return list(await asyncio.gather(*tasks))

    async def export_graph(self, registry: PluginRegistry) -> list[dict[str, Any]]:
        """
        Serialize the complete entity graph: System + Components + APIs.

        Backstage rejects (or renders as broken relations) references to
        entities that are never ingested.  Components emitted by this provider
        reference ``spec.system: baselith-core`` and ``spec.providesApis:
        [<plugin>-api]`` — this method emits those referenced entities too, so
        the catalog stays internally consistent from a single payload.
        """
        components = await self.export_all(registry)
        entities: list[dict[str, Any]] = [
            build_system_entity(
                entities_url=self._entities_url,
                base_url=self._base_url,
                docs_base_url=self._docs_base_url,
            ),
            *components,
        ]
        for component in components:
            if not component["spec"].get("providesApis"):
                continue
            meta = component["metadata"]
            entities.append(
                build_api_entity(
                    plugin_name=meta["name"],
                    title=str(meta.get("title", meta["name"])),
                    description=str(meta.get("description", "")),
                    owner=str(component["spec"]["owner"]),
                    lifecycle=str(component["spec"]["lifecycle"]),
                    entities_url=self._entities_url,
                    base_url=self._base_url,
                    tags=list(meta.get("tags", [])),
                )
            )
        return entities

    async def get_provider_payload(self, registry: PluginRegistry) -> dict[str, Any]:
        """
        Build the full Entity Provider mutation payload.

        Compatible with Backstage's EntityProvider.applyMutation() "full"
        mutation contract — replace the entire set of owned entities on each
        push.  Includes the System root and API entities alongside the plugin
        Components (see :meth:`export_graph`).

        Returns
        -------
        {
            "type": "full",
            "entities": [ ... catalog-info entity dicts ... ]
        }
        """
        entities = await self.export_graph(registry)
        return {"type": "full", "entities": entities}

    async def to_catalog_info(self, plugin: Plugin) -> dict[str, Any]:
        """
        Map a plugin to a Backstage Component entity dict.

        The returned dict serializes directly to a valid catalog-info.yaml
        (apiVersion: backstage.io/v1alpha1, kind: Component).

        Mapping
        -------
        PluginMetadata.name          → metadata.name (format-sanitised)
        PluginMetadata.version       → labels[app.kubernetes.io/version]
        PluginMetadata.description   → metadata.description
        PluginMetadata.author        → spec.owner (group:default/<slug> ref)
        PluginMetadata.tags          → metadata.tags  (+ category tag)
        PluginMetadata.category      → metadata.labels[baselith.ai/category]
        PluginMetadata.readiness     → metadata.labels[baselith.ai/readiness]
        PluginMetadata.homepage      → metadata.links + annotations
        Detected patterns            → metadata.labels  (baselith.ai/pattern-*)
        PluginState                  → spec.lifecycle
        plugin.get_routers()         → spec.providesApis  (if non-empty)
        plugin_dependencies          → spec.dependsOn (component:default/<dep>)
        """
        meta = plugin.metadata
        patterns = await self.detect_agentic_patterns(plugin)
        lifecycle = await self.get_health_status(meta.name)
        entity_name = sanitize_entity_name(meta.name)

        # ── Labels ────────────────────────────────────────────────────────────
        labels: dict[str, str] = dict.fromkeys(patterns, "true")
        labels["baselith.ai/readiness"] = sanitize_label_value(meta.readiness)
        labels["baselith.ai/category"] = sanitize_label_value(
            meta.category.lower().replace(" ", "-")
        )
        if meta.version:
            labels["app.kubernetes.io/version"] = sanitize_label_value(meta.version)

        # ── Annotations ───────────────────────────────────────────────────────
        annotations: dict[str, str] = {
            # Required for Entity Provider ingestion: where this entity is
            # managed from (the live export endpoint of this instance).
            **location_annotations(self._entities_url),
            # TechDocs: point to the plugin's docs directory (MkDocs)
            "backstage.io/techdocs-ref": f"dir:./plugins/{meta.name}",
            # The registry identity, verbatim (may differ from the sanitised
            # metadata.name) — lets integrations map back to the plugin.
            "baselith.ai/plugin-id": meta.name,
            # Health bridge: live status from BaselithCore's health endpoint
            "baselith.ai/health-url": f"{self._base_url}/health",
            # Plugin admin API: current state, config, metrics
            "baselith.ai/plugin-api-url": (f"{self._base_url}/api/plugins/{meta.name}"),
            # Manifest source: direct link to the manifest.yaml in the repo
            "baselith.ai/manifest-url": (
                f"{self._catalog_source_location}plugins/{meta.name}/manifest.yaml"
            ),
        }
        if meta.homepage:
            annotations["backstage.io/source-location"] = f"url:{meta.homepage}"
        if meta.license:
            annotations["baselith.ai/license"] = meta.license
        if meta.min_core_version:
            annotations["baselith.ai/min-core-version"] = meta.min_core_version

        # ── Tags ──────────────────────────────────────────────────────────────
        tags: list[str] = [t.lower().replace(" ", "-") for t in meta.tags]
        category_tag = meta.category.lower().replace(" ", "-")
        if category_tag not in tags:
            tags.append(category_tag)

        # ── Links ─────────────────────────────────────────────────────────────
        links: list[dict[str, str]] = [
            {
                "url": f"{self._base_url}/api/plugins/{meta.name}",
                "title": "Plugin API",
                "icon": "dashboard",
            },
            {
                "url": f"{self._docs_base_url}/plugins/{meta.name}",
                "title": "Documentation",
                "icon": "docs",
            },
        ]
        if meta.homepage:
            links.insert(0, {"url": meta.homepage, "title": "Homepage", "icon": "web"})

        # ── Spec ──────────────────────────────────────────────────────────────
        provides_apis: list[str] = [api_name(meta.name)] if plugin.get_routers() else []
        depends_on: list[str] = [
            component_ref(dep) for dep in meta.plugin_dependencies.keys()
        ]

        return {
            "apiVersion": BACKSTAGE_API_VERSION,
            "kind": "Component",
            "metadata": {
                "name": entity_name,
                "namespace": DEFAULT_NAMESPACE,
                "title": _slugify_title(meta.name),
                "description": meta.description,
                "labels": labels,
                "annotations": annotations,
                "tags": tags,
                "links": links,
            },
            "spec": {
                "type": "baselith-plugin",
                "lifecycle": lifecycle,
                "owner": owner_ref(meta.author),
                "system": SYSTEM_NAME,
                "providesApis": provides_apis,
                "dependsOn": depends_on,
            },
        }

    async def detect_agentic_patterns(self, plugin: Plugin) -> list[str]:
        """
        Identify the Agentic Design Patterns implemented by this plugin.

        Detection runs three strategies in order, merging results:

        1. **Tag-based**: intersect manifest.yaml tags against known pattern
           aliases (fastest — zero I/O).
        2. **Resource-based**: map required_resources / optional_resources to
           known patterns (zero I/O).
        3. **Source-scan**: async grep of .py files inside the plugin directory
           for ``from core.X`` / ``import core.X`` import statements (runs in
           an executor to avoid blocking the event loop).

        Results are cached per plugin name; call ``invalidate_pattern_cache``
        after a hot-reload.
        """
        name = plugin.metadata.name
        if name in self._pattern_cache:
            return self._pattern_cache[name]

        detected: list[str] = []

        for label in self._detect_from_tags(plugin.metadata):
            if label not in detected:
                detected.append(label)

        for label in self._detect_from_resources(plugin.metadata):
            if label not in detected:
                detected.append(label)

        for label in await self._detect_from_source(plugin):
            if label not in detected:
                detected.append(label)

        self._pattern_cache[name] = detected
        logger.debug("BackstageProvider: patterns for '%s': %s", name, detected)
        return detected

    async def get_health_status(self, plugin_name: str) -> str:
        """
        Map the plugin's PluginState to a Backstage lifecycle string.

        Returns one of: "production", "experimental", "deprecated", "unknown"
        """
        state = self._lifecycle.get_state(plugin_name)
        if state is None:
            return "unknown"
        return _STATE_TO_LIFECYCLE.get(state, "unknown")

    def invalidate_pattern_cache(self, plugin_name: str) -> None:
        """
        Remove the cached pattern detection result for a plugin.

        Call this after a hot-reload so the next export re-scans the source.
        """
        self._pattern_cache.pop(plugin_name, None)

    def register_plugin_hook(
        self,
        lifecycle_manager: PluginLifecycleManager,
        plugin: Plugin,
    ) -> None:
        """
        Register the ``on_after_init`` cache-warming hook for a single plugin.

        Called by HotReloadController just before ``transition_to_active`` so
        that plugins enabled at runtime (after startup) are covered the same
        way as plugins loaded during the initial boot sequence.

        Parameters
        ----------
        lifecycle_manager:
            The active PluginLifecycleManager.
        plugin:
            The plugin instance about to become ACTIVE.
        """

        async def _warm_cache(p: Plugin | None) -> None:
            if p is None:
                return
            try:
                await self.detect_agentic_patterns(p)
                logger.info(
                    "BackstageProvider: pattern cache warmed for '%s'",
                    p.metadata.name,
                )
            except Exception as exc:
                logger.warning(
                    "BackstageProvider: pattern detection failed for '%s': %s",
                    p.metadata.name,
                    exc,
                )

        lifecycle_manager.register_hook(
            plugin.metadata.name, "on_after_init", _warm_cache
        )
        logger.debug(
            "BackstageProvider: registered on_after_init hook for '%s'",
            plugin.metadata.name,
        )

    def attach_lifecycle_hooks(
        self,
        lifecycle_manager: PluginLifecycleManager,
        registry: PluginRegistry,
    ) -> None:
        """
        Register ``on_after_init`` hooks so pattern detection runs
        automatically as each plugin reaches ACTIVE state.

        Call this once during application startup (e.g. in lifespan.py).
        The hook fires asynchronously; a failure never blocks plugin activation.

        Parameters
        ----------
        lifecycle_manager:
            The same PluginLifecycleManager passed to __init__.
        registry:
            The active PluginRegistry — used to enumerate already-registered
            plugins so hooks are attached even for plugins loaded before this
            call.
        """

        async def _warm_cache(plugin: Plugin | None) -> None:
            if plugin is None:
                return
            try:
                await self.detect_agentic_patterns(plugin)
                logger.info(
                    "BackstageProvider: pattern cache warmed for '%s'",
                    plugin.metadata.name,
                )
            except Exception as exc:
                logger.warning(
                    "BackstageProvider: pattern detection failed for '%s': %s",
                    plugin.metadata.name,
                    exc,
                )

        for plugin in registry.get_all():
            lifecycle_manager.register_hook(
                plugin.metadata.name, "on_after_init", _warm_cache
            )
            logger.debug(
                "BackstageProvider: registered on_after_init hook for '%s'",
                plugin.metadata.name,
            )

    # ── Private helpers ────────────────────────────────────────────────────────

    def _detect_from_tags(self, meta: PluginMetadata) -> list[str]:
        """Return pattern labels whose short name matches a manifest tag."""
        tag_set = {t.lower().replace(" ", "-") for t in meta.tags}
        return [label for alias, label in TAG_ALIASES.items() if alias in tag_set]

    def _detect_from_resources(self, meta: PluginMetadata) -> list[str]:
        """Return pattern labels implied by required/optional resources."""
        resources = set(meta.required_resources + meta.optional_resources)
        return [
            label
            for resource, label in RESOURCE_TO_PATTERN.items()
            if resource in resources
        ]

    async def _detect_from_source(self, plugin: Plugin) -> list[str]:
        """
        Async wrapper: resolve the plugin directory, then scan in an executor.
        """
        try:
            plugin_module = inspect.getmodule(plugin.__class__)
            if not plugin_module or not getattr(plugin_module, "__file__", None):
                return []
            plugin_dir = Path(plugin_module.__file__).parent  # type: ignore[arg-type]
        except Exception:
            return []

        return await asyncio.to_thread(scan_source_files, plugin_dir)


# ── Module-level helpers (no self state needed) ───────────────────────────────


def _slugify_title(name: str) -> str:
    """Convert a plugin slug (kebab/snake) to a human-readable title."""
    return name.replace("-", " ").replace("_", " ").title()


# Re-exported for callers that previously imported the table from this module.
_PATTERN_MAP = PATTERN_MAP
