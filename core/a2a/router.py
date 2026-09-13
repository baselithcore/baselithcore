"""
A2A Router

FastAPI router for exposing A2A protocol endpoints.
Provides standard A2A HTTP API including agent card discovery.

Discovery is served at the A2A 0.3.0 path ``/.well-known/agent-card.json``.
The pre-0.3.0 ``/.well-known/agent.json`` stays mounted as an alias: peers
built against the older spec still look there, and the card is identical.
"""

import asyncio
import contextlib
import json
from collections.abc import AsyncIterator
from typing import Any

from core.observability.logging import get_logger

try:
    from fastapi import APIRouter, Request
    from fastapi.responses import ORJSONResponse, StreamingResponse
except ImportError:
    # FastAPI is optional
    APIRouter = None  # type: ignore
    Request = None  # type: ignore
    ORJSONResponse = None  # type: ignore
    StreamingResponse = None  # type: ignore

from .agent_card import AgentCard
from .guards import (
    A2A_ERROR_PAYLOAD_TOO_LARGE,
    A2ARateLimitGuard,
    a2a_max_body_bytes,
    jsonrpc_error_response,
    read_capped_body,
)
from .protocol import A2AMethod
from .responses import (
    A2AHealthResponse,
    AgentCardResponse,
    JSONRPCResponseModel,
)
from .security import (
    NONCE_HEADER,
    PEER_HEADER,
    SIGNATURE_HEADER,
    TIMESTAMP_HEADER,
    get_a2a_peer_secrets,
    get_a2a_shared_secret,
    unauthenticated_a2a_allowed,
    verify_signature,
    warn_if_unauthenticated_in_production,
)
from .server import A2AServer

logger = get_logger(__name__)

#: A2A 0.3.0 discovery path.
AGENT_CARD_PATH = "/.well-known/agent-card.json"
#: Pre-0.3.0 path, kept as an alias so existing peers keep resolving.
LEGACY_AGENT_CARD_PATH = "/.well-known/agent.json"

#: How long an SSE stream may stay silent before it sends a comment frame.
#: Proxies and client idle timeouts drop a connection that says nothing, and a
#: dropped stream is indistinguishable from a crashed agent.
SSE_KEEPALIVE_SECONDS = 15.0

#: An SSE comment: ignored by every conformant consumer, but traffic.
_KEEPALIVE_FRAME = ": keepalive\n\n"

_STREAM_HEADERS = {
    "Cache-Control": "no-cache",
    "Connection": "keep-alive",
    # Reverse proxies buffer by default, which would hold every event until
    # the stream ends — exactly what a live stream must not do.
    "X-Accel-Buffering": "no",
}

#: Sentinel put on the queue when the producer is finished.
_END_OF_STREAM = object()

#: Events buffered ahead of a slow consumer before the producer is made to
#: wait. Unbounded, one agent emitting faster than the peer reads is a memory
#: leak with no backpressure anywhere to stop it.
_STREAM_BUFFER = 64


