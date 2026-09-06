"""
Simulation Handler for Orchestrator.

Enables multi-turn social simulation and scenario evolution
using the Swarm Colony.
"""

from typing import Any

from core.observability.logging import get_logger
from core.orchestration.handlers.swarm_handler import SwarmHandler

logger = get_logger(__name__)


class SimulationHandler(SwarmHandler):
    """
    Handler for 'scenario_simulation' intent.

    Extends SwarmHandler to support multi-round evolution where agent
    outcomes from round N affect the world state and memory context
    for round N+1.
    """

    async def handle_simulation(
        self, query: str, context: dict[str, Any], rounds: int = 3
    ) -> dict[str, Any]:
        """
        Handle a multi-round simulation.
        """
        # DEBUG, truncated: the raw user query is free-text PII — it must
        # not land in INFO-level aggregated logs.
        logger.debug(f"Starting multi-round simulation: {query[:80]} ({rounds} rounds)")

        current_state = query
        all_round_results = []

        # Ids of agents minted for THIS simulation. The colony outlives the
        # request, so anything registered by decomposition must be
        # unregistered on the way out (same contract as SwarmHandler.handle —
        # this override used to drop the list, leaking up to
        # max_dynamic_subtasks agents per round into every later auction).
        dynamic_agent_ids: list[str] = []
        try:
            for r in range(1, rounds + 1):
                logger.info(f"Starting Simulation Round {r}")

                # 1. Decompose current state into tasks
                sub_tasks = await self._decompose_task(
                    current_state, context, dynamic_agent_ids
                )
                if not sub_tasks:
                    break

                # 2. Execute sub-tasks. ``context`` must be threaded through:
                # the budget enforcement inside _execute_subtasks is a no-op
                # without it, and this rounds × sub-tasks loop is the
                # highest-fan-out path.
                sub_results = await self._execute_subtasks(
                    sub_tasks, current_state, context
                )

                # 3. Synthesize round outcome
                round_synthesis = await self._synthesize_results(
                    current_state, sub_results, context
                )

                all_round_results.append(
                    {
                        "round": r,
                        "sub_results": sub_results,
                        "synthesis": round_synthesis,
                    }
                )

                # 4. Update memory with round outcome (World State update)
                if self._colony.memory_manager:
                    from core.memory.types import MemoryType

                    await self._colony.memory_manager.add_memory(
                        content=f"Outcome of Round {r} for simulation '{query}': {round_synthesis}",
                        memory_type=MemoryType.EPISODIC,
                        metadata={
                            "simulation": query,
                            "round": r,
                            "type": "simulation_outcome",
                        },
                    )

                # 5. Update current state for next round
                current_state = (
                    f"Original Goal: {query}\nPrevious Round Outcome: {round_synthesis}"
                )

            # Final synthesis of the entire simulation
            final_report = await self._generate_final_simulation_report(
                query, all_round_results
            )

            return {
                "response": final_report,
                "rounds": all_round_results,
                "metadata": {"total_rounds": rounds, "approach": "swarm_simulation"},
            }
        finally:
            for agent_id in dynamic_agent_ids:
                self._colony.unregister_agent(agent_id)

    async def _generate_final_simulation_report(
        self, original_query: str, history: list[dict[str, Any]]
    ) -> str:
        """
        Final synthesis of all simulation rounds.
        """
        if not self.llm_service:
            return "Simulation completed. (Synthesis unavailable without LLM)"

        history_text = "\n\n".join(
            f"### Round {h['round']}\n{h['synthesis']}" for h in history
        )

        prompt = f"""You are a Simulation Analysis Agent. Analyze the following multi-round simulation history and provide a final predictive report.

Original Scenario: {original_query}

Simulation History:
{history_text}

Provide a final report that:
1. Summarizes the evolution of the scenario.
2. Identifies key inflection points or emergent behaviors.
3. Provides a final prediction or recommendation based on the simulation outcome.
"""
        summary: str = await self.llm_service.generate_response(prompt)
        return summary
