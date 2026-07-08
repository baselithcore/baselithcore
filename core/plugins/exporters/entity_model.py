"""
Backstage entity-model helpers: format hygiene and auxiliary entity builders.

The Backstage catalog enforces a strict entity format (the "Entity envelope"):

- ``metadata.name`` must match ``[a-zA-Z0-9\\-_.]`` and be at most 63 chars.
- Label **values** follow the same charset/length rules and must start and end
  with an alphanumeric character (Kubernetes label-value semantics).
- ``spec.owner`` / ``spec.dependsOn`` / ``spec.providesApis`` are **entity
  references** (``kind:namespace/name``); a free-form author string such as
  ``"Jane Doe <jane@corp>"`` is rejected or, worse, silently creates a dangling
  reference the Backstage UI renders as a broken relation.
- Entities ingested through an **Entity Provider** must carry the
  ``backstage.io/managed-by-location`` and
  ``backstage.io/managed-by-origin-location`` annotations, otherwise catalog
  processing fails.

This module centralises those rules so :class:`BackstageProvider` emits a
complete, valid entity graph — a ``System`` root, one ``Component`` per plugin,
and one ``API`` entity per plugin that exposes routers — with no dangling refs.
"""

from __future__ import annotations

import re
from typing import Any

BACKSTAGE_API_VERSION = "backstage.io/v1alpha1"
DEFAULT_NAMESPACE = "default"
SYSTEM_NAME = "baselith-core"
DOMAIN_NAME = "baselith"
# Single platform-team owner group. Matches the slug that manifest author
# "BaselithCore Team" resolves to, so the Domain/System/Resources and every
# plugin Component share one owner group instead of two near-duplicates.
DEFAULT_OWNER_NAME = "baselithcore-team"

#: Manifest ``readiness`` → Backstage ``spec.lifecycle``.  Backstage treats
#: lifecycle as *maturity* (experimental / production / deprecated), not as
#: runtime health — the live PluginState is exported separately as the
#: ``baselith.ai/runtime-state`` label.
_READINESS_TO_LIFECYCLE = {
    "stable": "production",
    "ga": "production",
    "production": "production",
    "deprecated": "deprecated",
}

#: Known infrastructure resource ids (manifest ``required_resources``) →
#: Backstage Resource ``spec.type``.  Unknown ids fall back to
#: ``"infrastructure"`` so the graph never drops a declared dependency.
RESOURCE_TYPE_MAP = {
    "postgres": "database",
    "postgresql": "database",
    "database": "database",
    "redis": "cache",
    "cache": "cache",
    "qdrant": "vector-database",
    "vectorstore": "vector-database",
    "vector-db": "vector-database",
    "llm": "llm-provider",
    "embedding": "llm-provider",
    "queue": "message-queue",
    "rabbitmq": "message-queue",
    "docker": "container-runtime",
    "browser": "browser-runtime",
    "playwright": "browser-runtime",
}

#: Path (relative to the framework base URL) where the Entity Provider payload
#: is served; used to build the mandatory managed-by-location annotations.
ENTITIES_PATH = "/api/backstage/entities"

_INVALID_NAME_CHARS = re.compile(r"[^a-zA-Z0-9\-_.]+")
_SEPARATOR_EDGES = re.compile(r"^[\-_.]+|[\-_.]+$")
_MAX_NAME_LEN = 63
_VERSION_OP = re.compile(r"[<>=~!]")


def is_infra_resource(resource_id: str) -> bool:
    """Whether a manifest resource id denotes real shared infrastructure.

    Manifest ``required_resources``/``optional_resources`` lists mix genuine
    infra (``postgres``, ``redis``, ``llm``, ``qdrant``, ``graph``) with
    dependency pins (``fastapi>=0.110.0``) and config toggles/env flags
    (``AURA_PERSISTENCE``, ``BASELITH_PITWALL_ENABLED``). Only the former should
    become ``Resource`` entities / ``dependsOn`` edges — the rest would pollute
    the catalog graph with meaningless nodes. This keeps a lowercase, versionless,
    non-env-flag token.
    """
    raw = (resource_id or "").strip()
    if not raw or _VERSION_OP.search(raw) or "." in raw:
        return False
    # ENV-style flag/toggle (all-caps and/or SNAKE_CASE): AURA_PERSISTENCE.
    if raw.upper() == raw and ("_" in raw or len(raw) > 4):
        return False
    return True