async def sse_frames(events: AsyncIterator[dict[str, Any]]) -> AsyncIterator[str]:
    """Frame *events* as SSE, keeping the connection alive while it is quiet.

    The producer runs as its own task so a silent period can be filled with a
    comment frame instead of the consumer simply blocking. When the consumer
    goes away — the client disconnected, or the response was closed — the
    ``finally`` cancels the producer, so the work behind a stream nobody is
    reading does not keep running (and, for ``message/stream``, does not keep
    an agent turn alive for a peer that hung up).

    Args:
        events: The A2A events to frame, newest first, ending after the event
            carrying ``final: true``.

    Yields:
        SSE frames: ``data:`` events, and ``:`` comments during quiet periods.
    """
    queue: asyncio.Queue[Any] = asyncio.Queue(maxsize=_STREAM_BUFFER)

    async def _pump() -> None:
        try:
            async for event in events:
                # Blocks once the buffer is full, so backpressure reaches the
                # agent producing the events instead of the process's memory.
                await queue.put(event)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # pragma: no cover - defensive
            logger.exception("a2a_stream_producer_failed: %s", exc)
        finally:
            # Best effort: a full buffer means the consumer is still draining,
            # and the ``producer.done()`` checks below end the stream instead.
            with contextlib.suppress(asyncio.QueueFull):
                queue.put_nowait(_END_OF_STREAM)

    producer = asyncio.create_task(_pump())
    try:
        while True:
            try:
                item = queue.get_nowait()
            except asyncio.QueueEmpty:
                if producer.done():
                    return
                try:
                    item = await asyncio.wait_for(
                        queue.get(), timeout=SSE_KEEPALIVE_SECONDS
                    )
                except TimeoutError:
                    # Nothing arrived. Either the producer is merely slow — keep
                    # the connection warm — or it finished and its sentinel did
                    # not fit, in which case end the stream rather than sit out
                    # another keepalive.
                    if producer.done():
                        return
                    yield _KEEPALIVE_FRAME
                    continue
            if item is _END_OF_STREAM:
                return
            yield f"data: {json.dumps(item)}\n\n"
    finally:
        # Consumer gone (or stream finished): never leave the producer running.
        producer.cancel()


def create_wellknown_router(card: "AgentCard") -> "APIRouter":
    """
    Create a discovery-only router serving the A2A agent card.

    Unlike :func:`create_a2a_router`, this does not require a full
    :class:`A2AServer` — it only exposes the standard discovery endpoint
    ``/.well-known/agent-card.json`` (plus the pre-0.3.0
    ``/.well-known/agent.json`` and ``/a2a/agent-card`` as aliases) so a host
    application can advertise its capabilities without committing to a
    JSON-RPC task backend.

    Args:
        card: The agent card to advertise.

    Returns:
        FastAPI APIRouter serving the discovery endpoints.

    Raises:
        ImportError: If FastAPI is not installed.
    """
    if APIRouter is None:
        raise ImportError(
            "FastAPI is required for the A2A discovery router. "
            "Install with: pip install fastapi"
        )

    router = APIRouter(tags=["A2A Discovery"])

    @router.get(
        AGENT_CARD_PATH,
        response_model=AgentCardResponse,
        response_model_exclude_none=True,
    )
    async def wellknown_agent_card() -> dict[str, Any]:
        """Standard A2A agent-card discovery endpoint (0.3.0)."""
        return card.to_dict()

    @router.get(
        LEGACY_AGENT_CARD_PATH,
        response_model=AgentCardResponse,
        response_model_exclude_none=True,
    )
    async def legacy_wellknown_agent_card() -> dict[str, Any]:
        """Pre-0.3.0 discovery path, kept so existing peers still resolve."""
        return card.to_dict()

    @router.get(
        "/a2a/agent-card",
        response_model=AgentCardResponse,
        response_model_exclude_none=True,
    )
    async def agent_card_alias() -> dict[str, Any]:
        """Alias for the agent card under the /a2a prefix."""
        return card.to_dict()

    return router


