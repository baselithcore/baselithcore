"""
Tests for Cost Control Middleware and Logic.
"""

import pytest
from unittest.mock import MagicMock
from core.middleware.cost_control import (
    CostController,
    CostControlMiddleware,
    BudgetExceededError,
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
    app = MagicMock()
    middleware = CostControlMiddleware(app, controller=controller)
    request = MagicMock()

    async def call_next(req):
        stats = controller.get_stats()
        assert stats is not None
        return "ok"

    await middleware.dispatch(request, call_next)


@pytest.mark.asyncio
async def test_middleware_catches_budget_error():
    controller = CostController()
    app = MagicMock()
    middleware = CostControlMiddleware(app, controller=controller)
    request = MagicMock()

    async def call_next(req):
        raise BudgetExceededError("Too much!")

    response = await middleware.dispatch(request, call_next)
    assert response.status_code == 429
    # Note: JSONResponse.body is bytes. But here we assume direct response object usage inspection if needed.
