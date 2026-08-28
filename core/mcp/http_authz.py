"""Request admission for the MCP Streamable HTTP transport.

Origin validation, authentication, the capability check and per-identity
metering — everything a request must clear before
:mod:`core.mcp.http_transport` hands it to the server. Split out to keep that
module under the 500-line cap.

The transport authenticated its callers and stopped there: every authenticated
identity, including a least-privilege scoped API key minted for an unrelated
resource, reached ``tools/list``, ``resources/read`` and ``tools/call``, and no
per-identity budget metered the endpoint. :func:`build_gate` closes both.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from fastapi import HTTPException, Request, Response
from fastapi.responses import JSONResponse

from core.observability.logging import get_logger

logger = get_logger(__name__)

# RFC 9728 well-known location for OAuth 2.0 Protected Resource Metadata.
METADATA_PATH = "/.well-known/oauth-protected-resource"

# JSON-RPC application error codes this gate emits.
UNAUTHORIZED = -32001
INSUFFICIENT_SCOPE = -32002
RATE_LIMITED = -32003

# Capability demanded when the config object does not declare one (partial test
# doubles). Defaults to the production value rather than to "no check".
DEFAULT_REQUIRED_SCOPE = "mcp:invoke"

_rate_limiter: Any | None = None


def get_rate_limiter() -> Any:
    """Process-wide limiter for the MCP endpoint (built on first use)."""
    global _rate_limiter
    if _rate_limiter is None:
        from core.middleware.rate_limiter import RateLimiter

        _rate_limiter = RateLimiter()
    return _rate_limiter


def reset_rate_limiter() -> None:
    """Drop the cached limiter. For tests that swap the backend."""
    global _rate_limiter
    _rate_limiter = None


def _jsonrpc_error(code: int, message: str, status: int, **kwargs: Any) -> JSONResponse:
    return JSONResponse(
        status_code=status,
        content={
            "jsonrpc": "2.0",
            "id": None,
            "error": {"code": code, "message": message},
        },
        **kwargs,
    )


def origin_rejected(request: Request, allowed_origins: frozenset[str]) -> bool:
    """DNS-rebinding defense: browser origins must be explicitly allowlisted."""
    origin = request.headers.get("origin")
    if origin is None:
        return False
    return origin not in allowed_origins


def metadata_url(request: Request, path: str) -> str:
    """Absolute URL of this resource's RFC 9728 metadata document."""
    base = str(request.base_url).rstrip("/")
    return f"{base}{METADATA_PATH}{path}"


async def authenticate(
    request: Request, resource_metadata_url: str
) -> tuple[Any | None, Response | None]:
    """Resolve the caller through the central AuthManager.

    Returns ``(user, None)`` on success or ``(None, 401 response)`` when the
    credentials are missing or resolve to the anonymous identity. The challenge
    carries ``resource_metadata`` (RFC 9728) so a client that has no token yet
    can discover which authorization server to obtain one from.
    """
    from core.auth.manager import get_auth_manager

    user = await get_auth_manager().authenticate(request.headers.get("authorization"))
    if user is None or not getattr(user, "is_authenticated", False):
        return None, _jsonrpc_error(
            UNAUTHORIZED,
            "Unauthorized",
            401,
            headers={
                "WWW-Authenticate": f'Bearer resource_metadata="{resource_metadata_url}"'
            },
        )
    return user, None


def has_required_scope(user: Any, required: str) -> bool:
    """Whether ``user`` holds ``required``. An empty requirement passes."""
    scope = (required or "").strip()
    if not scope:
        return True
    checker = getattr(user, "has_scope", None)
    if checker is None:
        # A test double or a future identity type without the capability API:
        # refuse rather than silently granting the whole surface.
        return False
    return bool(checker(scope))


async def enforce_rate_limit(cfg: Any, identity: str) -> JSONResponse | None:
    """Meter one request for ``identity``; a JSON-RPC 429 when over budget.

    Returns ``None`` when the request may proceed.
    """
    limit = getattr(cfg, "mcp_http_rate_limit_per_minute", 0)
    if not limit:
        return None
    try:
        await get_rate_limiter().check(f"mcp:{identity}", limit, 60)
    except HTTPException as exc:
        # 429 over budget; 503 when the backend is down and
        # RATE_LIMIT_FAIL_MODE=closed. Both are refusals — pass the status on.
        return _jsonrpc_error(
            RATE_LIMITED,
            "Rate limit exceeded",
            exc.status_code,
            headers=dict(exc.headers or {}),
        )
    return None


def build_gate(
    cfg: Any, path: str, allowed_origins: frozenset[str]
) -> Callable[[Request], Awaitable[tuple[str | None, Response | None]]]:
    """Return the admission gate for one MCP router.

    The gate returns ``(owner, rejection)``: ``rejection`` short-circuits the
    handler; otherwise ``owner`` is the session-owner key.
    """

    async def _gate(request: Request) -> tuple[str | None, Response | None]:
        if origin_rejected(request, allowed_origins):
            logger.warning(
                "mcp_http_origin_rejected", origin=request.headers.get("origin")
            )
            return None, _jsonrpc_error(-32000, "Origin not allowed", 403)

        if not cfg.mcp_http_require_auth:
            # Auth disabled: key the session owner on the peer address rather
            # than a single shared ``None`` bucket, which let any client ride
            # or terminate another's session and let one client exhaust the
            # whole per-owner session cap.
            owner = request.client.host if request.client else "unknown"
            rejection = await enforce_rate_limit(cfg, owner)
            return (owner, rejection) if rejection is None else (None, rejection)

        user, challenge = await authenticate(request, metadata_url(request, path))
        if challenge is not None:
            return None, challenge
        if user is None:
            return None, None

        # Authenticating is not authorizing: without this a scoped key minted
        # for an unrelated resource reached the whole tool catalog.
        required = getattr(cfg, "mcp_http_required_scope", DEFAULT_REQUIRED_SCOPE)
        if not has_required_scope(user, required):
            logger.warning("mcp_http_insufficient_scope", required=required)
            return None, _jsonrpc_error(INSUFFICIENT_SCOPE, "Insufficient scope", 403)

        # Bind identity so tenant-scoped tools resolve the tenant.
        from core.context import set_user_context

        set_user_context(user.user_id)

        owner = str(user.user_id)
        tenant = getattr(user, "tenant_id", None) or "default"
        rejection = await enforce_rate_limit(cfg, f"{tenant}:{owner}")
        return (owner, rejection) if rejection is None else (None, rejection)

    return _gate


__all__ = [
    "DEFAULT_REQUIRED_SCOPE",
    "INSUFFICIENT_SCOPE",
    "METADATA_PATH",
    "RATE_LIMITED",
    "UNAUTHORIZED",
    "authenticate",
    "build_gate",
    "enforce_rate_limit",
    "get_rate_limiter",
    "has_required_scope",
    "metadata_url",
    "origin_rejected",
    "reset_rate_limiter",
]
