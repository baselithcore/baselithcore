"""Sub-app-mount API discovery for the Backstage exporter.

Some plugins do not register FastAPI routers on the host app; instead they
``app.mount(MOUNT_PATH, get_app())`` a self-contained FastAPI/Starlette
sub-application (the established sub-app-mount pattern used by e.g. wikigen,
docheck, agent_jira, baselithbrain).  Those routes live in a *separate* ASGI
app and therefore never appear in the host ``/openapi.json`` nor in
``plugin.get_routers()`` — so the router-based API-entity path in
:mod:`.api_definition` cannot see them and the plugin exports with **no**
Backstage ``API`` entity even though it serves a real HTTP API.

This module closes that gap.  :func:`discover_subapp_apis` walks the host
app's ``Mount`` routes, keeps the ones whose mounted app exposes its own
``openapi()`` document, and keys them by the mount ``name`` (which the
sub-app-mount pattern sets to the plugin's registry name).
:func:`build_subapp_api_definition` serialises a mounted sub-app's OpenAPI with
its paths re-prefixed by the mount path, so the inline definition embedded in
the API entity matches the URLs the endpoints are actually served at.
"""

from __future__ import annotations

import json
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any

__all__ = [
    "SubAppApi",
    "build_subapp_api_definition",
    "discover_subapp_apis",
]


@dataclass(frozen=True)
class SubAppApi:
    """A mounted FastAPI sub-app that exposes its own OpenAPI document.

    Attributes:
        plugin_name: The mount ``name`` — set to the owning plugin's registry
            name by the sub-app-mount pattern.
        mount_path: Host path the sub-app is mounted at (e.g. ``/wikigen``),
            trailing slash stripped.
        openapi: The sub-app's OpenAPI document (self-contained: its own
            ``paths`` and ``components``).
    """

    plugin_name: str
    mount_path: str
    openapi: dict[str, Any]


def discover_subapp_apis(routes: Iterable[Any] | None) -> dict[str, SubAppApi]:
    """Map plugin name → mounted sub-app OpenAPI, from the host app's routes.

    Only ``Mount`` routes whose mounted app exposes a callable ``openapi()``
    returning a document with non-empty ``paths`` are kept (StaticFiles/SPA
    mounts and the core ``/static`` mount are skipped — they carry no API).
    Discovery is best-effort: any mount whose ``openapi()`` raises is ignored
    so a single misbehaving sub-app never breaks the whole export.
    """
    result: dict[str, SubAppApi] = {}
    for route in routes or []:
        sub_app = getattr(route, "app", None)
        name = getattr(route, "name", None)
        path = getattr(route, "path", None)
        if sub_app is None or not name or not isinstance(path, str):
            continue
        openapi_fn = getattr(sub_app, "openapi", None)
        if not callable(openapi_fn):
            continue
        try:
            document = openapi_fn()
        except Exception:
            continue
        if not isinstance(document, dict) or not document.get("paths"):
            continue
        result[str(name)] = SubAppApi(
            plugin_name=str(name),
            mount_path=path.rstrip("/"),
            openapi=document,
        )
    return result


#: Top-level keys copied verbatim from the sub-app document when present.
_PASSTHROUGH_KEYS = ("servers", "security", "tags")


def build_subapp_api_definition(
    openapi: dict[str, Any],
    *,
    mount_path: str,
    title: str,
    description: str = "",
) -> str:
    """Serialise a mounted sub-app's OpenAPI, re-prefixing paths with the mount.

    A sub-app mounted at ``/wikigen`` reports its paths relative to the mount
    (``/api/pages``); at the host they are served at ``/wikigen/api/pages``.
    Re-prefixing keeps the embedded contract addressable.  The sub-app's
    ``components`` are self-contained, so they are copied wholesale (no pruning
    needed).  Returns the document serialised as canonical JSON, ready to embed
    inline in the API entity's ``spec.definition``.
    """
    prefix = mount_path.rstrip("/")
    paths = openapi.get("paths") or {}
    reprefixed = {
        (f"{prefix}{path}" if path.startswith("/") else path): item
        for path, item in paths.items()
    }
    document: dict[str, Any] = {
        "openapi": str(openapi.get("openapi", "3.1.0")),
        "info": {
            "title": f"{title} API",
            "description": description or f"Routes mounted under {prefix}.",
            "version": str((openapi.get("info") or {}).get("version", "0.0.0")),
        },
        "paths": reprefixed,
    }
    for key in _PASSTHROUGH_KEYS:
        if key in openapi:
            document[key] = openapi[key]
    components = openapi.get("components")
    if components:
        document["components"] = components
    return json.dumps(document, sort_keys=True)
