"""
Cost Control Middleware

Controls computational costs (LLM tokens, GraphDB queries) per request.
Provides budget tracking and enforcement.
"""

from __future__ import annotations

import contextvars
from core.observability.logging import get_logger
import time
from dataclasses import dataclass, field
from typing import List, Optional

from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.types import ASGIApp

from core.config import get_app_config, get_storage_config

logger = get_logger(__name__)


@dataclass
class CostStats:
    """Cost statistics for the current request."""

    tokens_used: int = 0
    graph_queries: int = 0
    start_time: float = field(default_factory=time.time)
    queries_log: List[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        """Convert to dictionary for logging/reporting."""
        return {
            "tokens_used": self.tokens_used,
            "graph_queries": self.graph_queries,
            "duration": round(time.time() - self.start_time, 3),
        }


# ContextVar for per-request isolation
_cost_context: contextvars.ContextVar[Optional[CostStats]] = contextvars.ContextVar(
    "cost_stats", default=None
)


class BudgetExceededError(Exception):
    """Raised when a budget limit is exceeded."""

    pass


class CostController:
    """
    Controller for tracking and limiting computational costs.

    Uses contextvars for thread-safe/async-safe state management.

    Example:
        ```python
        # In middleware
        cost_controller.initialize()

        # In application code
        cost_controller.track_tokens(100, model="gpt-4")
        stats = cost_controller.get_stats()
        ```
    """

    def __init__(
        self,
        enabled: bool = True,
        agent_max_tokens: int = 10000,
        graph_query_limit: int = 100,
        graph_max_hops: int = 5,
    ) -> None:
        self.enabled = enabled
        self.agent_max_tokens = agent_max_tokens
        self.graph_query_limit = graph_query_limit
        self.graph_max_hops = graph_max_hops

    def initialize(self) -> None:
        """Initialize a new counter for the current context."""
        _cost_context.set(CostStats())

    def get_stats(self) -> Optional[CostStats]:
        """Get current statistics (if initialized)."""
        return _cost_context.get()

    def track_tokens(self, count: int, model: str = "unknown") -> None:
        """
        Increment token counter.

        Args:
            count: Number of tokens used
            model: Model name for logging

        Raises:
            BudgetExceededError: If token budget is exceeded
        """
        stats = _cost_context.get()
        if not stats or not self.enabled:
            return

        stats.tokens_used += count

        if stats.tokens_used > self.agent_max_tokens:
            logger.error(
                f"🛑 BUDGET EXCEEDED: Tokens {stats.tokens_used} > {self.agent_max_tokens}"
            )
            raise BudgetExceededError(
                f"Token limit exceeded: {stats.tokens_used}/{self.agent_max_tokens}"
            )

    def track_query(self, cypher: str) -> None:
        """
        Track a graph database query.

        Args:
            cypher: Cypher query string

        Raises:
            BudgetExceededError: If query limit is exceeded
        """
        stats = _cost_context.get()
        if not stats or not self.enabled:
            return

        stats.graph_queries += 1
        stats.queries_log.append(cypher[:100] + "..." if len(cypher) > 100 else cypher)

        if stats.graph_queries > self.graph_query_limit:
            logger.error(
                f"🛑 BUDGET EXCEEDED: Graph Queries {stats.graph_queries} > {self.graph_query_limit}"
            )
            raise BudgetExceededError(
                f"Graph query limit exceeded: {stats.graph_queries}/{self.graph_query_limit}"
            )

        # Pattern validation for unbounded queries
        stripped = cypher.strip().upper()
        if "MATCH (N) RETURN N" in stripped or "MATCH (N) DETACH DELETE N" in stripped:
            if "WHERE" not in stripped and "LIMIT" not in stripped:
                logger.warning(f"⚠️ Potentially unbounded query detected: {cypher}")

    def check_hops(self, hops: int) -> None:
        """
        Verify traversal depth limit.

        Args:
            hops: Number of graph hops

        Raises:
            BudgetExceededError: If hop limit is exceeded
        """
        if not self.enabled:
            return

        if hops > self.graph_max_hops:
            logger.error(f"🛑 TRAVERSAL LIMIT: Hops {hops} > {self.graph_max_hops}")
            raise BudgetExceededError(
                f"Max hop limit exceeded: {hops}/{self.graph_max_hops}"
            )


# Initialize global instance with config
_app_config = get_app_config()
_storage_config = get_storage_config()

cost_controller = CostController(
    enabled=_app_config.cost_control_enabled,
    agent_max_tokens=_app_config.agent_max_tokens,
    graph_query_limit=_storage_config.graph_query_limit,
    graph_max_hops=_storage_config.graph_max_hops,
)


class CostControlMiddleware(BaseHTTPMiddleware):
    """
    Middleware that initializes cost tracking at request start
    and logs the final report.

    WebSocket and lifespan scopes are passed through unchanged.
    """

    def __init__(self, app: ASGIApp, controller: CostController = cost_controller):
        super().__init__(app)
        self.controller = controller

    async def __call__(self, scope, receive, send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        await super().__call__(scope, receive, send)

    async def dispatch(self, request: Request, call_next):
        """Process request with cost tracking."""
        self.controller.initialize()

        try:
            response = await call_next(request)
            return response
        except BudgetExceededError as e:
            logger.warning(f"Cost limit blocked request: {e}")
            return JSONResponse(
                status_code=429, content={"error": "Quota exceeded", "message": str(e)}
            )
        finally:
            stats = self.controller.get_stats()
            if stats:
                logger.info(
                    f"💰 COST REPORT [{request.method} {request.url.path}]: "
                    f"Tokens={stats.tokens_used}, DB_Queries={stats.graph_queries}, "
                    f"Time={round(time.time() - stats.start_time, 3)}s"
                )
