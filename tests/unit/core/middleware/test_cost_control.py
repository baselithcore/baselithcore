"""
Tests for Cost Control Middleware and Logic.
"""

from unittest.mock import MagicMock

import pytest

from core.middleware.cost_control import (
    BudgetExceededError,
    CostController,
    CostControlMiddleware,
    cost_controller,
)


class TestCostController:
    def test_initialization(self):
        controller = CostController(
            enabled=True,
            agent_max_tokens=100,
            graph_query_limit=10,
            graph_max_hops=2,
        )
        assert controller.enabled
        assert controller.agent_max_tokens == 100

    def test_track_tokens_enforces_limit(self):
        controller = CostController(agent_max_tokens=50)
        controller.initialize()

        controller.track_tokens(30)
        stats = controller.get_stats()
        assert stats.tokens_used == 30

        with pytest.raises(BudgetExceededError):
            controller.track_tokens(21)

    def test_track_query_enforces_limit(self):
        controller = CostController(graph_query_limit=2)
        controller.initialize()

        controller.track_query("MATCH (n) RETURN n")
        controller.track_query("MATCH (n) RETURN n")

        with pytest.raises(BudgetExceededError):
            controller.track_query("MATCH (n) RETURN n")

    def test_check_hops_enforces_limit(self):
        controller = CostController(graph_max_hops=2)
        controller.initialize()

        controller.check_hops(2)
        with pytest.raises(BudgetExceededError):
            controller.check_hops(3)

    def test_disabled_does_not_track(self):
        controller = CostController(enabled=False, agent_max_tokens=10)
        controller.initialize()
        controller.track_tokens(100)
        stats = controller.get_stats()
        # Even if disabled, if initialize is called, stats struct is created,
        # but tracking methods might return early or not update/check.
        # In current impl: if not self.enabled -> return.
        assert stats.tokens_used == 0


@pytest.mark.asyncio
async def test_middleware_initializes_context():
    controller = CostController()

    async def app(scope, receive, send):
        stats = controller.get_stats()
        assert stats is not None
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b"ok"})

    middleware = CostControlMiddleware(app, controller=controller)
    scope = {"type": "http", "method": "GET", "path": "/"}
    sent: list = []

    async def receive():
        return {"type": "http.request"}

    async def send(message):
        sent.append(message)

    await middleware(scope, receive, send)
    assert sent[0]["status"] == 200


@pytest.mark.asyncio
async def test_middleware_catches_budget_error():
    controller = CostController()

    async def app(scope, receive, send):
        raise BudgetExceededError("Too much!")

    middleware = CostControlMiddleware(app, controller=controller)
    scope = {"type": "http", "method": "GET", "path": "/"}
    sent: list = []

    async def receive():
        return {"type": "http.request"}

    async def send(message):
        sent.append(message)

    await middleware(scope, receive, send)
    start = next(m for m in sent if m["type"] == "http.response.start")
    assert start["status"] == 429


def test_llm_service_reports_tokens_to_middleware():
    """LLMService must forward token counts to the global cost_controller."""
    from core.services.llm.service import _report_tokens_to_middleware

    cost_controller.initialize()
    _report_tokens_to_middleware(42, model="input")
    _report_tokens_to_middleware(58, model="output")
    stats = cost_controller.get_stats()
    assert stats is not None
    assert stats.tokens_used == 100


def test_llm_service_budget_error_propagates():
    """Middleware budget overrun raised by the bridge must surface unwrapped."""
    # The token-reporting bridge lives in core.services.llm._telemetry (re-exported
    # from service as _report_tokens_to_middleware); patch cost_controller there.
    import core.services.llm._telemetry as telemetry_module
    from core.middleware.cost_control import CostController
    from core.services.llm.service import _report_tokens_to_middleware

    controller = CostController(agent_max_tokens=10)
    controller.initialize()
    original = telemetry_module.cost_controller
    telemetry_module.cost_controller = controller
    try:
        with pytest.raises(BudgetExceededError):
            _report_tokens_to_middleware(50, model="input")
    finally:
        telemetry_module.cost_controller = original


def test_db_tracking_cursor_increments_queries():
    """TrackingCursor subclasses must report each executed query exactly once."""
    from core.db.connection import _track_db_query

    cost_controller.initialize()
    _track_db_query("SELECT 1")
    _track_db_query("INSERT INTO t VALUES (1)")
    stats = cost_controller.get_stats()
    assert stats is not None
    assert stats.sql_queries == 2
    assert stats.graph_queries == 0


def test_tracking_cursor_subclasses_psycopg_cursor():
    """Tracking cursor factories must be subclasses of psycopg cursors."""
    from psycopg import AsyncCursor, Cursor

    from core.db.connection import TrackingAsyncCursor, TrackingCursor

    assert issubclass(TrackingCursor, Cursor)
    assert issubclass(TrackingAsyncCursor, AsyncCursor)


def test_track_sql_query_stringifies_lazily():
    """Non-str query objects (psycopg Composed/SQL) are stringified only when a
    positive sql_query_limit actually consumes the text."""
    from core.middleware.cost_control import CostController

    class Explosive:
        """Object whose __str__ must NOT run on the default (limit=0) path."""

        def __str__(self) -> str:
            raise AssertionError("stringified on the hot path")

    controller = CostController()
    controller.sql_query_limit = 0
    controller.initialize()
    controller.track_sql_query(Explosive())  # must not raise
    stats = controller.get_stats()
    assert stats is not None and stats.sql_queries == 1
    assert stats.queries_log == []


def test_track_sql_query_logs_text_when_limit_set():
    from core.middleware.cost_control import CostController

    class Composed:
        def __str__(self) -> str:
            return "SELECT something"

    controller = CostController()
    controller.sql_query_limit = 10
    controller.initialize()
    controller.track_sql_query(Composed())
    stats = controller.get_stats()
    assert stats is not None
    assert stats.queries_log == ["SELECT something"]


@pytest.mark.asyncio
async def test_budget_exceeded_handler_renders_429_problem_document():
    """A BudgetExceededError raised deep in application code must surface as a
    429 problem+json, not the catch-all 500 (Starlette's ExceptionMiddleware
    intercepts it before CostControlMiddleware's own except branch)."""
    import orjson

    from core.api.errors import budget_exceeded_handler

    request = MagicMock()
    request.url.path = "/chat"
    response = await budget_exceeded_handler(
        request, BudgetExceededError("SQL query limit exceeded: 51/50")
    )
    assert response.status_code == 429
    body = orjson.loads(response.body)
    assert body["code"] == "budget_exceeded"
    assert body["type"] == "urn:baselith:error:budget_exceeded"
    assert "51/50" in body["detail"]
