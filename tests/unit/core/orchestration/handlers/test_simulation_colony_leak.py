"""Dynamic agents minted during a simulation must not outlive the request.

Two defects, one symptom — a minted agent still bidding after the simulation:

1. ``SwarmHandler.handle`` unregisters them in a ``finally`` block;
   ``SimulationHandler.handle_simulation`` forgot to thread the id list through
   (the base ``_decompose_task`` got ``None`` and used a throwaway list), so up
   to ``max_dynamic_subtasks`` agents per round stayed registered forever.
2. ``handle_simulation`` opened no colony scope, so every ``self._colony`` read
   resolved through the bounded per-tenant LRU *independently*. With more
   tenants churning than that registry holds, the colony could be evicted and
   rebuilt between the read that registers an agent and the ``finally`` that
   unregisters it — cleanup against a different object, agent still registered
   in the original.
"""

from __future__ import annotations

from contextlib import contextmanager

from core import context as core_context
from core.orchestration.handlers.simulation_handler import SimulationHandler
from core.swarm.types import AgentProfile, Capability


@contextmanager
def _tenant(tenant_id: str):
    token = core_context.set_tenant_context(tenant_id)
    try:
        yield
    finally:
        core_context.reset_tenant_context(token)


MINTED = "dynamic_test_agent"


def _wire(monkeypatch, seen_colonies: list, on_execute=None):
    """Stub decomposition/execution/synthesis, recording the colony used."""

    async def fake_decompose(llm_service, colony_arg, query, dynamic_agent_ids):
        seen_colonies.append(colony_arg)
        colony_arg.register_agent(
            AgentProfile(
                id=MINTED,
                name="Minted",
                capabilities=[Capability(name="analysis", proficiency=0.9)],
            )
        )
        if MINTED not in dynamic_agent_ids:
            dynamic_agent_ids.append(MINTED)
        return [{"description": "sub", "capability": "analysis"}]

    async def fake_execute(self, sub_tasks, original_query, context=None):
        if on_execute is not None:
            on_execute(self)
        return [{"task": "sub", "agent": "Minted", "result": "ok", "success": True}]

    async def fake_synthesize(self, original_query, sub_results, context):
        return "synth"

    async def fake_report(self, original_query, history):
        return "report"

    monkeypatch.setattr(
        "core.orchestration.handlers.swarm_handler.decompose_task", fake_decompose
    )
    monkeypatch.setattr(SimulationHandler, "_execute_subtasks", fake_execute)
    monkeypatch.setattr(SimulationHandler, "_synthesize_results", fake_synthesize)
    monkeypatch.setattr(
        SimulationHandler, "_generate_final_simulation_report", fake_report
    )


async def test_simulation_unregisters_dynamic_agents(monkeypatch):
    handler = SimulationHandler()
    seen: list = []
    _wire(monkeypatch, seen)

    result = await handler.handle_simulation("scenario", {}, rounds=2)

    assert result["response"] == "report"
    # Unregistered from the very colony it was registered in — no leak into
    # later requests' auctions.
    assert seen, "decomposition never ran"
    for colony in seen:
        assert colony.get_agent(MINTED) is None


async def test_one_colony_serves_the_whole_simulation(monkeypatch):
    """Every round bids in the same colony, so round N's pheromone field is
    still there for round N+1."""
    handler = SimulationHandler()
    seen: list = []
    _wire(monkeypatch, seen)

    await handler.handle_simulation("scenario", {}, rounds=3)

    assert len(seen) == 3
    assert all(colony is seen[0] for colony in seen)


async def test_a_tenant_eviction_cannot_swap_the_colony_mid_simulation(monkeypatch):
    """The failure the scope exists to prevent.

    Without a request scope, ``self._colony`` resolved through the bounded
    per-tenant LRU on every read. Another tenant touching the handler while a
    simulation is in flight evicts this one's colony; the ``finally`` then
    rebuilds a fresh colony and unregisters the agent from *that*, leaving it
    registered in the colony it was minted into.
    """
    handler = SimulationHandler()
    # A one-entry registry makes the eviction deterministic: any other tenant
    # touching the handler drops this simulation's colony.
    handler._max_tenant_colonies = 1

    seen: list = []

    def _other_tenant_arrives(_self):
        with _tenant("noisy-neighbour"):
            # The read is what evicts: resolving the property touches the LRU.
            assert _self._colony is not None

    _wire(monkeypatch, seen, on_execute=_other_tenant_arrives)

    with _tenant("simulating-tenant"):
        await handler.handle_simulation("scenario", {}, rounds=2)

    assert seen, "decomposition never ran"
    for colony in seen:
        assert colony.get_agent(MINTED) is None
