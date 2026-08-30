"""
Virtual agent specifications for the swarm handler.

Default multi-perspective agent roster (Researcher / Analyst / Synthesizer /
Critic) used by :class:`core.orchestration.handlers.swarm_handler.SwarmHandler`.
Extracted to keep modules under the 500-line cap; symbols are re-exported
from ``swarm_handler`` for backward compatibility.
"""

from dataclasses import dataclass


@dataclass
class VirtualAgentSpec:
    """Specification for a virtual agent in the swarm."""

    name: str
    role: str
    capabilities: list[str]
    system_prompt: str


# Default virtual agents for the swarm
DEFAULT_VIRTUAL_AGENTS = [
    VirtualAgentSpec(
        name="Researcher",
        role="research",
        capabilities=["web_search", "document_analysis", "fact_checking"],
        system_prompt=(
            "You are a Research Agent. Your role is to gather and verify information. "
            "Be thorough, cite sources, and focus on accuracy."
        ),
    ),
    VirtualAgentSpec(
        name="Analyst",
        role="analysis",
        capabilities=["data_analysis", "pattern_recognition", "statistical_reasoning"],
        system_prompt=(
            "You are an Analysis Agent. Your role is to analyze data and identify patterns. "
            "Be analytical, use structured approaches, and provide quantitative insights."
        ),
    ),
    VirtualAgentSpec(
        name="Synthesizer",
        role="synthesis",
        capabilities=["summarization", "integration", "report_generation"],
        system_prompt=(
            "You are a Synthesis Agent. Your role is to combine insights from multiple sources. "
            "Create coherent narratives and comprehensive summaries."
        ),
    ),
    VirtualAgentSpec(
        name="Critic",
        role="validation",
        capabilities=["critical_thinking", "fact_verification", "quality_assurance"],
        system_prompt=(
            "You are a Critic Agent. Your role is to challenge assumptions and verify conclusions. "
            "Look for logical fallacies, missing information, and potential biases."
        ),
    ),
]

# Embedded fallback for the registry-served ``swarm_decomposition`` catalog
# prompt (core/prompts/catalog/swarm_decomposition.md); ``{{ var }}`` syntax —
# the literal JSON example keeps single braces (the renderer only matches
# ``{{ identifier }}``).
DECOMPOSITION_PROMPT_TEMPLATE = """Analyze the following complex request and:
1. Decompose it into 2-4 independent sub-tasks.
2. For each sub-task, define a specialized virtual agent role.

Request: {{ query }}

Respond with a JSON array of objects:
[
    {
        "description": "detailed task description",
        "capability": "research|analysis|synthesis|validation",
        "agent_name": "Specialized Name",
        "agent_role": "brief_role_identifier",
        "agent_prompt": "Specific system instructions for this agent"
    },
    ...
]
"""


def build_decomposition_prompt(query: str) -> str:
    """Render the swarm decomposition prompt from the registry catalog.

    Registry-served (versioned, label-resolved, provenance span) with the
    embedded template as fallback when the registry is unavailable.
    """
    from core.prompts.catalog import resolve_catalog_prompt

    return resolve_catalog_prompt(
        "swarm_decomposition",
        {"query": query},
        fallback_template=DECOMPOSITION_PROMPT_TEMPLATE,
    )


DEFAULT_MAX_DYNAMIC_SUBTASKS = 4


def max_dynamic_subtasks() -> int:
    """Hard cap on model-emitted sub-tasks per decomposition.

    The prompt asks for 2-4 but that is advisory only; this cap bounds the
    dynamic agents (and parallel executions) a single completion can spawn.
    Env override: ``BASELITH_SWARM_MAX_SUBTASKS`` (min 1).
    """
    import os

    raw = os.getenv("BASELITH_SWARM_MAX_SUBTASKS", str(DEFAULT_MAX_DYNAMIC_SUBTASKS))
    try:
        return max(int(raw), 1)
    except ValueError:
        return DEFAULT_MAX_DYNAMIC_SUBTASKS


__all__ = [
    "DECOMPOSITION_PROMPT_TEMPLATE",
    "DEFAULT_MAX_DYNAMIC_SUBTASKS",
    "build_decomposition_prompt",
    "DEFAULT_VIRTUAL_AGENTS",
    "VirtualAgentSpec",
    "max_dynamic_subtasks",
]


def register_dynamic_agent(colony, spec: VirtualAgentSpec) -> str:
    """Register a dynamically generated agent with the colony; return its id."""
    import uuid

    from core.swarm.types import AgentProfile, Capability

    profile = AgentProfile(
        id=f"dynamic_{spec.role}_{uuid.uuid4().hex[:8]}",
        name=spec.name,
        capabilities=[
            Capability(name=cap, proficiency=1.0) for cap in spec.capabilities
        ],
        metadata={"system_prompt": spec.system_prompt},
    )
    colony.register_agent(profile)
    return profile.id


async def decompose_task(
    llm_service,
    colony,
    query: str,
    dynamic_agent_ids: list[str],
) -> list[dict]:
    """Decompose a complex query into sub-tasks, registering dynamic agents.

    Args:
        llm_service: Service used for the decomposition completion (may be
            ``None`` — falls back to a single analysis task).
        colony: Colony the dynamic agents are registered with.
        query: The root query to decompose.
        dynamic_agent_ids: Output list — the ids of every dynamic agent
            registered for this request are appended here so the caller can
            unregister them when the request completes (the colony lives for
            the process; without cleanup each request leaked its agents into
            every later request's auctions).
    """
    import json

    from core.observability.logging import get_logger

    logger = get_logger(__name__)

    if not llm_service:
        return [{"description": query, "capability": "analysis"}]

    prompt = build_decomposition_prompt(query)
    try:
        response = await llm_service.generate_response(prompt, json=True)
        tasks = json.loads(response)
        if isinstance(tasks, list) and len(tasks) > 0:
            # Hard cap on model-emitted sub-tasks: the "2-4" in the prompt is
            # advisory only — without a cap a single adversarial or malformed
            # completion could spawn an unbounded number of dynamic agents and
            # parallel executions (cost/resource DoS).
            limit = max_dynamic_subtasks()
            sane = [t for t in tasks if isinstance(t, dict)][:limit]
            if len(sane) < len(tasks):
                logger.warning(
                    "swarm_decomposition_truncated emitted=%d kept=%d cap=%d",
                    len(tasks),
                    len(sane),
                    limit,
                )
            if not sane:
                raise ValueError("decomposition yielded no valid task objects")
            for t in sane:
                if "agent_name" in t:
                    spec = VirtualAgentSpec(
                        name=str(t["agent_name"]),
                        role=str(t.get("agent_role", "worker")),
                        capabilities=[str(t.get("capability", "analysis"))],
                        system_prompt=str(t.get("agent_prompt", "")),
                    )
                    dynamic_agent_ids.append(register_dynamic_agent(colony, spec))
            return sane
    except Exception as e:
        logger.warning(f"Dynamic decomposition failed: {e}")

    # Fallback to defaults
    return [
        {"description": f"Research on: {query}", "capability": "research"},
        {"description": f"Analysis of: {query}", "capability": "analysis"},
    ]
