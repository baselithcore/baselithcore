"""One ``Colony`` per request — pheromones and agents must not cross requests.

``SwarmHandler`` built a single ``Colony`` in ``__init__`` and the orchestrator
builds one handler for the process. Every request therefore shared one agent
registry, one auction and — worst — **one pheromone field**: tenant A's
failure signals steered tenant B's bidding, and a dynamic agent minted for one
request competed in every later request's auctions. The handler now mints a
colony per request, and the un-scoped fallback is keyed by tenant so no path
shares pheromone state across tenants.
"""

from __future__ import annotations

import json

import pytest

import core.orchestration.handlers.swarm_handler as sh
from core.config.swarm import SwarmConfig
from core.context import reset_tenant_context, set_tenant_context
from core.orchestration.handlers.swarm_colony import (
    current_colony,
    request_colony_scope,
)
from core.orchestration.handlers.swarm_handler import SwarmHandler
from core.swarm.colony import Colony


class _StubLLM:
    """Returns a fixed decomposition then echo answers."""

    def __init__(self, tasks=None):
        self._tasks = tasks or [{"description": "sub", "capability": "analysis"}]

    async def generate_response(self, prompt, model=None, json=False, **kwargs):
        if json:
            return _json_dumps(self._tasks)
        return "answer"


def _json_dumps(value):
    return json.dumps(value)


@pytest.fixture(autouse=True)
def _no_budget(monkeypatch):
    monkeypatch.setattr(sh, "enforce_iteration", lambda ctx: None)

    async def _noop(ctx, name):
        return None

    monkeypatch.setattr(sh, "enforce_tool_invocation", _noop)


def _handler(**kwargs) -> SwarmHandler:
    kwargs.setdefault("llm_service", _StubLLM())
    return SwarmHandler(**kwargs)


class TestPerRequestColony:
    async def test_each_request_gets_a_fresh_colony(self) -> None:
        handler = _handler()
        seen: list[Colony] = []

        async def capture(sub_tasks, original_query, context=None):
            seen.append(handler._colony)
            return []

        handler._execute_subtasks = capture  # type: ignore[method-assign]
        await handler.handle("q1", {})
        await handler.handle("q2", {})
        assert len(seen) == 2
        assert seen[0] is not seen[1]

    async def test_pheromones_do_not_cross_requests(self) -> None:
        handler = _handler()
        sensed: list[dict[str, float]] = []

        async def deposit_then_sense(sub_tasks, original_query, context=None):
            colony = handler._colony
            sensed.append(colony.pheromones.sense("task_type:analysis"))
            colony.pheromones.deposit("failure", "task_type:analysis", intensity=1.0)
            return []

        handler._execute_subtasks = deposit_then_sense  # type: ignore[method-assign]
        await handler.handle("q1", {})
        await handler.handle("q2", {})
        # The second request started from a clean pheromone field.
        assert sensed[0] == {}
        assert sensed[1] == {}

    async def test_dynamic_agents_do_not_leak_into_the_next_request(self) -> None:
        handler = _handler(
            llm_service=_StubLLM(
                [
                    {
                        "description": "sub",
                        "capability": "analysis",
                        "agent_name": "Minted",
                        "agent_role": "worker",
                        "agent_prompt": "do it",
                    }
                ]
            )
        )
        rosters: list[set[str]] = []

        async def capture(sub_tasks, original_query, context=None):
            rosters.append(set(handler._colony._agents))
            return []

        handler._execute_subtasks = capture  # type: ignore[method-assign]
        await handler.handle("q1", {})
        await handler.handle("q2", {})
        minted_first = {a for a in rosters[0] if a.startswith("dynamic_")}
        assert minted_first, "decomposition should have minted an agent"
        assert not (minted_first & rosters[1])

    async def test_virtual_agents_registered_in_every_request_colony(self) -> None:
        handler = _handler()
        rosters: list[set[str]] = []

        async def capture(sub_tasks, original_query, context=None):
            rosters.append(set(handler._colony._agents))
            return []

        handler._execute_subtasks = capture  # type: ignore[method-assign]
        await handler.handle("q1", {})
        assert "virtual_research" in rosters[0]
        assert "virtual_validation" in rosters[0]

    async def test_scope_is_released_after_the_request(self) -> None:
        handler = _handler()

        async def capture(sub_tasks, original_query, context=None):
            return []

        handler._execute_subtasks = capture  # type: ignore[method-assign]
        assert current_colony() is None
        await handler.handle("q", {})
        assert current_colony() is None

    async def test_injected_colony_factory_is_used(self) -> None:
        built: list[Colony] = []

        def factory() -> Colony:
            colony = Colony(config=SwarmConfig())
            built.append(colony)
            return colony

        handler = _handler(colony_factory=factory)

        async def capture(sub_tasks, original_query, context=None):
            assert handler._colony is built[-1]
            return []

        handler._execute_subtasks = capture  # type: ignore[method-assign]
        await handler.handle("q", {})
        assert len(built) == 1


class TestTenantIsolation:
    def test_unscoped_colony_is_per_tenant(self) -> None:
        handler = _handler()
        token = set_tenant_context("tenant-a")
        try:
            colony_a = handler._colony
            colony_a.pheromones.deposit("failure", "task_type:analysis", intensity=1.0)
        finally:
            reset_tenant_context(token)

        token = set_tenant_context("tenant-b")
        try:
            colony_b = handler._colony
            assert colony_b is not colony_a
            # Tenant A's failure signal must not steer tenant B's bidding.
            assert colony_b.pheromones.sense("task_type:analysis") == {}
        finally:
            reset_tenant_context(token)

        token = set_tenant_context("tenant-a")
        try:
            assert handler._colony is colony_a
        finally:
            reset_tenant_context(token)

    def test_tenant_registry_is_bounded(self) -> None:
        handler = _handler()
        for index in range(sh.MAX_TENANT_COLONIES + 5):
            token = set_tenant_context(f"tenant-{index}")
            try:
                handler._colony
            finally:
                reset_tenant_context(token)
        assert len(handler._tenant_colonies) <= sh.MAX_TENANT_COLONIES

    def test_simulation_handler_inherits_tenant_isolation(self) -> None:
        from core.orchestration.handlers.simulation_handler import SimulationHandler

        handler = SimulationHandler(llm_service=_StubLLM())
        token = set_tenant_context("tenant-a")
        try:
            colony_a = handler._colony
        finally:
            reset_tenant_context(token)
        token = set_tenant_context("tenant-b")
        try:
            assert handler._colony is not colony_a
        finally:
            reset_tenant_context(token)


class TestBackwardCompatibility:
    def test_colony_attribute_is_stable_outside_a_request(self) -> None:
        handler = _handler()
        assert handler._colony is handler._colony

    def test_colony_config_still_exposed(self) -> None:
        config = SwarmConfig(max_concurrent_subtasks=2)
        handler = _handler(colony_config=config)
        assert handler.colony_config is config
        assert handler._colony.config is config

    def test_explicit_scope_overrides_the_tenant_fallback(self) -> None:
        handler = _handler()
        scoped = Colony(config=SwarmConfig())
        with request_colony_scope(scoped):
            assert handler._colony is scoped
        assert handler._colony is not scoped


if __name__ == "__main__":  # pragma: no cover
    pytest.main([__file__, "-v"])
