"""The reasoning flow handler clamps the Tree-of-Thoughts search shape.

``max_steps``/``branching_factor`` come from the request context or plugin
config; ToT cost grows with both, so unclamped values let one caller order an
arbitrarily large tree of LLM calls.
"""

from __future__ import annotations

import sys
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

# Resolve through sys.modules: a shim can clobber the package attribute.
import plugins.reasoning_agent.plugin  # noqa: F401

_plugin = sys.modules["plugins.reasoning_agent.plugin"]


def _handler(config: dict[str, Any] | None = None) -> tuple[Any, AsyncMock]:
    agent = MagicMock()
    agent.solve = AsyncMock(return_value={"best_solution": "x"})
    cfg = config or {}
    handler = _plugin.ReasoningFlowHandler(
        agent, config_provider=lambda key, default: cfg.get(key, default)
    )
    return handler, agent.solve


@pytest.mark.parametrize(
    ("context", "expected"),
    [
        ({"max_steps": 10_000, "branching_factor": 500}, (10, 5)),
        ({"max_steps": 0, "branching_factor": -3}, (1, 1)),
        ({"max_steps": "7", "branching_factor": "2"}, (7, 2)),
        ({"max_steps": "lots", "branching_factor": None}, (5, 3)),
        ({"max_steps": True, "branching_factor": 4}, (5, 4)),
        ({}, (5, 3)),
    ],
)
async def test_context_values_are_clamped(context, expected) -> None:
    handler, solve = _handler()
    await handler.handle("solve it", context)
    kwargs = solve.await_args.kwargs
    assert (kwargs["max_steps"], kwargs["branching_factor"]) == expected


async def test_config_values_are_clamped_too() -> None:
    handler, solve = _handler({"max_steps": 999, "branching_factor": 999})
    await handler.handle("solve it", {})
    kwargs = solve.await_args.kwargs
    assert kwargs["max_steps"] == _plugin.MAX_REASONING_STEPS
    assert kwargs["branching_factor"] == _plugin.MAX_BRANCHING_FACTOR
