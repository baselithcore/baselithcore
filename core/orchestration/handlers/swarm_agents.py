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

DECOMPOSITION_PROMPT_TEMPLATE = """Analyze the following complex request and:
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
    "DEFAULT_VIRTUAL_AGENTS",
    "VirtualAgentSpec",
    "max_dynamic_subtasks",
]
