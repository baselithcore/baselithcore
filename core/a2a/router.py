"""
A2A Router

FastAPI router for exposing A2A protocol endpoints.
Provides standard A2A HTTP API including agent card discovery.
"""

from core.observability.logging import get_logger
from typing import Any, Dict, Optional

try:
    from fastapi import APIRouter, Request
    from fastapi.responses import JSONResponse
except ImportError:
    # FastAPI is optional
    APIRouter = None  # type: ignore
    Request = None  # type: ignore
    JSONResponse = None  # type: ignore

from .server import A2AServer

logger = get_logger(__name__)


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

    @router.post("")
    async def dispatch(request: Request) -> JSONResponse:
        """
        Main A2A JSON-RPC endpoint.

        Dispatches incoming JSON-RPC requests to the appropriate handler.
        """
        try:
            body = await request.json()
        except Exception as e:
            logger.warning(f"Failed to parse request body: {e}")
            return JSONResponse(
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
        return JSONResponse(content=response)

    @router.get("/health")
    async def health() -> Dict[str, Any]:
        """Health check endpoint."""
        return {
            "status": "healthy",
            "agent": server.agent_card.name,
            "version": server.agent_card.version,
        }

    @router.get("/agent-card")
    async def get_agent_card() -> Dict[str, Any]:
        """Get the agent card (alternative to well-known)."""
        return server.agent_card.to_dict()

    # Add well-known endpoint at root level if requested
    if include_wellknown:
        # Note: This creates a separate router for /.well-known
        wellknown_router = APIRouter(tags=["A2A Discovery"])

        @wellknown_router.get("/.well-known/agent.json")
        async def wellknown_agent_card() -> Dict[str, Any]:
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
    title: Optional[str] = None,
    version: Optional[str] = None,
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
