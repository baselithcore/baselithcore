"""Plugin Context Middleware.

Attributes each HTTP request to the plugin that owns its route and binds that
identity to :mod:`core.context` for the request's duration, so downstream
framework seams — e.g. the central per-plugin LLM policy — know which plugin
a call executes on behalf of. Resolution is path-derived (router prefix from
the plugin registry, then mounted sub-app prefix) — never a client header.

Pure ASGI (no ``BaseHTTPMiddleware``) and strictly best-effort: resolution
failures pass the request through unattributed, they never fail it.
"""

from __future__ import annotations

from starlette.routing import Mount
from starlette.types import ASGIApp, Receive, Scope, Send

from core.context import reset_plugin_context, set_plugin_context

_MOUNT_MAP_ATTR = "_plugin_ctx_mount_map"


def _mounted_plugins(app: object) -> dict[str, str]:
    """``{mount_prefix: plugin_name}`` for mounted sub-apps (cached on state)."""
    state = getattr(app, "state", None)
    cached = getattr(state, _MOUNT_MAP_ATTR, None)
    if isinstance(cached, dict):
        return cached
    mapping: dict[str, str] = {}
    try:
        for route in getattr(app, "routes", []):
            if isinstance(route, Mount) and route.name and route.path:
                mapping[route.path.rstrip("/")] = route.name
    except Exception:
        mapping = {}
    try:
        if state is not None:
            setattr(state, _MOUNT_MAP_ATTR, mapping)
    except Exception:
        pass
    return mapping


def _resolve_plugin(scope: Scope) -> str | None:
    """Attribute a request path to a plugin: router prefix, then sub-app mount."""
    app = scope.get("app")
    path = scope.get("path", "") or ""
    registry = getattr(getattr(app, "state", None), "plugin_registry", None)
    if registry is not None:
        try:
            matched = registry.match_plugin_route(path)
            if matched:
                return str(matched)
        except Exception:
            pass
    best: str | None = None
    best_len = -1
    for prefix, name in _mounted_plugins(app).items():
        if (path == prefix or path.startswith(f"{prefix}/")) and len(prefix) > best_len:
            best, best_len = name, len(prefix)
    return best


class PluginContextMiddleware:
    """Pure-ASGI middleware binding the owning plugin of each request."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        plugin = _resolve_plugin(scope)
        if plugin is None:
            await self.app(scope, receive, send)
            return
        token = set_plugin_context(plugin)
        try:
            await self.app(scope, receive, send)
        finally:
            reset_plugin_context(token)


__all__ = ["PluginContextMiddleware"]
