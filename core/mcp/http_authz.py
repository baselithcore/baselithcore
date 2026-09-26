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
from core.utils.runtime_env import is_production_env

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


def resource_identifier(request: Request, path: str, cfg: Any | None = None) -> str:
    """This endpoint's canonical resource identifier (RFC 8707 / RFC 9728).

    The single source of the value published as ``resource`` in the
    protected-resource metadata *and* the value an access token must name in
    its ``aud`` claim — the two were written independently, which is how an
    audience check ends up validating against something the metadata never
    advertised.

    ``MCP_RESOURCE_URL`` wins when set. The fallback derives the identifier
    from ``request.base_url``, which is built from the ``Host`` header: unless
    the deployment pins the host (``TRUSTED_HOSTS`` / TrustedHost), a caller
    chooses what this endpoint claims to be, and both the advertised resource
    and the audience it is compared against move with it.

    Args:
        request: The request being served.
        path: The MCP endpoint's mount path.
        cfg: MCP config; when it carries a non-empty ``mcp_resource_url`` that
            value is authoritative.

    Returns:
        The canonical resource identifier, without a trailing slash.
    """
    configured = str(getattr(cfg, "mcp_resource_url", "") or "").strip()
    if configured:
        return configured.rstrip("/")
    base = str(request.base_url).rstrip("/")
    return f"{base}{path}"


def resource_unpinned(cfg: Any, trusted_hosts: list[str] | None = None) -> bool:
    """Whether the resource identifier is derived from a caller-chosen Host.

    True when neither ``MCP_RESOURCE_URL`` nor ``TRUSTED_HOSTS`` is set: the
    fallback in :func:`resource_identifier` then builds the identifier — and
    so the audience an access token is checked against — from whatever
    ``Host`` the caller sends.

    Args:
        cfg: MCP config (reads ``mcp_resource_url``).
        trusted_hosts: The host allowlist; ``None`` reads ``TRUSTED_HOSTS``
            from the security config.
    """
    if str(getattr(cfg, "mcp_resource_url", "") or "").strip():
        return False
    if trusted_hosts is None:
        from core.config import get_security_config

        trusted_hosts = list(getattr(get_security_config(), "trusted_hosts", []))
    return not trusted_hosts


def log_unpinned_resource(cfg: Any, trusted_hosts: list[str] | None = None) -> bool:
    """Log an ERROR at mount time when the resource identifier is unpinned.

    Returns:
        Whether the ERROR was emitted (see :func:`resource_unpinned`).
    """
    if not resource_unpinned(cfg, trusted_hosts):
        return False
    logger.error(
        "mcp_http_resource_unpinned",
        note=(
            "neither MCP_RESOURCE_URL nor TRUSTED_HOSTS is set: the MCP "
            "resource identifier and the token audience it is checked against "
            "are derived from the caller-controlled Host header"
        ),
        fix="set MCP_RESOURCE_URL to the public endpoint URL, or TRUSTED_HOSTS",
    )
    return True


def _canonical_resource(value: str) -> str:
    """Normalize a resource URI for comparison.

    Scheme and host are case-insensitive per RFC 3986; a trailing slash is not
    significant for a resource identifier. Everything else is compared as-is —
    a differing path is a different resource, not a formatting difference.
    """
    text = value.strip()
    if not text:
        return ""
    head, separator, tail = text.partition("://")
    if separator:
        authority, slash, rest = tail.partition("/")
        text = f"{head.lower()}://{authority.lower()}{slash}{rest}"
    return text.rstrip("/")


def token_audiences(user: Any) -> tuple[str, ...]:
    """The ``aud`` values the caller's token carries, if any.

    JWT and OIDC identities keep the verified claim set on ``metadata``; an
    API-key identity has none, which is why the caller decides separately
    whether an empty result means "unbound token" or "not a token at all".
    """
    claims = getattr(user, "metadata", None)
    if not isinstance(claims, dict):
        return ()
    audience = claims.get("aud")
    if isinstance(audience, str):
        return (audience,)
    if isinstance(audience, list | tuple):
        return tuple(str(item) for item in audience)
    return ()


def token_audience_rejected(user: Any, resource: str, *, required: bool) -> bool:
    """Whether this token must be refused for not being minted for *resource*.

    *required* is the master switch, not just the missing-``aud`` rule: with it
    off nothing is refused. That is deliberate. ``JWT_AUDIENCE`` is pinned once
    per deployment (``core.auth.jwt`` passes it to every verification), so the
    outcome here is binary — either the issued audience is this endpoint's
    resource identifier or *every* token mismatches and the endpoint is
    unreachable. An operator who cannot reissue tokens today needs a way to
    stand the check down; the alternative is an outage with no lever. The
    setting resolves to the production posture when unset, so the default is
    still enforced where it matters.

    With the check on: an ``aud`` naming a different resource is a refusal —
    that token was issued to somebody else and is being replayed here — and so
    is a token carrying no ``aud`` at all.

    The bare origin is accepted alongside the full endpoint URL: the MCP
    authorization spec lists both as valid canonical resource URIs for an
    endpoint served under a path.
    """
    if not required:
        return False
    audiences = token_audiences(user)
    if not audiences:
        return True
    canonical = _canonical_resource(resource)
    head, separator, tail = canonical.partition("://")
    origin = f"{head}://{tail.partition('/')[0]}" if separator else canonical
    accepted = {canonical, origin}
    return not any(_canonical_resource(item) in accepted for item in audiences)


