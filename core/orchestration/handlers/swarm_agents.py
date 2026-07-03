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

__all__ = ["DEFAULT_VIRTUAL_AGENTS", "VirtualAgentSpec"]
