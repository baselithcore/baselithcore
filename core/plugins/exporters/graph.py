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
from collections.abc import Iterable
from typing import TYPE_CHECKING, Any

from .entity_model import (
    api_name,
    build_api_entity,
    build_domain_entity,
    build_group_entity,
    build_resource_entity,
    build_system_entity,
    group_slug,
    infra_resources,
    owner_display,
    owner_ref,
    resource_name,
    sanitize_entity_name,
)
from .mounts import SubAppApi, build_subapp_api_definition, discover_subapp_apis

if TYPE_CHECKING:
    from core.plugins.interface import Plugin
    from core.plugins.registry import PluginRegistry

    from .backstage_provider import BackstageProvider

__all__ = ["export_entity_graph"]


def _subapp_for(plugin: Plugin, subapps: dict[str, SubAppApi]) -> SubAppApi | None:
    """Find the mounted sub-app owned by *plugin* (by registry name)."""
    name = plugin.metadata.name
    return subapps.get(name) or subapps.get(sanitize_entity_name(name))


async def export_entity_graph(
    provider: BackstageProvider,
    registry: PluginRegistry,
    routes: Iterable[Any] | None = None,
) -> list[dict[str, Any]]:
    """Serialize the complete entity graph for the Entity Provider payload.

    When *routes* (the host app's routes) are supplied, plugins that expose
    their HTTP API via a mounted FastAPI sub-app — invisible to
    ``get_routers()`` / the host OpenAPI — also get an ``API`` entity, with the
    sub-app's own OpenAPI inlined and mount-path-prefixed (see :mod:`.mounts`).
    """
    plugins = registry.get_all()
    components = list(
        await asyncio.gather(*(provider.export_entity(p) for p in plugins))
    )
    subapps = discover_subapp_apis(routes)

    entities: list[dict[str, Any]] = [
        build_domain_entity(entities_url=provider.entities_url),
        build_system_entity(
            entities_url=provider.entities_url,
            base_url=provider.base_url,
            docs_base_url=provider.docs_base_url,
        ),
    ]

    # Groups — one per unique owner reference (platform team always included:
    # it owns the Domain, System, and Resource entities). Titles use the
    # original manifest author so brand casing survives the slug round-trip.
    title_by_slug: dict[str, str] = {}
    for plugin in plugins:
        author = plugin.metadata.author
        title_by_slug.setdefault(group_slug(owner_ref(author)), owner_display(author))
    owner_slugs = {group_slug(owner_ref(None))}
    owner_slugs.update(group_slug(str(c["spec"]["owner"])) for c in components)
    entities.extend(
        build_group_entity(
            slug=slug,
            entities_url=provider.entities_url,
            title=title_by_slug.get(slug),
        )
        for slug in sorted(owner_slugs)
    )

    # Resources — one per unique declared infra resource (required ∪ optional;
    # dependency pins and env flags are filtered out by infra_resources()).
    seen_resources: set[str] = set()
    for plugin in plugins:
        meta = plugin.metadata
        for res in infra_resources(meta.required_resources, meta.optional_resources):
            name = resource_name(res)
            if name not in seen_resources:
                seen_resources.add(name)
                entities.append(
                    build_resource_entity(
                        resource_id=res, entities_url=provider.entities_url
                    )
                )

    entities.extend(components)

    # Sub-app-mount plugins serve their API from a mounted FastAPI (not via
    # get_routers()), so their Component has no providesApis yet — inject it so
    # the API entity below is emitted and the relation resolves.
    components_by_name = {c["metadata"]["name"]: c for c in components}
    for plugin in plugins:
        if _subapp_for(plugin, subapps) is None:
            continue
        component = components_by_name.get(sanitize_entity_name(plugin.metadata.name))
        if component is None:
            continue
        provides = component["spec"].setdefault("providesApis", [])
        ref = api_name(plugin.metadata.name)
        if ref not in provides:
            provides.append(ref)

    # APIs — one per plugin that exposes routers (inline plugin-scoped OpenAPI
    # sliced from the host spec) or a mounted FastAPI sub-app (its own OpenAPI,
    # mount-path-prefixed).
    for plugin in plugins:
        component = components_by_name.get(sanitize_entity_name(plugin.metadata.name))
        if component is None or not component["spec"].get("providesApis"):
            continue
        comp_meta = component["metadata"]
        title = str(comp_meta.get("title", comp_meta["name"]))
        description = str(comp_meta.get("description", ""))
        subapp = _subapp_for(plugin, subapps)
        if subapp is not None:
            definition: str | None = build_subapp_api_definition(
                subapp.openapi,
                mount_path=subapp.mount_path,
                title=title,
                description=description,
            )
        else:
            definition = provider.build_api_definition(plugin)
        entities.append(
            build_api_entity(
                plugin_name=comp_meta["name"],
                title=title,
                description=description,
                owner=str(component["spec"]["owner"]),
                lifecycle=str(component["spec"]["lifecycle"]),
                entities_url=provider.entities_url,
                base_url=provider.base_url,
                tags=list(comp_meta.get("tags", [])),
                definition=definition,
            )
        )
    return entities