def audience_required(cfg: Any) -> bool:
    """Whether the RFC 8707 audience check is enforced at all.

    ``None`` (unset) resolves here rather than at config load, so a process
    that arms the production posture after settings were built still fails
    closed. An explicit ``false`` is the documented operator override — see
    :func:`token_audience_rejected` for why one has to exist.
    """
    declared = getattr(cfg, "mcp_require_token_audience", None)
    if declared is None:
        return is_production_env()
    return bool(declared)


def is_bearer_credential(request: Request) -> bool:
    """Whether the caller presented an OAuth bearer token (not an API key).

    Audience binding is a property of OAuth access tokens; an API key has no
    issuer, no audience and nothing to bind, so subjecting it to the check
    would refuse every API-key client for a problem it cannot have.
    """
    header = request.headers.get("authorization") or ""
    return header.split(" ", 1)[0].lower() == "bearer"


async def throttle_auth_failure(request: Request) -> JSONResponse | None:
    """Meter one failed credential against the caller's per-IP authfail window.

    The same ``authfail:<ip>`` bucket and budget
    (``AUTH_FAILURE_LIMIT_PER_MINUTE`` per ``RATE_LIMIT_WINDOW_SECONDS``) that
    :meth:`core.middleware.security.SecurityManager.enforce_auth` charges, so
    a credential-stuffing client cannot get an unmetered stream of 401s by
    switching from the REST surface to the MCP endpoint.

    Returns:
        A JSON-RPC ``429`` (``503`` when the limiter backend is down and
        ``RATE_LIMIT_FAIL_MODE=closed``) once the budget is spent, else
        ``None``.
    """
    from core.middleware._admin_auth import client_bucket
    from core.middleware.security import get_security_manager

    manager = get_security_manager()
    client_ip = request.client.host if request.client else "unknown"
    try:
        await manager.rate_limiter.check(
            f"authfail:{client_bucket(client_ip)}",
            manager.config.auth_failure_limit_per_minute,
            manager.config.rate_limit_window_seconds,
        )
    except HTTPException as exc:
        logger.warning("mcp_http_auth_failures_throttled", ip=client_ip)
        return _jsonrpc_error(
            RATE_LIMITED,
            "Too many failed authentication attempts",
            exc.status_code,
            headers=dict(exc.headers or {}),
        )
    return None


async def authenticate(
    request: Request, resource_metadata_url: str
) -> tuple[Any | None, Response | None]:
    """Resolve the caller through the central AuthManager.

    Returns ``(user, None)`` on success or ``(None, 401 response)`` when the
    credentials are missing or resolve to the anonymous identity. The challenge
    carries ``resource_metadata`` (RFC 9728) so a client that has no token yet
    can discover which authorization server to obtain one from.

    A *presented* credential that fails is charged to the per-IP authfail
    window (:func:`throttle_auth_failure`), which answers ``429`` once spent.
    A request carrying no ``Authorization`` header is not charged: that is
    the spec's discovery step, not a guess.
    """
    from core.auth.manager import get_auth_manager

    header = request.headers.get("authorization")
    user = await get_auth_manager().authenticate(header)
    if user is None or not getattr(user, "is_authenticated", False):
        if header:
            throttled = await throttle_auth_failure(request)
            if throttled is not None:
                return None, throttled
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

        # RFC 8707: a token is valid *for a resource*. Without this check a
        # token an authorization server minted for an unrelated service — or
        # one lifted from it — reached this endpoint's whole tool catalog.
        resource = resource_identifier(request, path, cfg)
        if is_bearer_credential(request) and token_audience_rejected(
            user, resource, required=audience_required(cfg)
        ):
            logger.warning(
                "mcp_http_token_audience_rejected",
                resource=resource,
                audiences=list(token_audiences(user)),
            )
            return None, _jsonrpc_error(
                UNAUTHORIZED,
                "Token is not valid for this resource",
                401,
                headers={
                    "WWW-Authenticate": (
                        'Bearer error="invalid_token", error_description='
                        '"The access token was not issued for this resource", '
                        f'resource_metadata="{metadata_url(request, path)}"'
                    )
                },
            )

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
    "audience_required",
    "authenticate",
    "build_gate",
    "enforce_rate_limit",
    "get_rate_limiter",
    "has_required_scope",
    "is_bearer_credential",
    "log_unpinned_resource",
    "metadata_url",
    "origin_rejected",
    "reset_rate_limiter",
    "resource_identifier",
    "resource_unpinned",
    "throttle_auth_failure",
    "token_audience_rejected",
    "token_audiences",
]