def create_a2a_router(
    server: A2AServer,
    prefix: str = "/a2a",
    include_wellknown: bool = True,
) -> "APIRouter":
    """
    Create a FastAPI router for A2A protocol endpoints.

    Args:
        server: The A2A server instance to use
        prefix: URL prefix for routes (default: /a2a)
        include_wellknown: Include /.well-known/agent.json endpoint

    Returns:
        FastAPI APIRouter instance

    Raises:
        ImportError: If FastAPI is not installed

    Example:
        ```python
        from fastapi import FastAPI
        from core.a2a import create_a2a_router, AgentCard, EchoA2AServer

        app = FastAPI()
        card = AgentCard(name="echo", description="Echo agent")
        server = EchoA2AServer(card)

        app.include_router(create_a2a_router(server))
        ```
    """
    if APIRouter is None:
        raise ImportError(
            "FastAPI is required for A2A router. Install with: pip install fastapi"
        )

    router = APIRouter(prefix=prefix, tags=["A2A"])
    warn_if_unauthenticated_in_production()
    # One limiter per router; built lazily on the first request.
    rate_limit_guard = A2ARateLimitGuard()

    @router.post("", response_model=JSONRPCResponseModel)
    async def dispatch(request: Request) -> Any:
        """
        Main A2A JSON-RPC endpoint.

        Dispatches incoming JSON-RPC requests to the appropriate handler.
        Every request is first metered per source IP (429 once the budget in
        BASELITH_A2A_RATE_LIMIT_PER_MINUTE is exhausted) and its body is capped
        at BASELITH_A2A_MAX_BODY_BYTES — both run before signature verification,
        so an unauthenticated flood costs no HMAC work.
        When BASELITH_A2A_SHARED_SECRET is configured, requests must carry a
        valid HMAC signature (X-A2A-Timestamp / X-A2A-Signature) or they are
        rejected with 401 before any processing.
        """
        throttled = await rate_limit_guard.check(request)
        if throttled is not None:
            return throttled

        raw_body = await read_capped_body(request)
        if raw_body is None:
            logger.warning(
                "Rejected oversized A2A request body (cap %d bytes)",
                a2a_max_body_bytes(),
                extra={"client": request.client.host if request.client else None},
            )
            return jsonrpc_error_response(
                413,
                A2A_ERROR_PAYLOAD_TOO_LARGE,
                "Request body exceeds the A2A payload limit.",
            )

        secret = get_a2a_shared_secret()
        if secret is not None or get_a2a_peer_secrets():
            # Signing configured (shared and/or per-peer): require a valid
            # signature. A request declaring X-A2A-Peer verifies against that
            # peer's own secret, MAC-bound.
            authorized = verify_signature(
                raw_body,
                request.headers.get(TIMESTAMP_HEADER),
                request.headers.get(SIGNATURE_HEADER),
                secret,
                nonce_header=request.headers.get(NONCE_HEADER),
                peer_header=request.headers.get(PEER_HEADER),
            )
        else:
            # No secret configured: allowed only outside production, or with an
            # explicit opt-in. Fail closed in production so an unsigned peer
            # cannot invoke the agent by default.
            authorized = unauthenticated_a2a_allowed()

        if not authorized:
            logger.warning(
                "Rejected A2A request (missing/invalid signature or unsigned "
                "request while unauthenticated A2A is disabled)",
                extra={"client": request.client.host if request.client else None},
            )
            return ORJSONResponse(
                status_code=401,
                content={
                    "jsonrpc": "2.0",
                    "error": {
                        "code": -32001,
                        "message": (
                            "Unauthorized: A2A request signing is required "
                            "(set BASELITH_A2A_SHARED_SECRET)"
                        ),
                    },
                    "id": None,
                },
            )

        try:
            import json as _json

            body = _json.loads(raw_body)
        except Exception as e:
            logger.warning(f"Failed to parse request body: {e}")
            return ORJSONResponse(
                status_code=400,
                content={
                    "jsonrpc": "2.0",
                    # No exception text in the payload: the parser message is
                    # library-generated and adds nothing a caller can act on.
                    # It is logged above for whoever debugs the request.
                    "error": {
                        "code": -32700,
                        "message": "Parse error",
                    },
                    "id": None,
                },
            )

        # message/stream is served as Server-Sent Events (text/event-stream):
        # each A2A event is one `data:` frame, terminated by the event carrying
        # `final: true`. This matches the streaming=True capability advertised on
        # the agent card, so conformant peers no longer break on this method.
        # sse_frames adds the keepalive comments an idle stream needs to
        # survive a proxy, and cancels the agent turn behind a stream whose
        # consumer has gone.
        if (
            isinstance(body, dict)
            and body.get("method") == A2AMethod.MESSAGE_STREAM.value
        ):
            return StreamingResponse(
                sse_frames(server.dispatch_stream(body)),
                media_type="text/event-stream",
                headers=dict(_STREAM_HEADERS),
            )

        response = await server.dispatch(body)
        return ORJSONResponse(content=response)

    @router.get("/health", response_model=A2AHealthResponse)
    async def health() -> dict[str, Any]:
        """Health check endpoint."""
        return {
            "status": "healthy",
            "agent": server.agent_card.name,
            "version": server.agent_card.version,
        }

    @router.get(
        "/agent-card",
        response_model=AgentCardResponse,
        response_model_exclude_none=True,
    )
    async def get_agent_card() -> dict[str, Any]:
        """Get the agent card (alternative to well-known)."""
        return server.agent_card.to_dict()

    # Add well-known endpoint at root level if requested
    if include_wellknown:
        # Note: This creates a separate router for /.well-known
        wellknown_router = APIRouter(tags=["A2A Discovery"])

        @wellknown_router.get(
            AGENT_CARD_PATH,
            response_model=AgentCardResponse,
            response_model_exclude_none=True,
        )
        async def wellknown_agent_card() -> dict[str, Any]:
            """
            Standard A2A agent card discovery endpoint (0.3.0).

            Per A2A spec, agents expose their card at this path.
            """
            return server.agent_card.to_dict()

        @wellknown_router.get(
            LEGACY_AGENT_CARD_PATH,
            response_model=AgentCardResponse,
            response_model_exclude_none=True,
        )
        async def legacy_wellknown_agent_card() -> dict[str, Any]:
            """Pre-0.3.0 discovery path, kept so existing peers still resolve."""
            return server.agent_card.to_dict()

        # Return combined router
        combined = APIRouter()
        combined.include_router(router)
        combined.include_router(wellknown_router)
        return combined

    return router


