"""The served path must actually simulate, with memory wired into the colony.

Two defects on the ``scenario_simulation`` / ``collaborative_task`` routes:

1. ``SimulationHandler`` did not override ``handle``. The orchestrator
   dispatches through ``handle``, so a scenario request ran the inherited
   single-pass swarm flow and ``handle_simulation`` was reachable only by
   calling it directly.
2. The per-request colony was minted without a memory manager, so memory
   recall returned nothing and simulation write-back never ran — even with
   ``Orchestrator(memory_manager=...)``.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from core.orchestration.handlers.simulation_handler import (
    DEFAULT_SIMULATION_ROUNDS,
    MAX_SIMULATION_ROUNDS,
    SimulationHandler,
    resolve_rounds,
)
from core.orchestration.handlers.swarm_handler import SwarmHandler


@pytest.mark.parametrize(
    ("context", "expected"),
    [
        ({}, DEFAULT_SIMULATION_ROUNDS),
        ({"rounds": 5}, 5),
        ({"rounds": "2"}, 2),
        ({"rounds": 0}, 1),
        ({"rounds": -4}, 1),
        ({"rounds": 10_000}, MAX_SIMULATION_ROUNDS),
        ({"rounds": "many"}, DEFAULT_SIMULATION_ROUNDS),
        ({"rounds": True}, DEFAULT_SIMULATION_ROUNDS),
        ({"rounds": None}, DEFAULT_SIMULATION_ROUNDS),
    ],
)
def test_resolve_rounds_clamps(context, expected):
    assert resolve_rounds(context) == expected


def _stub_rounds(monkeypatch, seen_colonies: list) -> None:
    async def fake_decompose(self, query, context, dynamic_agent_ids=None):
        seen_colonies.append(self._colony)
        return [{"description": "sub", "capability": "analysis"}]

    async def fake_execute(self, sub_tasks, original_query, context=None):
        return [{"task": "sub", "agent": "A", "result": "ok", "success": True}]

    async def fake_synthesize(self, original_query, sub_results, context):
        return "synth"

    async def fake_report(self, original_query, history):
        return "report"

    monkeypatch.setattr(SimulationHandler, "_decompose_task", fake_decompose)
    monkeypatch.setattr(SimulationHandler, "_execute_subtasks", fake_execute)
    monkeypatch.setattr(SimulationHandler, "_synthesize_results", fake_synthesize)
    monkeypatch.setattr(
        SimulationHandler, "_generate_final_simulation_report", fake_report
    )


async def test_handle_runs_the_multi_round_simulation(monkeypatch):
    seen: list = []
    _stub_rounds(monkeypatch, seen)

    result = await SimulationHandler().handle("scenario", {"rounds": 2})

    assert result["metadata"]["approach"] == "swarm_simulation"
    assert result["metadata"]["total_rounds"] == 2
    assert [r["round"] for r in result["rounds"]] == [1, 2]
    assert len(seen) == 2


async def test_handle_defaults_to_three_rounds(monkeypatch):
    _stub_rounds(monkeypatch, [])

    result = await SimulationHandler().handle("scenario", {})

    assert result["metadata"]["total_rounds"] == DEFAULT_SIMULATION_ROUNDS


async def test_context_memory_manager_reaches_colony_and_gets_writes(monkeypatch):
    seen: list = []
    _stub_rounds(monkeypatch, seen)
    memory = MagicMock()
    memory.add_memory = AsyncMock()

    await SimulationHandler().handle(
        "scenario", {"rounds": 2, "memory_manager": memory}
    )

    assert all(colony.memory_manager is memory for colony in seen)
    assert memory.add_memory.await_count == 2


async def test_handler_memory_manager_used_by_default_factory(monkeypatch):
    seen: list = []
    _stub_rounds(monkeypatch, seen)
    memory = MagicMock()
    memory.add_memory = AsyncMock()

    await SimulationHandler(memory_manager=memory).handle("scenario", {"rounds": 1})

    assert seen[0].memory_manager is memory
    memory.add_memory.assert_awaited_once()


def test_swarm_request_colony_takes_context_memory():
    memory = object()
    colony = SwarmHandler().new_request_colony({"memory_manager": memory})
    assert colony.memory_manager is memory


def test_factory_supplied_memory_is_not_overridden():
    from core.swarm.colony import Colony

    own = object()
    handler = SwarmHandler(colony_factory=lambda: Colony(memory_manager=own))  # type: ignore[arg-type]
    colony = handler.new_request_colony({"memory_manager": object()})
    assert colony.memory_manager is own


def test_orchestrator_hands_its_memory_to_swarm_handlers():
    from core.orchestration import Orchestrator

    memory = MagicMock()
    orchestrator = Orchestrator(
        intent_classifier=MagicMock(), memory_manager=memory, default_intent="qa_docs"
    )
    for intent in ("collaborative_task", "scenario_simulation"):
        handler = orchestrator._flow_handlers[intent]
        assert handler.new_colony().memory_manager is memory
