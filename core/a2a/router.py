"""
A2A Router

FastAPI router for exposing A2A protocol endpoints.
Provides standard A2A HTTP API including agent card discovery.
"""

from typing import Any

from core.observability.logging import get_logger

try:
    from fastapi import APIRouter, Request
    from fastapi.responses import ORJSONResponse
except ImportError:
    # FastAPI is optional
    APIRouter = None  # type: ignore
    Request = None  # type: ignore
    ORJSONResponse = None  # type: ignore

from .agent_card import AgentCard
from .security import (
    SIGNATURE_HEADER,
    TIMESTAMP_HEADER,
    get_a2a_shared_secret,
    unauthenticated_a2a_allowed,
    verify_signature,
    warn_if_unauthenticated_in_production,
)
from .server import A2AServer

logger = get_logger(__name__)


def create_wellknown_router(card: "AgentCard") -> "APIRouter":
    """
    Create a discovery-only router serving the A2A agent card.

    Unlike :func:`create_a2a_router`, this does not require a full
    :class:`A2AServer` — it only exposes the standard discovery endpoint
    ``/.well-known/agent.json`` (plus ``/a2a/agent-card`` as an alias) so a
    host application can advertise its capabilities without committing to a
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

    @router.get("/.well-known/agent.json")
    async def wellknown_agent_card() -> dict[str, Any]:
        """Standard A2A agent-card discovery endpoint."""
        return card.to_dict()

    @router.get("/a2a/agent-card")
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

    @router.post("")
    async def dispatch(request: Request) -> ORJSONResponse:
        """
        Main A2A JSON-RPC endpoint.

        Dispatches incoming JSON-RPC requests to the appropriate handler.
        When BASELITH_A2A_SHARED_SECRET is configured, requests must carry a
        valid HMAC signature (X-A2A-Timestamp / X-A2A-Signature) or they are
        rejected with 401 before any processing.
        """
        raw_body = await request.body()

        secret = get_a2a_shared_secret()
        if secret is not None:
            # Signing configured: require a valid signature.
            authorized = verify_signature(
                raw_body,
                request.headers.get(TIMESTAMP_HEADER),
                request.headers.get(SIGNATURE_HEADER),
                secret,
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
                    "error": {
                        "code": -32700,
                        "message": "Parse error",
                        "data": str(e),
                    },
                    "id": None,
                },
            )

        response = await server.dispatch(body)
        return ORJSONResponse(content=response)

    @router.get("/health")
    async def health() -> dict[str, Any]:
        """Health check endpoint."""
        return {
            "status": "healthy",
            "agent": server.agent_card.name,
            "version": server.agent_card.version,
        }

    @router.get("/agent-card")
    async def get_agent_card() -> dict[str, Any]:
        """Get the agent card (alternative to well-known)."""
        return server.agent_card.to_dict()

    # Add well-known endpoint at root level if requested
    if include_wellknown:
        # Note: This creates a separate router for /.well-known
        wellknown_router = APIRouter(tags=["A2A Discovery"])

        @wellknown_router.get("/.well-known/agent.json")
        async def wellknown_agent_card() -> dict[str, Any]:
            """
            Standard A2A agent card discovery endpoint.

            Per A2A spec, agents should expose their card at this path.
            """
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

    router = create_a2a_router(server)
    app.include_router(router)

    return app