def create_standalone_app(
    server: A2AServer,
    title: str | None = None,
    version: str | None = None,
) -> Any:
    """
    Create a standalone FastAPI application for an A2A server.

    Args:
        server: The A2A server instance
        title: API title (defaults to agent name)
        version: API version (defaults to agent version)

    Returns:
        FastAPI application instance

    Example:
        ```python
        from core.a2a import AgentCard, EchoA2AServer, create_standalone_app

        card = AgentCard(name="echo", description="Echo agent")
        server = EchoA2AServer(card)
        app = create_standalone_app(server)

        # Run with: uvicorn mymodule:app
        ```
    """
    try:
        from fastapi import FastAPI
    except ImportError:
        raise ImportError(
            "FastAPI is required. Install with: pip install fastapi"
        ) from None

    app = FastAPI(
        title=title or f"{server.agent_card.name} A2A API",
        version=version or server.agent_card.version,
        description=server.agent_card.description,
    )

    # This app is a documented deployment shape, not a demo: it faces peer
    # agents directly, without the middleware stack core.api.factory builds.
    # Mount the two perimeter guards that a bare FastAPI has no equivalent for
    # — an unbounded request body is a trivial memory-exhaustion vector, and a
    # response with no CSP/HSTS/nosniff is one an embedded browser will happily
    # over-trust. Both are pure ASGI, so they add no per-request task hop.
    # Imported lazily to keep `core.a2a` importable without the HTTP stack.
    try:
        from core.middleware.security_headers import (
            RequestSizeLimitMiddleware,
            SecurityHeadersMiddleware,
        )

        # Order matters and is the reverse of the call order: add_middleware
        # inserts at index 0, so the LAST added ends up outermost. Registering
        # the size limit first and the headers second puts SecurityHeaders on
        # the outside, so the 413 the size limiter short-circuits still carries
        # CSP/HSTS/nosniff. This mirrors core.api.factory, where the same
        # ordering is deliberate.
        app.add_middleware(RequestSizeLimitMiddleware)
        app.add_middleware(SecurityHeadersMiddleware)
    except Exception:  # pragma: no cover - never block a standalone bring-up
        logger.warning(
            "A2A standalone app: security middleware unavailable; "
            "serving without request-size and security-header guards.",
            exc_info=True,
        )

    router = create_a2a_router(server)
    app.include_router(router)

    return app
