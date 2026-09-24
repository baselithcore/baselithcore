"""The ``TOT_*`` settings are the reasoning handler's ToT defaults.

Regression: ``TOT_MAX_DEPTH``, ``TOT_BRANCHING_FACTOR`` and ``TOT_STRATEGY``
were declared but nothing read them — the handler hard-coded 3/3/"bfs".
"""

from unittest.mock import AsyncMock

import pytest

import core.config.reasoning as reasoning_config
from core.config.reasoning import ReasoningConfig
from core.orchestration.handlers.reasoning import ReasoningHandler


def _handler() -> ReasoningHandler:
    handler = ReasoningHandler()
    handler._tot_engine = AsyncMock()
    handler._tot_engine.solve = AsyncMock(return_value={"solution": "ok", "steps": []})
    return handler


@pytest.mark.asyncio
async def test_defaults_come_from_config(monkeypatch):
    monkeypatch.setattr(
        reasoning_config,
        "_reasoning_config",
        ReasoningConfig(max_depth=5, branching_factor=4, strategy="mcts"),
    )
    handler = _handler()
    await handler.handle("q", {})
    kwargs = handler._tot_engine.solve.await_args.kwargs
    assert (kwargs["k"], kwargs["max_steps"], kwargs["strategy"]) == (4, 5, "mcts")


@pytest.mark.asyncio
async def test_request_context_still_wins(monkeypatch):
    monkeypatch.setattr(
        reasoning_config, "_reasoning_config", ReasoningConfig(max_depth=5)
    )
    handler = _handler()
    await handler.handle("q", {"max_steps": 2, "k": 2})
    kwargs = handler._tot_engine.solve.await_args.kwargs
    assert (kwargs["k"], kwargs["max_steps"]) == (2, 2)


def test_retired_dfs_reads_as_bfs():
    assert ReasoningConfig(strategy="dfs").strategy == "bfs"
