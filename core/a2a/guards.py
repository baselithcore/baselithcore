"""Perimeter guards for the A2A JSON-RPC endpoint.

The JSON-RPC dispatcher is reachable by any peer that can open a socket to it,
and its only authentication is the optional HMAC signature — which is *not*
required outside production (see
:func:`core.a2a.security.unauthenticated_a2a_allowed`). Two cheap, local
guards therefore run before any dispatch work:

- **Per-source-IP rate limit** — reuses the same
  :class:`core.middleware.rate_limiter.RateLimiter` (Redis fixed window with an
  in-memory fallback) that ``SecurityManager`` applies to the HTTP API, with the
  same ``<scope>:<ip>`` key shape. Without it, an unsigned deployment hands out
  unmetered agent invocation — and even a signed one hands out unmetered HMAC
  verifications — to anyone who can reach the port.
- **Request body cap** — the dispatcher buffers the whole body to verify the
  signature over the exact bytes, so an unbounded body is a memory-exhaustion
  vector. ``RequestSizeLimitMiddleware`` covers factory-built apps at 10 MiB;
  this local cap defaults to a much tighter 1 MiB (JSON-RPC envelopes are small)
  and also protects routers mounted into a host app that has no such middleware.

Both are configured by environment variable and can be disabled with ``0``:

- ``BASELITH_A2A_RATE_LIMIT_PER_MINUTE`` (default
  :data:`DEFAULT_A2A_RATE_LIMIT_PER_MINUTE`)
- ``BASELITH_A2A_MAX_BODY_BYTES`` (default :data:`DEFAULT_A2A_MAX_BODY_BYTES`)
"""

from __future__ import annotations

import os
from typing import TYPE_CHECKING, Any

from core.observability.logging import get_logger

if TYPE_CHECKING:  # pragma: no cover - typing only
    from fastapi import Request

try:
    from fastapi import HTTPException
    from fastapi.responses import ORJSONResponse
except ImportError:  # pragma: no cover - FastAPI is optional for core.a2a
    HTTPException = None  # type: ignore[assignment, misc]
    ORJSONResponse = None  # type: ignore[assignment, misc]

logger = get_logger(__name__)

_ENV_RATE_LIMIT = "BASELITH_A2A_RATE_LIMIT_PER_MINUTE"
_ENV_MAX_BODY = "BASELITH_A2A_MAX_BODY_BYTES"

#: Requests per minute allowed from a single source IP. A2A peers are machine
#: callers doing task dispatch and polling, so 2 req/s sustained is generous for
#: a legitimate peer while bounding what an unauthenticated flood can cost.
#: Raise it for chatty meshes; ``0`` disables the limit entirely.
DEFAULT_A2A_RATE_LIMIT_PER_MINUTE = 120

#: Fixed window the budget above is measured over (matches the "per minute"
#: naming of the env var, independent of ``RATE_LIMIT_WINDOW_SECONDS``).
A2A_RATE_LIMIT_WINDOW_SECONDS = 60

#: Maximum accepted JSON-RPC request body. A2A envelopes carry text/data parts,
#: not uploads; 1 MiB leaves ample headroom while keeping the buffered read
#: bounded. ``0`` disables the cap.
DEFAULT_A2A_MAX_BODY_BYTES = 1024 * 1024

# Server-error range (-32099..-32000) codes for the two guards. The values
# already taken by core.a2a.protocol.ErrorCode are avoided so a peer can tell
# a throttle apart from a task lookup failure.
A2A_ERROR_RATE_LIMITED = -32008
A2A_ERROR_PAYLOAD_TOO_LARGE = -32009
A2A_ERROR_AGENT_UNAVAILABLE = -32000


def _env_int(name: str, default: int) -> int:
    """Read a non-negative integer from the environment, or ``default``."""
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    try:
        value = int(raw)
    except ValueError:
        logger.warning(
            "Ignoring non-integer %s=%r; falling back to %d", name, raw, default
        )
        return default
    return max(0, value)


def a2a_rate_limit_per_minute() -> int:
    """Per-source-IP request budget for the A2A endpoint (0 = disabled)."""
    return _env_int(_ENV_RATE_LIMIT, DEFAULT_A2A_RATE_LIMIT_PER_MINUTE)


def a2a_max_body_bytes() -> int:
    """Maximum accepted A2A request body in bytes (0 = uncapped)."""
    return _env_int(_ENV_MAX_BODY, DEFAULT_A2A_MAX_BODY_BYTES)


