"""Per-plugin OpenAPI definition builder for the Backstage exporter.

Backstage ``API`` entities carry their contract in ``spec.definition``.
Pointing every plugin at the framework's full ``/openapi.json`` has two
problems: the catalog processor fetches it unauthenticated (the framework
may guard it), and every plugin API renders the *whole* application spec.

:func:`build_plugin_api_definition` slices the in-process OpenAPI document
down to the paths mounted under one plugin's router prefix, prunes
``components`` to the schemas transitively referenced by those paths, and
returns the result serialized as JSON — ready to embed inline in the API
entity (self-contained payload, no fetch at processing time).
"""

from __future__ import annotations

import json
import re
from typing import Any

__all__ = [
    "build_plugin_api_definition",
    "collect_refs",
    "collect_route_selectors",
    "normalize_route_path",
]


def collect_route_selectors(plugin: Any) -> tuple[list[str], set[str]]:
    """Derive the (prefixes, exact route paths) selecting a plugin's routes.

    Prefixes are ``get_router_prefix() + router.prefix`` per router; exact
    paths are the real mounted route paths (``base + route.path``) — precise
    even when the router prefix is broad (e.g. a combined router mounted at
    ``/api``).
    """
    base = str(plugin.get_router_prefix() or "").rstrip("/")
    prefixes: list[str] = []
    exact_paths: set[str] = set()
    for router in plugin.get_routers():
        router_prefix = str(getattr(router, "prefix", "") or "")
        full = f"{base}{router_prefix}".rstrip("/")
        if full and full not in prefixes:
            prefixes.append(full)
        routes = getattr(router, "routes", None) or []
        if isinstance(routes, (list, tuple)):
            for route in routes:
                path = getattr(route, "path", None)
                if isinstance(path, str) and path:
                    exact_paths.add(f"{base}{path}")
    if not prefixes and base:
        prefixes = [base]
    return prefixes, exact_paths


_PATH_CONVERTER = re.compile(r"{(\w+):[^}]+}")


def normalize_route_path(path: str) -> str:
    """Strip Starlette path-converter suffixes (``{p:path}`` → ``{p}``).

    Route objects carry the converter form; the OpenAPI document uses the
    bare parameter name — normalising lets the two be compared directly.
    """
    return _PATH_CONVERTER.sub(r"{\1}", path)


#: Top-level keys copied verbatim from the source document when present.
_PASSTHROUGH_KEYS = ("openapi", "servers", "security", "tags")


def collect_refs(node: Any, acc: set[str]) -> None:
    """Recursively collect ``$ref`` values (``#/components/...``) into *acc*."""
    if isinstance(node, dict):
        ref = node.get("$ref")
        if isinstance(ref, str) and ref.startswith("#/components/"):
            acc.add(ref)
        for value in node.values():
            collect_refs(value, acc)
    elif isinstance(node, list):
        for item in node:
            collect_refs(item, acc)


def _prune_components(
    components: dict[str, Any], filtered_paths: dict[str, Any]
) -> dict[str, Any]:
    """Return only the component subtrees transitively referenced by *filtered_paths*."""
    needed: set[str] = set()
    collect_refs(filtered_paths, needed)

    # Refs inside components may reference further components — iterate to a
    # fixed point so nested schemas survive the pruning.
    resolved: set[str] = set()
    while needed - resolved:
        for ref in sorted(needed - resolved):
            resolved.add(ref)
            parts = ref.split("/")  # ["#", "components", <section>, <name>]
            if len(parts) < 4:
                continue
            section, name = parts[2], parts[3]
            target = components.get(section, {}).get(name)
            if target is not None:
                collect_refs(target, needed)

    pruned: dict[str, dict[str, Any]] = {}
    for ref in resolved:
        parts = ref.split("/")
        if len(parts) < 4:
            continue
        section, name = parts[2], parts[3]
        value = components.get(section, {}).get(name)
        if value is not None:
            pruned.setdefault(section, {})[name] = value
    return pruned


def build_plugin_api_definition(
    openapi: dict[str, Any],
    *,
    prefixes: list[str],
    title: str,
    description: str = "",
    exact_paths: set[str] | None = None,
) -> str | None:
    """Slice *openapi* down to this plugin's paths and serialize to JSON.

    A path is kept when it appears in *exact_paths* (the plugin's real mounted
    route paths — precise even for plugins whose router prefix is broad, e.g.
    a combined router mounted at ``/api``) or sits under one of *prefixes*.
    Empty and root prefixes are ignored: they would swallow the entire
    application spec into a single plugin's API entity.  Returns ``None``
    when nothing matches — the caller should then fall back to the ``$text``
    full-spec reference.
    """
    usable = [
        p.rstrip("/") for p in prefixes if p and p.rstrip("/") not in ("", "/api")
    ]
    exact = {normalize_route_path(p) for p in (exact_paths or set())}
    if not usable and not exact:
        return None

    all_paths = openapi.get("paths") or {}
    filtered = {
        path: item
        for path, item in all_paths.items()
        if path in exact or any(path == p or path.startswith(f"{p}/") for p in usable)
    }
    if not filtered:
        return None

    document: dict[str, Any] = {
        "openapi": str(openapi.get("openapi", "3.1.0")),
        "info": {
            "title": f"{title} API",
            "description": description or f"Routes mounted under {', '.join(usable)}.",
            "version": str((openapi.get("info") or {}).get("version", "0.0.0")),
        },
        "paths": filtered,
    }
    for key in _PASSTHROUGH_KEYS:
        if key != "openapi" and key in openapi:
            document[key] = openapi[key]

    components = openapi.get("components") or {}
    if components:
        pruned = _prune_components(components, filtered)
        # Security schemes are referenced by name (not $ref) from operation
        # ``security`` blocks — keep them wholesale so the contract stays valid.
        if "securitySchemes" in components:
            pruned.setdefault("securitySchemes", components["securitySchemes"])
        if pruned:
            document["components"] = pruned

    return json.dumps(document, sort_keys=True)
