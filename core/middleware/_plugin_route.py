"""Per-request memoization of a plugin route match.

Both :class:`PluginActivationMiddleware` and :class:`PluginContextMiddleware`
resolve the owning plugin of a request via ``registry.match_plugin_route(path)``.
They run in the same request, so the second one reuses the first's result via a
value stashed on the ASGI ``scope`` instead of scanning the route prefixes twice.
"""

from __future__ import annotations

from typing import Any, cast

from starlette.types import Scope

_SCOPE_KEY = "_plugin_route_match"
_UNSET = object()


def matched_plugin_route(scope: Scope, registry: Any, path: str) -> str | None:
    """Return the plugin owning ``path``, computing it at most once per request.

    The result (including ``None``) is cached on ``scope`` so a sibling
    middleware in the same request skips the repeated prefix scan. A registry
    error resolves to ``None`` and is cached like any other miss.
    """
    cached = scope.get(_SCOPE_KEY, _UNSET)
    if cached is not _UNSET:
        return cast("str | None", cached)
    try:
        matched = registry.match_plugin_route(path)
    except Exception:
        matched = None
    result = str(matched) if matched else None
    scope[_SCOPE_KEY] = result
    return result
