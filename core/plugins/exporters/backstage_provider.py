"""
Backstage Software Catalog integration for BaselithCore.

Transforms the PluginRegistry into Backstage Entity Provider payloads,
enabling automatic synchronisation of plugin metadata with the Backstage
Software Catalog.

Architecture
------------
- Separation of Concerns: lives in core/plugins/exporters/, never imported
  by agent execution paths.  Entity-format rules and auxiliary entity
  builders live in entity_model.py; pattern tables in patterns.py; plugin
  introspection in introspection.py; graph assembly in graph.py; the
  per-plugin OpenAPI slicer in api_definition.py.
- Async Everything: pattern detection runs in an executor thread (no blocking
  I/O on the event loop).  Lifecycle-hook attachment fires during LOADING →
  ACTIVE transition.
- Strong Contracts: satisfies the BackstageExporter Protocol defined in
  core/plugins/protocols.py without inheriting from it (structural typing).

Entity graph
------------
``get_provider_payload`` emits a *complete* graph — the ``baselith`` Domain,
the ``baselith-core`` System, one Component per plugin, one API entity per
plugin that exposes routers, plus the Group entities backing every
``spec.owner`` reference and the Resource entities backing every declared
``required_resources`` dependency — so no reference ever dangles in the
catalog.  Every entity carries the ``backstage.io/managed-by-location``
annotations required for Entity Provider ingestion.

Semantics
---------
``spec.lifecycle`` reflects the plugin's *maturity* (manifest ``readiness``),
per Backstage convention.  The live runtime state (PluginState) is exported
as the ``baselith.ai/runtime-state`` label instead, so operational health and
maturity never get conflated.

Lifecycle Integration
---------------------
Call ``provider.attach_lifecycle_hooks(lifecycle_manager, registry)`` once at
application startup (e.g. inside core/api/lifespan.py).  The hook fires
``detect_agentic_patterns`` asynchronously for each plugin as it reaches ACTIVE
state, pre-warming the cache so the first catalog export is instant.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Coroutine
from typing import Any

from core.observability.logging import get_logger
from core.plugins.interface import Plugin
from core.plugins.lifecycle import PluginLifecycleManager, PluginState
from core.plugins.registry import PluginRegistry

from .api_definition import build_plugin_api_definition, collect_route_selectors
from .entity_model import (
    BACKSTAGE_API_VERSION,
    DEFAULT_NAMESPACE,
    ENTITIES_PATH,
    SYSTEM_NAME,
    api_name,
    build_component_links,
    component_ref,
    location_annotations,
    owner_ref,
    readiness_to_lifecycle,
    resource_name,
    resource_ref,
    sanitize_entity_name,
    sanitize_label_value,
)
from .graph import export_entity_graph
from .introspection import (
    detect_from_resources,
    detect_from_source,
    detect_from_tags,
    plugin_has_techdocs,
)
from .patterns import PATTERN_MAP, scan_source_files

logger = get_logger(__name__)

# Back-compat alias — tests and downstream callers import the scan helper
# from this module.
_scan_source_files = scan_source_files

# PluginState → Backstage lifecycle string (used by get_health_status, which
# feeds operational views — NOT spec.lifecycle, which comes from the manifest
# readiness).
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
        The active PluginLifecycleManager — used for runtime-state mapping and
        lifecycle-hook registration.
    base_url:
        Base URL of the running BaselithCore instance (used to build annotation
        URLs for health and plugin API endpoints).
    docs_base_url:
        Base URL of the documentation site (MkDocs / TechDocs).
    catalog_source_location:
        Backstage source-location prefix pointing to the repository root
        (e.g. "url:https://github.com/org/baselithcore/blob/main/").
    openapi_supplier:
        Zero-arg callable returning the application's OpenAPI document
        (e.g. ``app.openapi``).  When provided, each plugin API entity embeds
        an inline OpenAPI definition scoped to the plugin's route prefix;
        otherwise the definition falls back to a ``$text`` reference.
    docs_base_url:
        Docs site base URL; ``None`` (default) omits Documentation links —
        a link to an unconfigured host is a broken link in the catalog UI.
    plugin_link_template:
        Optional "Manage Plugin" link template (``{plugin}`` placeholder,
        e.g. ``http://host:8000/baselithcontrol/#/plugin/{plugin}``); ``None``
        omits it.  Links must be browser-renderable — API endpoints stay in
        annotations.
    """

    def __init__(
        self,
        lifecycle_manager: PluginLifecycleManager,
        base_url: str = "http://localhost:8000",
        docs_base_url: str | None = None,
        catalog_source_location: str = "url:https://github.com/baselith/core/blob/main/",
        openapi_supplier: Callable[[], dict[str, Any]] | None = None,
        plugin_link_template: str | None = None,
    ) -> None:
        self._lifecycle = lifecycle_manager
        self._base_url = base_url.rstrip("/")
        self._docs_base_url = docs_base_url.rstrip("/") if docs_base_url else None
        self._catalog_source_location = catalog_source_location
        self._openapi_supplier = openapi_supplier
        self._plugin_link_template = plugin_link_template
        self._entities_url = f"{self._base_url}{ENTITIES_PATH}"
        # plugin_name → detected pattern label list (pre-warmed by lifecycle hooks)
        self._pattern_cache: dict[str, list[str]] = {}

    # ── Read-only wiring exposed to the graph assembler ───────────────────────

    @property
    def base_url(self) -> str:
        """Base URL of the running framework instance."""
        return self._base_url

    @property
    def docs_base_url(self) -> str | None:
        """Base URL of the documentation site (None when unconfigured)."""
        return self._docs_base_url

    @property
    def entities_url(self) -> str:
        """Full URL of the Entity Provider export endpoint."""
        return self._entities_url

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
        Serialize the complete entity graph: Domain + System + Groups +
        Resources + Components + APIs (see :mod:`.graph`).
        """
        return await export_entity_graph(self, registry)

    async def get_provider_payload(self, registry: PluginRegistry) -> dict[str, Any]:
        """
        Build the full Entity Provider mutation payload.

        Compatible with Backstage's EntityProvider.applyMutation() "full"
        mutation contract — replace the entire set of owned entities on each
        push.  Includes the Domain/System roots, owner Groups, shared
        Resources, and API entities alongside the plugin Components (see
        :meth:`export_graph`).

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
        PluginMetadata.readiness     → spec.lifecycle (maturity) + label
        PluginMetadata.homepage      → metadata.links
        Detected patterns            → metadata.labels  (baselith.ai/pattern-*)
        PluginState                  → labels[baselith.ai/runtime-state]
        plugin.get_routers()         → spec.providesApis  (if non-empty)
        plugin_dependencies          → spec.dependsOn (component:default/<dep>)
        required_resources           → spec.dependsOn (resource:default/<res>)
        optional_resources           → annotations[baselith.ai/optional-resources]
        """
        meta = plugin.metadata
        patterns = await self.detect_agentic_patterns(plugin)
        entity_name = sanitize_entity_name(meta.name)
        state = self._lifecycle.get_state(meta.name)
        runtime_state = state.value if state is not None else "unknown"

        # ── Labels ────────────────────────────────────────────────────────────
        labels: dict[str, str] = dict.fromkeys(patterns, "true")
        labels["baselith.ai/readiness"] = sanitize_label_value(meta.readiness)
        labels["baselith.ai/runtime-state"] = sanitize_label_value(
            str(runtime_state).lower()
        )
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
            # Where the plugin's source lives inside the repository.
            "backstage.io/source-location": (
                f"{self._catalog_source_location}plugins/{meta.name}/"
            ),
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
        # TechDocs: only advertise docs that can actually build — a ref
        # pointing at a directory without mkdocs.yml renders a broken Docs tab.
        if plugin_has_techdocs(plugin):
            annotations["backstage.io/techdocs-ref"] = (
                f"{self._catalog_source_location}plugins/{meta.name}"
            )
        if meta.license:
            annotations["baselith.ai/license"] = meta.license
        if meta.min_core_version:
            annotations["baselith.ai/min-core-version"] = meta.min_core_version
        if meta.optional_resources:
            annotations["baselith.ai/optional-resources"] = ",".join(
                sorted({resource_name(r) for r in meta.optional_resources})
            )

        # ── Tags ──────────────────────────────────────────────────────────────
        tags: list[str] = [t.lower().replace(" ", "-") for t in meta.tags]
        category_tag = meta.category.lower().replace(" ", "-")
        if category_tag not in tags:
            tags.append(category_tag)

        # ── Links (browser-renderable only; machine endpoints → annotations) ──
        links = build_component_links(
            plugin_name=meta.name,
            homepage=meta.homepage,
            plugin_link_template=self._plugin_link_template,
            docs_base_url=self._docs_base_url,
        )

        # ── Spec ──────────────────────────────────────────────────────────────
        provides_apis: list[str] = [api_name(meta.name)] if plugin.get_routers() else []
        depends_on: list[str] = [
            component_ref(dep) for dep in meta.plugin_dependencies.keys()
        ]
        for res in meta.required_resources:
            ref = resource_ref(res)
            if ref not in depends_on:
                depends_on.append(ref)

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
                "lifecycle": readiness_to_lifecycle(meta.readiness),
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
        for label in (
            *detect_from_tags(plugin.metadata),
            *detect_from_resources(plugin.metadata),
            *(await detect_from_source(plugin)),
        ):
            if label not in detected:
                detected.append(label)

        self._pattern_cache[name] = detected
        logger.debug("BackstageProvider: patterns for '%s': %s", name, detected)
        return detected

    async def get_health_status(self, plugin_name: str) -> str:
        """
        Map the plugin's PluginState to a Backstage lifecycle string.

        Returns one of: "production", "experimental", "deprecated", "unknown".
        Feeds operational dashboards; ``spec.lifecycle`` itself derives from
        the manifest ``readiness`` (maturity), not from this runtime value.
        """
        state = self._lifecycle.get_state(plugin_name)
        if state is None:
            return "unknown"
        return _STATE_TO_LIFECYCLE.get(state, "unknown")

    def build_api_definition(self, plugin: Plugin) -> str | None:
        """Inline OpenAPI definition scoped to this plugin, or None.

        The filter uses each mounted router's full prefix
        (``get_router_prefix() + router.prefix``) so a plugin whose base
        prefix is broad (e.g. auth's ``/api``) still yields a definition
        scoped to its own routes.  Returns None (→ ``$text`` fallback) when
        no openapi supplier is wired, no usable prefix exists, or slicing
        fails for any reason — the export must never break on a bad spec.
        """
        if self._openapi_supplier is None:
            return None
        try:
            document = self._openapi_supplier()
            prefixes, exact_paths = collect_route_selectors(plugin)
            return build_plugin_api_definition(
                document,
                prefixes=prefixes,
                title=_slugify_title(plugin.metadata.name),
                description=plugin.metadata.description,
                exact_paths=exact_paths,
            )
        except Exception as exc:
            logger.warning(
                "BackstageProvider: inline API definition failed for '%s': %s",
                plugin.metadata.name,
                exc,
            )
            return None

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
        """
        lifecycle_manager.register_hook(
            plugin.metadata.name, "on_after_init", self._warm_cache_hook()
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
        """
        hook = self._warm_cache_hook()
        for plugin in registry.get_all():
            lifecycle_manager.register_hook(plugin.metadata.name, "on_after_init", hook)
            logger.debug(
                "BackstageProvider: registered on_after_init hook for '%s'",
                plugin.metadata.name,
            )

    # ── Private helpers ────────────────────────────────────────────────────────

    def _warm_cache_hook(
        self,
    ) -> Callable[[Plugin | None], Coroutine[Any, Any, None]]:
        """Build the async on_after_init hook that pre-warms the pattern cache."""

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

        return _warm_cache


# ── Module-level helpers (no self state needed) ───────────────────────────────


def _slugify_title(name: str) -> str:
    """Convert a plugin slug (kebab/snake) to a human-readable title."""
    return name.replace("-", " ").replace("_", " ").title()


# Re-exported for callers that previously imported the table from this module.
_PATTERN_MAP = PATTERN_MAP
