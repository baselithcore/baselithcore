"""Dynamic agents minted during a simulation must not outlive the request.

``SwarmHandler.handle`` already unregisters them in a ``finally`` block;
``SimulationHandler.handle_simulation`` forgot to thread the id list through
(the base ``_decompose_task`` got ``None`` and used a throwaway list), so up
to ``max_dynamic_subtasks`` agents per round stayed registered in the shared
colony forever and competed in every later request's auctions.
"""

from __future__ import annotations

from core.orchestration.handlers.simulation_handler import SimulationHandler
from core.swarm.types import AgentProfile, Capability


async def test_simulation_unregisters_dynamic_agents(monkeypatch):
    handler = SimulationHandler()
    colony = handler._colony
    baseline_ids = set(colony._agents.keys())

    minted = "dynamic_test_agent"

    async def fake_decompose(llm_service, colony_arg, query, dynamic_agent_ids):
        colony_arg.register_agent(
            AgentProfile(
                id=minted,
                name="Minted",
                capabilities=[Capability(name="analysis", proficiency=0.9)],
            )
        )
        dynamic_agent_ids.append(minted)
        return [{"description": "sub", "capability": "analysis"}]

    async def fake_execute(self, sub_tasks, original_query, context=None):
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

    result = await handler.handle_simulation("scenario", {}, rounds=2)

    assert result["response"] == "report"
    # Unregistered on the way out — no leak into later requests' auctions.
    assert colony.get_agent(minted) is None
    assert set(colony._agents.keys()) == baseline_ids
