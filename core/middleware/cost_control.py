"""
Cost Control Middleware

Controls computational costs (LLM tokens, GraphDB queries) per request.
Provides budget tracking and enforcement.
"""

from __future__ import annotations

import contextvars
import time
from dataclasses import dataclass, field

from fastapi.responses import JSONResponse
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from core.config import get_app_config, get_storage_config
from core.observability.logging import get_logger

logger = get_logger(__name__)


@dataclass
class CostStats:
    """Cost statistics for the current request."""

    tokens_used: int = 0
    graph_queries: int = 0
    start_time: float = field(default_factory=time.time)
    queries_log: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        """Convert to dictionary for logging/reporting."""
        return {
            "tokens_used": self.tokens_used,
            "graph_queries": self.graph_queries,
            "duration": round(time.time() - self.start_time, 3),
        }


# ContextVar for per-request isolation
_cost_context: contextvars.ContextVar[CostStats | None] = contextvars.ContextVar(
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

    def get_stats(self) -> CostStats | None:
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


class CostControlMiddleware:
    """Pure ASGI middleware that initializes per-request cost tracking.

    Avoids the overhead of ``BaseHTTPMiddleware`` (anyio task wrapping +
    duplicate queues), preserves streaming semantics, and short-circuits
    non-HTTP scopes.
    """

    def __init__(
        self, app: ASGIApp, controller: CostController = cost_controller
    ) -> None:
        self.app = app
        self.controller = controller

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        self.controller.initialize()
        budget_error: BudgetExceededError | None = None
        response_started = False

        async def send_wrapper(message: Message) -> None:
            nonlocal response_started
            if message["type"] == "http.response.start":
                response_started = True
            await send(message)

        try:
            await self.app(scope, receive, send_wrapper)
        except BudgetExceededError as exc:
            budget_error = exc
            logger.warning(f"Cost limit blocked request: {exc}")
        finally:
            stats = self.controller.get_stats()
            if stats is not None:
                # DEBUG keeps the cost report off the hot path in production
                # while remaining available for local diagnostics.
                logger.debug(
                    "cost_report",
                    method=scope.get("method", ""),
                    path=scope.get("path", ""),
                    tokens=stats.tokens_used,
                    db_queries=stats.graph_queries,
                    duration_seconds=round(time.time() - stats.start_time, 3),
                )

        if budget_error is not None and not response_started:
            response = JSONResponse(
                status_code=429,
                content={"error": "Quota exceeded", "message": str(budget_error)},
            )
            await response(scope, receive, send)