def infra_resources(required: list[str], optional: list[str]) -> list[str]:
    """De-duplicated, order-preserving infra resource ids from both lists."""
    out: list[str] = []
    seen: set[str] = set()
    for res in [*required, *optional]:
        if not is_infra_resource(res):
            continue
        norm = res.strip().lower()
        key = resource_name(norm)
        if key not in seen:
            seen.add(key)
            out.append(norm)
    return out


def owner_display(author: str | None) -> str:
    """Human-facing owner name from a manifest ``author`` (email suffix dropped)."""
    display = (author or "").split("<", 1)[0].strip()
    return display or DEFAULT_OWNER_NAME.replace("-", " ").title()


def file_location_annotations(local_root: str, plugin_name: str) -> dict[str, str]:
    """Local-filesystem ``file:`` location so TechDocs ``dir:`` refs resolve.

    Points at the plugin's ``manifest.yaml`` on the portal backend's own disk;
    its directory is where ``mkdocs.yml`` lives, so a ``dir:.`` techdocs-ref
    reads the docs locally — no git host / token / push required.

    ``plugin_name`` is sanitised before interpolation: a manifest name is
    attacker-influenced input and must never traverse outside ``plugins/``.
    """
    safe_name = sanitize_entity_name(plugin_name)
    location = f"file:{local_root.rstrip('/')}/plugins/{safe_name}/manifest.yaml"
    return {
        "backstage.io/managed-by-location": location,
        "backstage.io/managed-by-origin-location": location,
    }


def sanitize_entity_name(raw: str | None, fallback: str = "unknown") -> str:
    """Coerce ``raw`` into a valid Backstage ``metadata.name``.

    Invalid character runs collapse to a single ``-``; leading/trailing
    separators are stripped; the result is truncated to 63 chars. Returns
    ``fallback`` when nothing valid survives.
    """
    if not raw:
        return fallback
    name = _INVALID_NAME_CHARS.sub("-", raw.strip())
    name = _SEPARATOR_EDGES.sub("", name)[:_MAX_NAME_LEN]
    name = _SEPARATOR_EDGES.sub("", name)
    return name or fallback


def sanitize_label_value(raw: str | None) -> str:
    """Coerce ``raw`` into a valid Kubernetes-style label value.

    Same charset as entity names, but the value must also *start and end*
    with an alphanumeric character. Empty input maps to ``"unknown"`` so the
    label never serialises as an invalid empty string.
    """
    if not raw:
        return "unknown"
    value = _INVALID_NAME_CHARS.sub("-", raw.strip())[:_MAX_NAME_LEN]
    value = re.sub(r"^[^a-zA-Z0-9]+|[^a-zA-Z0-9]+$", "", value)
    return value or "unknown"


def owner_ref(author: str | None) -> str:
    """Map a manifest ``author`` string to a valid Backstage owner reference.

    ``"Jane Doe <jane@corp.io>"`` → ``"group:default/jane-doe"``. Email/URL
    suffixes in angle brackets are dropped before slugification; a missing or
    unsanitisable author falls back to the platform team group.
    """
    display = (author or "").split("<", 1)[0].strip()
    slug = sanitize_entity_name(display.lower().replace(" ", "-"), fallback="")
    return f"group:{DEFAULT_NAMESPACE}/{slug or DEFAULT_OWNER_NAME}"


def component_ref(name: str) -> str:
    """Fully-qualified ``component:`` entity reference for a plugin name."""
    return f"component:{DEFAULT_NAMESPACE}/{sanitize_entity_name(name)}"


def readiness_to_lifecycle(readiness: str | None) -> str:
    """Map a manifest ``readiness`` stage to a Backstage lifecycle string.

    ``stable``/``ga``/``production`` → ``production``; ``deprecated`` →
    ``deprecated``; everything else (``alpha``, ``beta``, ``experimental``,
    missing) → ``experimental``.
    """
    return _READINESS_TO_LIFECYCLE.get(
        (readiness or "").strip().lower(), "experimental"
    )


