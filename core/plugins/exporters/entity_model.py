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
DEFAULT_OWNER_NAME = "baselith-core-team"

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


def build_system_entity(
    *,
    entities_url: str,
    base_url: str,
    docs_base_url: str,
) -> dict[str, Any]:
    """Build the ``System`` root entity every plugin Component belongs to.

    Components declare ``spec.system: baselith-core``; without this entity the
    reference dangles and the Backstage system view renders a broken node.
    """
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
            "links": [
                {"url": base_url, "title": "Framework API", "icon": "dashboard"},
                {"url": docs_base_url, "title": "Documentation", "icon": "docs"},
            ],
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


def build_group_entity(*, slug: str, entities_url: str) -> dict[str, Any]:
    """Build a ``Group`` entity backing a Component's ``spec.owner`` reference.

    Owner refs (``group:default/<slug>``) are derived from manifest authors;
    emitting the Group keeps the reference from dangling in the catalog.
    """
    return {
        "apiVersion": BACKSTAGE_API_VERSION,
        "kind": "Group",
        "metadata": {
            "name": slug,
            "namespace": DEFAULT_NAMESPACE,
            "title": slug.replace("-", " ").replace("_", " ").title(),
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
    "build_domain_entity",
    "build_group_entity",
    "build_resource_entity",
    "build_system_entity",
    "component_ref",
    "group_slug",
    "location_annotations",
    "owner_ref",
    "readiness_to_lifecycle",
    "resource_name",
    "resource_ref",
    "sanitize_entity_name",
    "sanitize_label_value",
]
