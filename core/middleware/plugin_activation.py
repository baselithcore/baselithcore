"""
Lazy plugin activation middleware (pure ASGI).

Activates lazy plugins on the first request matching their router prefix.
The middleware is registered during app construction so Starlette's
middleware stack remains immutable after startup; the plugin registry
itself is populated later during lifespan startup and read from app state.

A plugin whose activation failed is answered ``503`` with ``Retry-After`` for
the backoff window (see :mod:`core.plugins._activation_backoff`) without being
re-attempted, so anonymous traffic to a broken plugin's prefix cannot keep
re-importing it under the global activation lock.
"""

from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send

from core.middleware._plugin_route import matched_plugin_route
from core.plugins._activation_backoff import (
    ACTIVATION_BACKOFF_SECONDS,
    PluginActivationBackoffError,
)

_RETRY_AFTER = str(int(ACTIVATION_BACKOFF_SECONDS))


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

        plugin_name = matched_plugin_route(
            scope, plugin_registry, scope.get("path", "")
        )
        if not plugin_name:
            await self.app(scope, receive, send)
            return

        try:
            activated = await plugin_registry.ensure_plugin_active(plugin_name)
        except PluginActivationBackoffError as exc:
            response = JSONResponse(
                status_code=503,
                content={"detail": f"Plugin '{plugin_name}' failed to activate."},
                headers={"Retry-After": str(exc.retry_after)},
            )
            await response(scope, receive, send)
            return
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
                headers={"Retry-After": _RETRY_AFTER},
            )
            await response(scope, receive, send)
            return

        await self.app(scope, receive, send)
