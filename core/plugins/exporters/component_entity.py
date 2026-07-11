"""Component-entity assembly for the Backstage exporter.

Body of ``BackstageProvider.to_catalog_info`` — the PluginMetadata →
Backstage Component mapping (labels, annotations, tags, links, spec).
Extracted from ``backstage_provider.py`` (module size cap); behavior is
unchanged and the mapping table lives in the provider method's docstring.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from .entity_model import (
    BACKSTAGE_API_VERSION,
    DEFAULT_NAMESPACE,
    SYSTEM_NAME,
    api_name,
    build_component_links,
    component_ref,
    file_location_annotations,
    infra_resources,
    location_annotations,
    owner_ref,
    readiness_to_lifecycle,
    resource_name,
    resource_ref,
    sanitize_entity_name,
    sanitize_label_value,
)
from .introspection import plugin_has_techdocs

if TYPE_CHECKING:
    from core.plugins.interface import Plugin

    from .backstage_provider import BackstageProvider


def slugify_title(name: str) -> str:
    """Convert a plugin slug (kebab/snake) to a human-readable title."""
    return name.replace("-", " ").replace("_", " ").title()


async def build_component_entity(
    provider: BackstageProvider, plugin: Plugin
) -> dict[str, Any]:
    """Map *plugin* to a Backstage Component entity dict."""
    meta = plugin.metadata
    patterns = await provider.detect_agentic_patterns(plugin)
    entity_name = sanitize_entity_name(meta.name)
    state = provider._lifecycle.get_state(meta.name)
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
    # Manifest names are attacker-influenced input: every path/URL segment
    # interpolation goes through the sanitised form so a hostile name can
    # never traverse (`../`) on the portal backend. Only the verbatim
    # `plugin-id` value keeps the raw registry identity.
    safe_name = entity_name
    annotations: dict[str, str] = {
        # Required for Entity Provider ingestion: where this entity is
        # managed from (the live export endpoint of this instance).
        **location_annotations(provider._entities_url),
        # Where the plugin's source lives inside the repository.
        "backstage.io/source-location": (
            f"{provider._catalog_source_location}plugins/{safe_name}/"
        ),
        # The registry identity, verbatim (may differ from the sanitised
        # metadata.name) — lets integrations map back to the plugin.
        "baselith.ai/plugin-id": meta.name,
        # Health bridge: live status from BaselithCore's health endpoint
        "baselith.ai/health-url": f"{provider._base_url}/health",
        # Plugin admin API: current state, config, metrics
        "baselith.ai/plugin-api-url": (f"{provider._base_url}/api/plugins/{safe_name}"),
        # Manifest source: direct link to the manifest.yaml in the repo
        "baselith.ai/manifest-url": (
            f"{provider._catalog_source_location}plugins/{safe_name}/manifest.yaml"
        ),
    }
    # TechDocs: only advertise docs that can actually build — a ref
    # pointing at a directory without mkdocs.yml renders a broken Docs tab.
    if plugin_has_techdocs(plugin):
        if provider._catalog_local_root:
            # Local self-hosted portal: read docs from the filesystem the
            # backend shares with the repo. `dir:.` resolves relative to the
            # entity's (file:) location — its dir is where mkdocs.yml lives —
            # so no git host, token or push is required.
            annotations.update(
                file_location_annotations(provider._catalog_local_root, meta.name)
            )
            annotations["backstage.io/techdocs-ref"] = "dir:."
        else:
            annotations["backstage.io/techdocs-ref"] = (
                f"{provider._catalog_source_location}plugins/{safe_name}"
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
        plugin_link_template=provider._plugin_link_template,
        docs_base_url=provider._docs_base_url,
    )

    # ── Spec ──────────────────────────────────────────────────────────────
    provides_apis: list[str] = [api_name(meta.name)] if plugin.get_routers() else []
    depends_on: list[str] = [
        component_ref(dep) for dep in meta.plugin_dependencies.keys()
    ]
    # Both required and optional infra resources become dependency edges so
    # the "Depends on resources" card is populated; dependency pins and env
    # flags are filtered out (they are not real Resource entities).
    for res in infra_resources(meta.required_resources, meta.optional_resources):
        ref = resource_ref(res)
        if ref not in depends_on:
            depends_on.append(ref)

    spec: dict[str, Any] = {
        "type": "baselith-plugin",
        "lifecycle": readiness_to_lifecycle(meta.readiness),
        "owner": owner_ref(meta.author),
        "system": SYSTEM_NAME,
        "providesApis": provides_apis,
        "dependsOn": depends_on,
    }
    # Optional composition: render as a subcomponent of a parent plugin.
    parent = getattr(meta, "subcomponent_of", "")
    if isinstance(parent, str) and parent.strip():
        spec["subcomponentOf"] = component_ref(parent.strip())

    return {
        "apiVersion": BACKSTAGE_API_VERSION,
        "kind": "Component",
        "metadata": {
            "name": entity_name,
            "namespace": DEFAULT_NAMESPACE,
            "title": slugify_title(meta.name),
            "description": meta.description,
            "labels": labels,
            "annotations": annotations,
            "tags": tags,
            "links": links,
        },
        "spec": spec,
    }


__all__ = ["build_component_entity", "slugify_title"]
