"""
Simulation Handler for Orchestrator.

Enables multi-turn social simulation and scenario evolution
using the Swarm Colony.
"""

from typing import Any

from core.observability.logging import get_logger
from core.orchestration.handlers.swarm_colony import request_colony_scope
from core.orchestration.handlers.swarm_handler import SwarmHandler

logger = get_logger(__name__)

#: Rounds run when the request context does not ask for a specific count.
DEFAULT_SIMULATION_ROUNDS = 3
#: Upper bound on ``context["rounds"]``: every round is a full decompose +
#: fan-out + synthesis cycle, so an unbounded caller value is a cost bomb.
MAX_SIMULATION_ROUNDS = 10


def resolve_rounds(context: dict[str, Any]) -> int:
    """Read ``context["rounds"]`` as an int clamped to ``[1, MAX_SIMULATION_ROUNDS]``.

    A missing, non-integer or boolean value falls back to
    :data:`DEFAULT_SIMULATION_ROUNDS`.
    """
    raw = context.get("rounds", DEFAULT_SIMULATION_ROUNDS)
    if isinstance(raw, bool) or not isinstance(raw, int):
        try:
            raw = int(str(raw))
        except ValueError:
            return DEFAULT_SIMULATION_ROUNDS
    return max(1, min(raw, MAX_SIMULATION_ROUNDS))


class SimulationHandler(SwarmHandler):
    """
    Handler for 'scenario_simulation' intent.

    Extends SwarmHandler to support multi-round evolution where agent
    outcomes from round N affect the world state and memory context
    for round N+1.
    """

    async def handle(self, query: str, context: dict[str, Any]) -> dict[str, Any]:
        """Run the multi-round simulation for a ``scenario_simulation`` request.

        The orchestrator dispatches through ``handle``; without this override
        the inherited single-pass :meth:`SwarmHandler.handle` ran instead and
        the simulation never happened on the served path.

        Args:
            query: The scenario to simulate.
            context: Orchestration context. ``context["rounds"]`` selects the
                round count (default 3, clamped to 1-10).

        Returns:
            The final report, the per-round history and run metadata.
        """
        return await self.handle_simulation(
            query, context, rounds=resolve_rounds(context)
        )

    async def handle_simulation(
        self, query: str, context: dict[str, Any], rounds: int = 3
    ) -> dict[str, Any]:
        """Handle a multi-round simulation.

        Runs inside its own :func:`~core.orchestration.handlers.swarm_colony.request_colony_scope`,
        exactly like :meth:`SwarmHandler.handle`. This entry point had none, so
        every ``self._colony`` read fell through to the bounded per-tenant
        registry — and resolved *independently*. A simulation is multi-round and
        long-lived, so on a deployment churning more tenants than that registry
        holds, the LRU could evict and rebuild the colony between the read that
        registered a dynamic agent and the ``finally`` that unregisters it: the
        cleanup then ran against a different colony while the agent stayed
        registered in the original one, competing in its auctions. It also meant
        round N+1 could bid in a colony that had lost round N's pheromone field.
        One scope pins one colony for the whole simulation.

        Note the deliberate change of lifetime this brings: the colony is now
        **minted per simulation** rather than borrowed from the tenant-keyed
        registry, so pheromone and dynamic agents no longer carry from one
        simulation to the next for the same tenant. That matches
        :meth:`SwarmHandler.handle` — a simulation's failure signals are about
        the scenario it was given, and letting them steer the next, unrelated
        scenario was the cross-request bleed the request scope exists to stop.

        Args:
            query: The scenario to simulate.
            context: Orchestration context (carries the per-request loop budget).
            rounds: How many evolution rounds to run.

        Returns:
            The final report, the per-round history and run metadata.
        """
        with request_colony_scope(self.new_request_colony(context)):
            return await self._simulate_in_colony(query, context, rounds)

    async def _simulate_in_colony(
        self, query: str, context: dict[str, Any], rounds: int
    ) -> dict[str, Any]:
        """Body of :meth:`handle_simulation`, with a request colony bound."""
        # DEBUG, truncated: the raw user query is free-text PII — it must
        # not land in INFO-level aggregated logs.
        logger.debug(f"Starting multi-round simulation: {query[:80]} ({rounds} rounds)")

        current_state = query
        all_round_results = []

        # Ids of agents minted for THIS simulation. Belt and braces on top of
        # the request scope: subclasses with their own entry point still rely
        # on the explicit cleanup (same contract as SwarmHandler.handle — this
        # override used to drop the list, leaking up to max_dynamic_subtasks
        # agents per round into every later auction).
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
                "metadata": {
                    # Rounds actually run: an empty decomposition ends early.
                    "total_rounds": len(all_round_results),
                    "requested_rounds": rounds,
                    "approach": "swarm_simulation",
                },
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