def resource_name(resource_id: str) -> str:
    """Backstage entity name for a manifest resource id."""
    return sanitize_entity_name(resource_id.strip().lower(), fallback="unknown")


def resource_ref(resource_id: str) -> str:
    """Fully-qualified ``resource:`` entity reference for a resource id."""
    return f"resource:{DEFAULT_NAMESPACE}/{resource_name(resource_id)}"


def group_slug(owner_reference: str) -> str:
    """Extract the bare group name from a ``group:namespace/name`` reference."""
    return owner_reference.rsplit("/", 1)[-1]


def api_name(plugin_name: str) -> str:
    """Entity name of the API exposed by a plugin (``<plugin>-api``)."""
    base = sanitize_entity_name(plugin_name)[: _MAX_NAME_LEN - 4]
    return f"{_SEPARATOR_EDGES.sub('', base)}-api"


def api_ref(plugin_name: str) -> str:
    """Fully-qualified ``api:`` entity reference for a plugin's API."""
    return f"api:{DEFAULT_NAMESPACE}/{api_name(plugin_name)}"


def location_annotations(entities_url: str) -> dict[str, str]:
    """The provider-location annotations required on provider-fed entities."""
    location = f"url:{entities_url}"
    return {
        "backstage.io/managed-by-location": location,
        "backstage.io/managed-by-origin-location": location,
    }


def build_component_links(
    *,
    plugin_name: str,
    homepage: str = "",
    plugin_link_template: str | None = None,
    docs_base_url: str | None = None,
) -> list[dict[str, str]]:
    """Browser-renderable links for a plugin Component.

    Machine endpoints (plugin admin API, health probe) belong in annotations,
    never here — a link answering 401 JSON is a broken link in the catalog UI.
    """
    links: list[dict[str, str]] = []
    if homepage:
        links.append({"url": homepage, "title": "Homepage", "icon": "web"})
    if plugin_link_template:
        links.append(
            {
                "url": plugin_link_template.format(plugin=plugin_name),
                "title": "Manage Plugin",
                "icon": "dashboard",
            }
        )
    if docs_base_url:
        links.append(
            {
                "url": f"{docs_base_url}/plugins/{plugin_name}",
                "title": "Documentation",
                "icon": "docs",
            }
        )
    return links


def build_system_entity(
    *,
    entities_url: str,
    base_url: str,
    docs_base_url: str | None = None,
) -> dict[str, Any]:
    """Build the ``System`` root entity every plugin Component belongs to.

    Components declare ``spec.system: baselith-core``; without this entity the
    reference dangles and the Backstage system view renders a broken node.
    The Documentation link is emitted only when a docs site is configured.
    """
    links: list[dict[str, str]] = [
        {"url": base_url, "title": "Framework API", "icon": "dashboard"},
    ]
    if docs_base_url:
        links.append({"url": docs_base_url, "title": "Documentation", "icon": "docs"})
    return {
        "apiVersion": BACKSTAGE_API_VERSION,
        "kind": "System",
        "metadata": {
            "name": SYSTEM_NAME,
            "namespace": DEFAULT_NAMESPACE,
            "title": "BaselithCore",
            "description": (
                "Modular orchestration engine for production-grade agentic AI "
                "— plugin ecosystem exported live from the running instance."
            ),
            "annotations": location_annotations(entities_url),
            "links": links,
        },
        "spec": {
            "owner": f"group:{DEFAULT_NAMESPACE}/{DEFAULT_OWNER_NAME}",
            "domain": DOMAIN_NAME,
        },
    }


def build_domain_entity(*, entities_url: str) -> dict[str, Any]:
    """Build the ``Domain`` root the ``baselith-core`` System belongs to.

    Backstage models large catalogs as Domain → System → Component; the System
    entity declares ``spec.domain: baselith`` and this entity anchors it.
    """
    return {
        "apiVersion": BACKSTAGE_API_VERSION,
        "kind": "Domain",
        "metadata": {
            "name": DOMAIN_NAME,
            "namespace": DEFAULT_NAMESPACE,
            "title": "Baselith Platform",
            "description": (
                "Agentic AI platform domain — orchestration core, plugin "
                "ecosystem, and shared infrastructure."
            ),
            "annotations": location_annotations(entities_url),
        },
        "spec": {"owner": f"group:{DEFAULT_NAMESPACE}/{DEFAULT_OWNER_NAME}"},
    }


