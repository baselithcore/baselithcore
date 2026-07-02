"""
Backstage Software Catalog integration for BaselithCore.

Transforms the PluginRegistry into Backstage Entity Provider payloads,
enabling automatic synchronisation of plugin metadata with the Backstage
Software Catalog.

Architecture
------------
- Separation of Concerns: lives in core/plugins/exporters/, never imported
  by agent execution paths.
- Async Everything: pattern detection runs in an executor thread (no blocking
  I/O on the event loop).  Lifecycle-hook attachment fires during LOADING →
  ACTIVE transition.
- Strong Contracts: satisfies the BackstageExporter Protocol defined in
  core/plugins/protocols.py without inheriting from it (structural typing).

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

logger = get_logger(__name__)


# ── Pattern registry ──────────────────────────────────────────────────────────
# Maps core module import paths → Backstage label keys.
# Add new patterns here without touching any detection logic.
_PATTERN_MAP: dict[str, str] = {
    "core.reasoning": "baselith.ai/pattern-reasoning",
    "core.reflection": "baselith.ai/pattern-reflection",
    "core.planning": "baselith.ai/pattern-planning",
    "core.guardrails": "baselith.ai/pattern-guardrails",
    "core.swarm": "baselith.ai/pattern-swarm",
    "core.a2a": "baselith.ai/pattern-a2a",
    "core.human": "baselith.ai/pattern-human-in-the-loop",
    "core.mcp": "baselith.ai/pattern-mcp",
    "core.world_model": "baselith.ai/pattern-world-model",
    "core.exploration": "baselith.ai/pattern-exploration",
    "core.adversarial": "baselith.ai/pattern-adversarial",
    "core.personas": "baselith.ai/pattern-personas",
    "core.meta": "baselith.ai/pattern-meta-agent",
    "core.learning": "baselith.ai/pattern-learning",
    "core.finetuning": "baselith.ai/pattern-finetuning",
    "core.memory": "baselith.ai/pattern-memory-tiering",
    "core.evaluation": "baselith.ai/pattern-evaluation",
    "core.task_queue": "baselith.ai/pattern-task-queue",
    "core.goals": "baselith.ai/pattern-goals",
    "core.orchestration": "baselith.ai/pattern-orchestration",
    "core.graph": "baselith.ai/pattern-knowledge-graph",
    "core.context": "baselith.ai/pattern-multi-tenancy",
}

# Short tag aliases: manifest tag "reasoning" → pattern key
_TAG_ALIASES: dict[str, str] = {
    module.split(".")[-1].replace("_", "-"): label
    for module, label in _PATTERN_MAP.items()
}

# resource name → pattern label (for required_resources / optional_resources)
_RESOURCE_TO_PATTERN: dict[str, str] = {
    "llm": "baselith.ai/pattern-reasoning",
    "evaluation": "baselith.ai/pattern-evaluation",
    "vectorstore": "baselith.ai/pattern-memory-tiering",
}

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
        # plugin_name → detected pattern label list (pre-warmed by lifecycle hooks)
        self._pattern_cache: dict[str, list[str]] = {}

    # ── Public API (satisfies BackstageExporter protocol) ─────────────────────

    async def export_entity(self, plugin: Plugin) -> dict[str, Any]:
        """Serialize a single plugin to a Backstage Component entity dict."""
        return await self.to_catalog_info(plugin)

    async def export_all(self, registry: PluginRegistry) -> list[dict[str, Any]]:
        """Serialize all registered plugins concurrently."""
        tasks = [self.export_entity(p) for p in registry.get_all()]
        return list(await asyncio.gather(*tasks))

    async def get_provider_payload(self, registry: PluginRegistry) -> dict[str, Any]:
        """
        Build the full Entity Provider mutation payload.

        Compatible with Backstage's EntityProvider.applyMutation() "full"
        mutation contract — replace the entire set of owned entities on each
        push.

        Returns
        -------
        {
            "type": "full",
            "entities": [ ... catalog-info entity dicts ... ]
        }
        """
        entities = await self.export_all(registry)
        return {"type": "full", "entities": entities}

    async def to_catalog_info(self, plugin: Plugin) -> dict[str, Any]:
        """
        Map a plugin to a Backstage Component entity dict.

        The returned dict serializes directly to a valid catalog-info.yaml
        (apiVersion: backstage.io/v1alpha1, kind: Component).

        Mapping
        -------
        PluginMetadata.name          → metadata.name
        PluginMetadata.version       → metadata.annotations[baselith.ai/version]
        PluginMetadata.description   → metadata.description
        PluginMetadata.author        → spec.owner
        PluginMetadata.tags          → metadata.tags  (+ category tag)
        PluginMetadata.category      → metadata.labels[baselith.ai/category]
        PluginMetadata.readiness     → metadata.labels[baselith.ai/readiness]
        PluginMetadata.homepage      → metadata.links + annotations
        Detected patterns            → metadata.labels  (baselith.ai/pattern-*)
        PluginState                  → spec.lifecycle
        plugin.get_routers()         → spec.providesApis  (if non-empty)
        plugin_dependencies          → spec.dependsOn
        """
        meta = plugin.metadata
        patterns = await self.detect_agentic_patterns(plugin)
        lifecycle = await self.get_health_status(meta.name)

        # ── Labels ────────────────────────────────────────────────────────────
        labels: dict[str, str] = dict.fromkeys(patterns, "true")
        labels["baselith.ai/readiness"] = meta.readiness
        labels["baselith.ai/category"] = meta.category.lower().replace(" ", "-")
        if meta.version:
            labels["app.kubernetes.io/version"] = meta.version

        # ── Annotations ───────────────────────────────────────────────────────
        annotations: dict[str, str] = {
            # TechDocs: point to the plugin's docs directory (MkDocs)
            "backstage.io/techdocs-ref": f"dir:./plugins/{meta.name}",
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
        provides_apis: list[str] = [f"{meta.name}-api"] if plugin.get_routers() else []
        depends_on: list[str] = [
            f"component:{dep}" for dep in meta.plugin_dependencies.keys()
        ]

        return {
            "apiVersion": "backstage.io/v1alpha1",
            "kind": "Component",
            "metadata": {
                "name": meta.name,
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
                "owner": meta.author or "baselith-core-team",
                "system": "baselith-core",
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
        return [label for alias, label in _TAG_ALIASES.items() if alias in tag_set]

    def _detect_from_resources(self, meta: PluginMetadata) -> list[str]:
        """Return pattern labels implied by required/optional resources."""
        resources = set(meta.required_resources + meta.optional_resources)
        return [
            label
            for resource, label in _RESOURCE_TO_PATTERN.items()
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

        return await asyncio.to_thread(_scan_source_files, plugin_dir)


# ── Module-level helpers (no self state needed) ───────────────────────────────


def _scan_source_files(plugin_dir: Path) -> list[str]:
    """
    Synchronous source scan; intended to run inside run_in_executor.

    Reads every .py file in the plugin directory (non-recursive — avoids
    traversing test directories and vendored code) and checks for
    ``from core.X …`` or ``import core.X`` import statements.
    """
    found: list[str] = []
    try:
        py_files = list(plugin_dir.glob("*.py"))
    except OSError:
        return found

    for py_file in py_files:
        try:
            source = py_file.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        for module_path, label in _PATTERN_MAP.items():
            if label not in found and (
                f"from {module_path}" in source or f"import {module_path}" in source
            ):
                found.append(label)

    return found


def _slugify_title(name: str) -> str:
    """Convert a plugin slug (kebab/snake) to a human-readable title."""
    return name.replace("-", " ").replace("_", " ").title()
