"""
Swarm Handler for Orchestrator.

Enables decentralized baselith-core coordination for complex tasks
using the Swarm Colony infrastructure.

Use cases:
- Large document analysis (split + parallel + merge)
- Multi-perspective reasoning
- Complex research tasks requiring diverse expertise
- Collaborative problem-solving
"""

import asyncio
from core.observability.logging import get_logger
from typing import Any, Dict, List, Optional

from core.orchestration.handlers import BaseFlowHandler

# Virtual-agent specs live in a sibling module (500-line cap); re-exported
# so existing imports from swarm_handler keep working.
from core.orchestration.handlers.swarm_agents import (
    DEFAULT_VIRTUAL_AGENTS as DEFAULT_VIRTUAL_AGENTS,
    VirtualAgentSpec as VirtualAgentSpec,
)
from core.swarm.colony import Colony
from core.config.swarm import SwarmConfig
from core.swarm.types import (
    AgentProfile,
    Capability,
    Task,
    TaskPriority,
)

logger = get_logger(__name__)


class SwarmHandler(BaseFlowHandler):
    """
    Handler for 'collaborative_task' intent.

    Uses Swarm Colony for decentralized baselith-core coordination
    on complex tasks that benefit from parallel processing and
    diverse perspectives.

    Workflow:
    1. Break down complex query into sub-tasks
    2. Create Task objects for each sub-task
    3. Submit tasks to Colony for auction-based allocation
    4. Coordinate virtual agent execution
    5. Aggregate and synthesize results

    Example:
        ```python
        handler = SwarmHandler()
        result = await handler.handle(
            query="Deep research on Machine Learning trends in 2024",
            context={}
        )
        ```
    """

    def __init__(
        self,
        *args,
        colony_config: Optional[SwarmConfig] = None,
        virtual_agents: Optional[List[VirtualAgentSpec]] = None,
        llm_service: Optional[Any] = None,
        **kwargs,
    ):
        """
        Initialize the swarm handler.

        Args:
            colony_config: Configuration for the swarm colony.
            virtual_agents: Optional custom virtual agent specifications.
            llm_service: Optional LLM service for task decomposition and synthesis.
            *args, **kwargs: Passed to BaseFlowHandler.
        """
        super().__init__(*args, **kwargs)

        if colony_config:
            self.colony_config = colony_config
        else:
            from core.config.swarm import get_swarm_config

            self.colony_config = get_swarm_config()

        self._colony = Colony(config=self.colony_config)
        self._virtual_agents = virtual_agents or DEFAULT_VIRTUAL_AGENTS

        if llm_service:
            self._llm_service = llm_service
        else:
            # Lazy load will handle fallback
            self._llm_service = None

        self._register_virtual_agents()

    @property
    def llm_service(self):
        """Lazy load LLM service."""
        if self._llm_service is None:
            try:
                from core.services.llm import get_llm_service

                self._llm_service = get_llm_service()
            except Exception as e:
                logger.warning(f"Could not load LLM service: {e}")
        return self._llm_service

    def _register_virtual_agents(self) -> None:
        """Register virtual agents with the colony."""
        for spec in self._virtual_agents:
            profile = AgentProfile(
                id=f"virtual_{spec.role}",
                name=spec.name,
                capabilities=[
                    Capability(name=cap, proficiency=0.9) for cap in spec.capabilities
                ],
            )
            self._colony.register_agent(profile)
            logger.debug(f"Registered virtual agent: {spec.name}")

    async def handle(self, query: str, context: Dict[str, Any]) -> Dict[str, Any]:
        """
        Handle collaborative task request.

        Args:
            query: User's complex query/task
            context: Optional execution context

        Returns:
            Dict with:
                - response: Synthesized final answer
                - sub_results: Results from individual agents
                - coordination_stats: Colony statistics
        """
        try:
            logger.info(f"Starting collaborative task: {query[:100]}...")

            # Step 1: Decompose task into sub-tasks
            sub_tasks = await self._decompose_task(query, context)
            if not sub_tasks:
                return self._fallback_response(query)

            # Step 2: Submit tasks to colony and collect results
            sub_results = await self._execute_subtasks(sub_tasks, query)

            # Step 3: Synthesize final response
            final_response = await self._synthesize_results(query, sub_results, context)

            return {
                "response": final_response,
                "sub_results": sub_results,
                "coordination_stats": self._colony.get_stats(),
                "metadata": {
                    "subtasks_count": len(sub_tasks),
                    "agents_used": len(set(r.get("agent") for r in sub_results)),
                    "approach": "swarm_collaborative",
                },
            }

        except Exception as e:
            logger.error(f"Error in SwarmHandler: {e}", exc_info=True)
            return self._error_response(str(e))

    async def _decompose_task(
        self, query: str, context: Dict[str, Any]
    ) -> List[Dict[str, Any]]:
        """
        Decompose a complex query into independent sub-tasks and dynamic agents.
        """
        if not self.llm_service:
            return [{"description": query, "capability": "analysis"}]

        prompt = f"""Analyze the following complex request and:
1. Decompose it into 2-4 independent sub-tasks.
2. For each sub-task, define a specialized virtual agent role.

Request: {query}

Respond with a JSON array of objects:
[
    {{
        "description": "detailed task description",
        "capability": "research|analysis|synthesis|validation",
        "agent_name": "Specialized Name",
        "agent_role": "brief_role_identifier",
        "agent_prompt": "Specific system instructions for this agent"
    }},
    ...
]
"""
        try:
            response = await self.llm_service.generate_response(prompt, json=True)
            import json

            tasks = json.loads(response)
            if isinstance(tasks, list) and len(tasks) > 0:
                # Register dynamic agents
                for t in tasks:
                    if "agent_name" in t:
                        spec = VirtualAgentSpec(
                            name=t["agent_name"],
                            role=t["agent_role"],
                            capabilities=[t["capability"]],
                            system_prompt=t["agent_prompt"],
                        )
                        self._register_dynamic_agent(spec)
                return tasks
        except Exception as e:
            logger.warning(f"Dynamic decomposition failed: {e}")

        # Fallback to defaults
        return [
            {"description": f"Research on: {query}", "capability": "research"},
            {"description": f"Analysis of: {query}", "capability": "analysis"},
        ]

    def _register_dynamic_agent(self, spec: VirtualAgentSpec) -> None:
        """Register a dynamically generated agent."""
        import uuid

        profile = AgentProfile(
            id=f"dynamic_{spec.role}_{uuid.uuid4().hex[:8]}",
            name=spec.name,
            capabilities=[
                Capability(name=cap, proficiency=1.0) for cap in spec.capabilities
            ],
            metadata={"system_prompt": spec.system_prompt},
        )
        self._colony.register_agent(profile)

    async def _execute_subtasks(
        self, sub_tasks: List[Dict[str, Any]], original_query: str
    ) -> List[Dict[str, Any]]:
        """
        Execute sub-tasks through the swarm colony in parallel.

        Orchestrates the dispatch of tasks to available virtual agents
        and collects their individual results.

        Args:
            sub_tasks: List of task specifications.
            original_query: The root goal for context.

        Returns:
            List[Dict[str, Any]]: Aggregated results from all executed tasks.
        """
        results = []

        async def execute_single(task_def: Dict[str, Any]) -> Dict[str, Any]:
            """
            Execute a single sub-task using assignment logic.

            Finds an agent with matching capabilities and runs the task.

            Args:
                task_def: The specific task request.

            Returns:
                Dict[str, Any]: The individual agent's response.
            """
            task = Task(
                description=task_def["description"],
                required_capabilities=[task_def.get("capability", "analysis")],
                priority=TaskPriority.NORMAL,
                parameters={"original_query": original_query},
            )

            # Submit to colony
            assigned_agent = await self._colony.submit_task(task)
            if not assigned_agent:
                return {
                    "task": task_def["description"],
                    "agent": None,
                    "result": "No agent available",
                    "success": False,
                }

            # Get agent profile
            agent = self._colony.get_agent(assigned_agent)
            if not agent:
                return {
                    "task": task_def["description"],
                    "agent": assigned_agent,
                    "result": "Agent not found",
                    "success": False,
                }

            # Execute task with LLM (simulating agent execution)
            result = await self._execute_with_agent(task_def, agent)

            # Mark task complete
            self._colony.complete_task(task.id, success=True, result=result)

            return {
                "task": task_def["description"],
                "agent": agent.name,
                "result": result,
                "success": True,
            }

        # Execute all sub-tasks in parallel
        tasks_async = [execute_single(t) for t in sub_tasks]
        results = await asyncio.gather(*tasks_async, return_exceptions=True)

        # Handle exceptions
        processed_results: List[Dict[str, Any]] = []
        for i, r in enumerate(results):
            if isinstance(r, BaseException):
                processed_results.append(
                    {
                        "task": sub_tasks[i]["description"],
                        "agent": None,
                        "result": f"Error: {str(r)}",
                        "success": False,
                    }
                )
            elif isinstance(r, dict):
                processed_results.append(r)

        return processed_results

    async def _execute_with_agent(
        self, task_def: Dict[str, Any], agent: AgentProfile
    ) -> str:
        """
        Execute a task with memory-aware virtual agent.
        """
        if not self.llm_service:
            return f"[{agent.name}] Analysis not available without LLM."

        # 1. Fetch memory context
        memory_context = ""
        if self._colony.memory_manager:
            try:
                # Semantic search for relevant memories
                memories = await self._colony.memory_manager.recall(
                    query=task_def["description"], limit=5
                )
                if memories:
                    memory_context = "\n## Relevant Memories\n" + "\n".join(
                        f"- {m.content}" for m in memories
                    )

                # Graph expansion (GraphRAG)
                if self._colony.memory_manager.graph_provider:
                    graph_results = (
                        await self._colony.memory_manager.graph_provider.query_graph(
                            query=task_def["description"]
                        )
                    )
                    if graph_results:
                        memory_context += "\n## Entity Relationships\n" + "\n".join(
                            f"- {r['source']} {r['relation']} {r['target']}"
                            for r in graph_results
                        )
            except Exception as e:
                logger.warning(f"Memory retrieval failed for agent {agent.name}: {e}")

        # 2. Preparation prompt
        system_prompt = agent.metadata.get("system_prompt")
        if not system_prompt:
            agent_spec = next(
                (a for a in self._virtual_agents if f"virtual_{a.role}" == agent.id),
                None,
            )
            system_prompt = (
                agent_spec.system_prompt
                if agent_spec
                else "You are a helpful assistant."
            )

        prompt = f"""{system_prompt}

{memory_context}

Assigned task: {task_def["description"]}

Provide a detailed response, incorporating relevant memories and relationship data if provided.
"""
        try:
            return await self.llm_service.generate_response(prompt)
        except Exception as e:
            return f"[{agent.name}] Error: {str(e)}"

    async def _synthesize_results(
        self,
        original_query: str,
        sub_results: List[Dict[str, Any]],
        context: Dict[str, Any],
    ) -> str:
        """
        Synthesize sub-task results into a coherent final response.

        Args:
            original_query: The initial user prompt.
            sub_results: Collected data from all agents.
            context: Original execution context.

        Returns:
            str: A unified response that answers the original query.
        """
        if not self.llm_service:
            # Simple concatenation fallback
            parts = []
            for r in sub_results:
                if r.get("success"):
                    parts.append(
                        f"**{r.get('agent', 'Agent')}**: {r.get('result', '')}"
                    )
            return "\n\n".join(parts) if parts else "No results available."

        # Build synthesis prompt
        results_text = "\n\n".join(
            f"### Contribution from {r.get('agent', 'Agent')}:\n{r.get('result', 'N/A')}"
            for r in sub_results
            if r.get("success")
        )

        prompt = f"""Synthesize the following contributions from different specialized agents into a cohesive and complete response.

## Original Question
{original_query}

## Agent Contributions
{results_text}

## Instructions
Create a final response that:
1. Integrates all relevant contributions
2. Is well-structured and readable
3. Highlights key points
4. Resolves any contradictions
"""
        try:
            return await self.llm_service.generate_response(prompt)
        except Exception as e:
            logger.error(f"Synthesis failed: {e}")
            # Fallback
            return results_text if results_text else "Synthesis not available."

    def _fallback_response(self, query: str) -> Dict[str, Any]:
        """
        Generate fallback when decomposition fails.

        Args:
            query: The problematic input.

        Returns:
            Dict[str, Any]: A generic but helpful failure message.
        """
        return {
            "response": f"I couldn't decompose the request into sub-tasks. "
            f"Trying with a direct approach: {query}",
            "sub_results": [],
            "coordination_stats": self._colony.get_stats(),
            "metadata": {"approach": "fallback"},
        }

    def _error_response(self, error_message: str) -> Dict[str, Any]:
        """
        Create standardized error response for the swarm handler.

        Args:
            error_message: Details of the failure.

        Returns:
            Dict[str, Any]: Error-formatted handler result.
        """
        return {
            "response": f"Error in task coordination: {error_message}",
            "error": True,
            "sub_results": [],
            "coordination_stats": self._colony.get_stats(),
            "metadata": {"error": error_message},
        }