def jsonrpc_error_response(
    status_code: int,
    code: int,
    message: str,
    headers: dict[str, str] | None = None,
) -> Any:
    """Build a JSON-RPC 2.0 error response with an HTTP status code."""
    return ORJSONResponse(
        status_code=status_code,
        content={
            "jsonrpc": "2.0",
            "error": {"code": code, "message": message},
            "id": None,
        },
        headers=headers,
    )


def client_ip(request: Request) -> str:
    """Source IP used as the rate-limit bucket key (``unknown`` when absent)."""
    return request.client.host if request.client else "unknown"


class A2ARateLimitGuard:
    """Per-source-IP fixed-window budget for the A2A JSON-RPC endpoint.

    One instance per router: the limiter is created lazily on the first request
    so importing ``core.a2a`` never opens a Redis client, and a limiter that
    cannot be constructed (no Redis package, unreadable security config) is
    reported once and then skipped rather than breaking the endpoint.

    Args:
        limiter: Optional pre-built limiter. Anything exposing
            ``async check(identifier, limit, window_seconds)`` works; the
            default is :class:`core.middleware.rate_limiter.RateLimiter`.
    """

    def __init__(self, limiter: Any | None = None) -> None:
        self._limiter = limiter
        self._init_failed = False

    def _get_limiter(self) -> Any | None:
        if self._limiter is None and not self._init_failed:
            try:
                from core.middleware.rate_limiter import RateLimiter

                self._limiter = RateLimiter()
            except Exception:
                self._init_failed = True
                logger.warning(
                    "A2A rate limiting unavailable (limiter could not be "
                    "initialized); the endpoint is serving unthrottled.",
                    exc_info=True,
                )
        return self._limiter

    async def check(self, request: Request) -> Any | None:
        """Meter one request.

        Returns:
            ``None`` when the request is within budget, otherwise a JSON-RPC
            error response (429, or 503 under ``RATE_LIMIT_FAIL_MODE=closed``)
            the caller must return immediately.
        """
        limit = a2a_rate_limit_per_minute()
        if limit <= 0 or HTTPException is None:
            return None
        limiter = self._get_limiter()
        if limiter is None:
            return None

        source = client_ip(request)
        try:
            await limiter.check(f"a2a:{source}", limit, A2A_RATE_LIMIT_WINDOW_SECONDS)
        except HTTPException as exc:
            headers = dict(exc.headers or {})
            if exc.status_code == 429:
                logger.warning("Rate-limited A2A request", extra={"client": source})
                return jsonrpc_error_response(
                    429,
                    A2A_ERROR_RATE_LIMITED,
                    "Too many A2A requests; retry after the window resets.",
                    headers,
                )
            # Fail-closed limiter backend (503) — surface it as agent
            # unavailable rather than letting the request through.
            return jsonrpc_error_response(
                exc.status_code,
                A2A_ERROR_AGENT_UNAVAILABLE,
                "A2A rate limiting backend unavailable; request rejected.",
                headers,
            )
        return None


async def read_capped_body(request: Request) -> bytes | None:
    """Buffer the request body, refusing anything over the configured cap.

    The body is streamed and aborted as soon as the cap is exceeded, so an
    oversized (or chunked, ``Content-Length``-less) payload is never fully
    materialized in memory.

    Returns:
        The body bytes, or ``None`` when the cap was exceeded.
    """
    max_bytes = a2a_max_body_bytes()
    if max_bytes <= 0:
        return await request.body()

    declared = request.headers.get("content-length")
    if declared and declared.isdigit() and int(declared) > max_bytes:
        return None

    chunks: list[bytes] = []
    total = 0
    async for chunk in request.stream():
        total += len(chunk)
        if total > max_bytes:
            return None
        chunks.append(chunk)
    return b"".join(chunks)


__all__ = [
    "A2A_ERROR_AGENT_UNAVAILABLE",
    "A2A_ERROR_PAYLOAD_TOO_LARGE",
    "A2A_ERROR_RATE_LIMITED",
    "A2A_RATE_LIMIT_WINDOW_SECONDS",
    "DEFAULT_A2A_MAX_BODY_BYTES",
    "DEFAULT_A2A_RATE_LIMIT_PER_MINUTE",
    "A2ARateLimitGuard",
    "a2a_max_body_bytes",
    "a2a_rate_limit_per_minute",
    "jsonrpc_error_response",
    "read_capped_body",
]