def build_group_entity(
    *, slug: str, entities_url: str, title: str | None = None
) -> dict[str, Any]:
    """Build a ``Group`` entity backing a Component's ``spec.owner`` reference.

    Owner refs (``group:default/<slug>``) are derived from manifest authors;
    emitting the Group keeps the reference from dangling in the catalog. When a
    display ``title`` is given (the original author string) it is used verbatim
    so brand casing survives (e.g. ``BaselithCore Team`` rather than the
    slug-titlecased ``Baselithcore Team``).
    """
    return {
        "apiVersion": BACKSTAGE_API_VERSION,
        "kind": "Group",
        "metadata": {
            "name": slug,
            "namespace": DEFAULT_NAMESPACE,
            "title": title or slug.replace("-", " ").replace("_", " ").title(),
            "description": "Plugin owner group (derived from manifest metadata).",
            "annotations": location_annotations(entities_url),
        },
        "spec": {"type": "team", "children": []},
    }


def build_resource_entity(*, resource_id: str, entities_url: str) -> dict[str, Any]:
    """Build a ``Resource`` entity for a manifest ``required_resources`` id.

    Components ``dependsOn`` these resources; emitting them keeps the
    dependency graph complete (Backstage renders unresolved refs as broken
    relations).
    """
    name = resource_name(resource_id)
    return {
        "apiVersion": BACKSTAGE_API_VERSION,
        "kind": "Resource",
        "metadata": {
            "name": name,
            "namespace": DEFAULT_NAMESPACE,
            "title": name.replace("-", " ").title(),
            "description": f"Shared platform resource '{resource_id}' required by plugins.",
            "annotations": location_annotations(entities_url),
        },
        "spec": {
            "type": RESOURCE_TYPE_MAP.get(name, "infrastructure"),
            "owner": f"group:{DEFAULT_NAMESPACE}/{DEFAULT_OWNER_NAME}",
            "system": SYSTEM_NAME,
        },
    }


def build_api_entity(
    *,
    plugin_name: str,
    title: str,
    description: str,
    owner: str,
    lifecycle: str,
    entities_url: str,
    base_url: str,
    tags: list[str] | None = None,
    definition: str | None = None,
) -> dict[str, Any]:
    """Build the ``API`` entity backing a Component's ``providesApis`` ref.

    When ``definition`` is given (a serialized OpenAPI document scoped to the
    plugin's routes) it is embedded inline — self-contained, no authenticated
    fetch needed at catalog-processing time.  Otherwise ``spec.definition``
    falls back to the ``$text`` placeholder pointing at the framework's full
    OpenAPI spec.
    """
    return {
        "apiVersion": BACKSTAGE_API_VERSION,
        "kind": "API",
        "metadata": {
            "name": api_name(plugin_name),
            "namespace": DEFAULT_NAMESPACE,
            "title": f"{title} API",
            "description": description or f"HTTP API exposed by {plugin_name}.",
            "annotations": location_annotations(entities_url),
            "tags": list(tags or []),
        },
        "spec": {
            "type": "openapi",
            "lifecycle": lifecycle,
            "owner": owner,
            "system": SYSTEM_NAME,
            "definition": (
                definition
                if definition is not None
                else {"$text": f"{base_url}/openapi.json"}
            ),
        },
    }


__all__ = [
    "BACKSTAGE_API_VERSION",
    "DEFAULT_NAMESPACE",
    "DEFAULT_OWNER_NAME",
    "DOMAIN_NAME",
    "ENTITIES_PATH",
    "RESOURCE_TYPE_MAP",
    "SYSTEM_NAME",
    "api_name",
    "api_ref",
    "build_api_entity",
    "build_component_links",
    "build_domain_entity",
    "build_group_entity",
    "build_resource_entity",
    "build_system_entity",
    "component_ref",
    "file_location_annotations",
    "group_slug",
    "infra_resources",
    "is_infra_resource",
    "location_annotations",
    "owner_display",
    "owner_ref",
    "readiness_to_lifecycle",
    "resource_name",
    "resource_ref",
    "sanitize_entity_name",
    "sanitize_label_value",
]
