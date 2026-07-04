"""Complete Backstage entity-graph assembly.

Builds the full Entity Provider payload — Domain + System + owner Groups +
shared Resources + plugin Components + plugin APIs — from a
:class:`~core.plugins.exporters.backstage_provider.BackstageProvider` and the
live :class:`~core.plugins.registry.PluginRegistry`.

Backstage rejects (or renders as broken relations) references to entities
that are never ingested; this module guarantees every reference emitted by a
Component (``spec.system``, ``spec.owner``, ``spec.providesApis``,
``spec.dependsOn``) resolves to an entity in the same payload.
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Any

from .entity_model import (
    build_api_entity,
    build_domain_entity,
    build_group_entity,
    build_resource_entity,
    build_system_entity,
    group_slug,
    owner_ref,
    resource_name,
    sanitize_entity_name,
)

if TYPE_CHECKING:
    from core.plugins.registry import PluginRegistry

    from .backstage_provider import BackstageProvider

__all__ = ["export_entity_graph"]


async def export_entity_graph(
    provider: BackstageProvider, registry: PluginRegistry
) -> list[dict[str, Any]]:
    """Serialize the complete entity graph for the Entity Provider payload."""
    plugins = registry.get_all()
    components = list(
        await asyncio.gather(*(provider.export_entity(p) for p in plugins))
    )

    entities: list[dict[str, Any]] = [
        build_domain_entity(entities_url=provider.entities_url),
        build_system_entity(
            entities_url=provider.entities_url,
            base_url=provider.base_url,
            docs_base_url=provider.docs_base_url,
        ),
    ]

    # Groups — one per unique owner reference (platform team always included:
    # it owns the Domain, System, and Resource entities).
    owner_slugs = {group_slug(owner_ref(None))}
    owner_slugs.update(group_slug(str(c["spec"]["owner"])) for c in components)
    entities.extend(
        build_group_entity(slug=slug, entities_url=provider.entities_url)
        for slug in sorted(owner_slugs)
    )

    # Resources — one per unique declared required resource.
    seen_resources: set[str] = set()
    for plugin in plugins:
        for res in plugin.metadata.required_resources:
            name = resource_name(res)
            if name not in seen_resources:
                seen_resources.add(name)
                entities.append(
                    build_resource_entity(
                        resource_id=res, entities_url=provider.entities_url
                    )
                )

    entities.extend(components)

    # APIs — one per plugin that exposes routers, with an inline plugin-scoped
    # OpenAPI definition when the provider has an openapi supplier.
    components_by_name = {c["metadata"]["name"]: c for c in components}
    for plugin in plugins:
        component = components_by_name.get(sanitize_entity_name(plugin.metadata.name))
        if component is None or not component["spec"].get("providesApis"):
            continue
        meta = component["metadata"]
        entities.append(
            build_api_entity(
                plugin_name=meta["name"],
                title=str(meta.get("title", meta["name"])),
                description=str(meta.get("description", "")),
                owner=str(component["spec"]["owner"]),
                lifecycle=str(component["spec"]["lifecycle"]),
                entities_url=provider.entities_url,
                base_url=provider.base_url,
                tags=list(meta.get("tags", [])),
                definition=provider.build_api_definition(plugin),
            )
        )
    return entities
