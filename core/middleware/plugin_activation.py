"""
Lazy plugin activation middleware (pure ASGI).

Activates lazy plugins on the first request matching their router prefix.
The middleware is registered during app construction so Starlette's
middleware stack remains immutable after startup; the plugin registry
itself is populated later during lifespan startup and read from app state.
"""

from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send


class PluginActivationMiddleware:
    """Activate lazy plugins on first matching request."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        # Starlette sets scope["app"] before invoking the middleware stack.
        fastapi_app = scope.get("app")
        plugin_registry = getattr(
            getattr(fastapi_app, "state", None), "plugin_registry", None
        )
        if plugin_registry is None:
            await self.app(scope, receive, send)
            return

        plugin_name = plugin_registry.match_plugin_route(scope.get("path", ""))
        if not plugin_name:
            await self.app(scope, receive, send)
            return

        try:
            activated = await plugin_registry.ensure_plugin_active(plugin_name)
        except RuntimeError:
            response = JSONResponse(
                status_code=503,
                content={"detail": "Plugin system is not ready yet."},
            )
            await response(scope, receive, send)
            return

        if not activated:
            response = JSONResponse(
                status_code=503,
                content={"detail": f"Plugin '{plugin_name}' failed to activate."},
            )
            await response(scope, receive, send)
            return

        await self.app(scope, receive, send)
