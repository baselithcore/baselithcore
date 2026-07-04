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
DEFAULT_OWNER_NAME = "baselith-core-team"

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
        "spec": {"owner": f"group:{DEFAULT_NAMESPACE}/{DEFAULT_OWNER_NAME}"},
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
) -> dict[str, Any]:
    """Build the ``API`` entity backing a Component's ``providesApis`` ref.

    ``spec.definition`` uses the ``$text`` placeholder so Backstage fetches the
    live OpenAPI document from the running framework at processing time (the
    FastAPI app serves a single spec covering every mounted plugin router).
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
            "definition": {"$text": f"{base_url}/openapi.json"},
        },
    }


__all__ = [
    "BACKSTAGE_API_VERSION",
    "DEFAULT_NAMESPACE",
    "DEFAULT_OWNER_NAME",
    "ENTITIES_PATH",
    "SYSTEM_NAME",
    "api_name",
    "api_ref",
    "build_api_entity",
    "build_system_entity",
    "component_ref",
    "location_annotations",
    "owner_ref",
    "sanitize_entity_name",
    "sanitize_label_value",
]
