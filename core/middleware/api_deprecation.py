"""Announce deprecated (unprefixed) API paths on the wire (pure ASGI).

The unprefixed copies of the API routers are included with
``deprecated=True`` (see :mod:`core.api.versioning`); that marks them in the
OpenAPI document, which a running client never reads. This middleware adds the
runtime signal to every response a deprecated route produced:

* ``Deprecation: @<unix-time>`` — RFC 9745, the date the path was deprecated;
* ``Link: </v1/<path>>; rel="successor-version"`` — RFC 5829, where to move.

The decision is read when the response starts from the flag the unprefixed
copies' marker dependency (:func:`core.api.versioning.mark_deprecated_path`)
sets on the ASGI scope, so it covers every status the route can answer —
success, a raised ``HTTPException``, a validation error — and streaming
responses alike. A request that never reached a deprecated route (a 404, an
outer-layer short-circuit, any ``/v1`` path) is left untouched.
"""

from __future__ import annotations

from urllib.parse import quote

from starlette.types import ASGIApp, Message, Receive, Scope, Send

from core.api.versioning import (
    DEPRECATED_SCOPE_KEY,
    DEPRECATION_HEADER_VALUE,
    V1_PREFIX,
)


def _successor_link(root_path: str, route_path: str) -> bytes:
    target = quote(f"{root_path}{V1_PREFIX}{route_path}", safe="/-._~{}")
    return f'<{target}>; rel="successor-version"'.encode("latin-1")


class APIDeprecationMiddleware:
    """Add ``Deprecation`` + successor ``Link`` to deprecated-route responses."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        async def send_wrapper(message: Message) -> None:
            if message["type"] == "http.response.start":
                root = str(scope.get("root_path") or "")
                path = str(scope.get("path") or "")
                # Starlette keeps root_path inside ``path``; the successor is
                # the route path re-anchored under root_path + /v1.
                if root and path.startswith(root):
                    path = path[len(root) :]
                if (
                    scope.get(DEPRECATED_SCOPE_KEY)
                    and path
                    and not path.startswith(V1_PREFIX + "/")
                ):
                    headers = list(message.get("headers") or [])
                    headers.append((b"deprecation", DEPRECATION_HEADER_VALUE.encode()))
                    headers.append((b"link", _successor_link(root, path)))
                    message["headers"] = headers
            await send(message)

        await self.app(scope, receive, send_wrapper)


__all__ = ["APIDeprecationMiddleware"]
