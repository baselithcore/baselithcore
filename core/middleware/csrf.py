"""
CSRF Origin-validation middleware (pure ASGI).

Rationale: the main API uses Bearer / API-key auth (not browser cookies),
so CSRF only matters for the admin endpoints that rely on HTTP Basic Auth.
Browsers automatically include Basic Auth credentials on same-origin
requests; rejecting cross-origin state-changing requests without an
allowed Origin prevents CSRF on those endpoints.

Requests without an Origin header (e.g. direct curl calls, server-to-
server) are passed through — they cannot be forged by a malicious page.
"""

from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send

_STATE_CHANGING_METHODS = frozenset({"POST", "PUT", "DELETE", "PATCH"})


class CSRFOriginMiddleware:
    """Validate the Origin header on state-changing requests."""

    def __init__(self, app: ASGIApp, allow_origins: list[str]) -> None:
        self.app = app
        self.allow_origins = frozenset(allow_origins)
        self.wildcard = "*" in self.allow_origins

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or scope["method"] not in _STATE_CHANGING_METHODS:
            await self.app(scope, receive, send)
            return

        origin: str | None = None
        for name, value in scope.get("headers", []):
            if name == b"origin":
                origin = value.decode("latin-1")
                break

        if origin and not self.wildcard and origin not in self.allow_origins:
            response = JSONResponse(
                status_code=403,
                content={"detail": "CSRF check failed: origin not allowed."},
            )
            await response(scope, receive, send)
            return

        await self.app(scope, receive, send)
