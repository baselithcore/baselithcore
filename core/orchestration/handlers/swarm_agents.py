"""
Virtual agent specifications for the swarm handler.

Default multi-perspective agent roster (Researcher / Analyst / Synthesizer /
Critic) used by :class:`core.orchestration.handlers.swarm_handler.SwarmHandler`.
Extracted to keep modules under the 500-line cap; symbols are re-exported
from ``swarm_handler`` for backward compatibility.
"""

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from core.orchestration.contract import ContractValidator


@dataclass
class VirtualAgentSpec:
    """Specification for a virtual agent in the swarm.

    Attributes:
        name: Display name.
        role: Short role identifier (auction capability matching).
        capabilities: Capability tags the colony matches tasks against.
        system_prompt: The agent's identity/instructions.
        allowed_tools: When set, the sub-agent may invoke only these tools —
            enforced through :func:`contract_for_spec` at the tool
            chokepoint. ``None`` inherits the request-level contract.
        path_scope: Glob patterns bounding which paths this agent may touch;
            overlapping scopes between concurrently spawned agents are
            reported by :func:`detect_scope_conflicts` so parallel writers
            cannot silently collide.
        model: Per-agent model override for its LLM calls (cheap executor /
            strong reviewer splits). ``None`` uses the service default.
    """

    name: str
    role: str
    capabilities: list[str]
    system_prompt: str
    allowed_tools: list[str] | None = None
    path_scope: list[str] | None = None
    model: str | None = None


def contract_for_spec(spec: VirtualAgentSpec) -> "ContractValidator | None":
    """Build a per-subagent contract validator from the spec's tool scope.

    Returns None when the spec declares no ``allowed_tools`` (the sub-agent
    then inherits whatever contract the request context carries).
    """
    if spec.allowed_tools is None:
        return None
    from core.orchestration.contract import (
        AgentContract,
        Capabilities,
        ContractValidator,
    )

    contract = AgentContract(
        name=f"subagent:{spec.name}",
        version="1.0",
        identity=spec.role,
        capabilities=Capabilities(allowed_tools=list(spec.allowed_tools)),
    )
    return ContractValidator(contract)


def _patterns_overlap(left: str, right: str) -> bool:
    """Heuristic glob-overlap: equal, or one pattern matches the other.

    ``fnmatch`` of one pattern against the other treats the target's
    wildcards as literals, which catches the practical collisions (equal
    patterns, and a broad pattern like ``src/**`` subsuming ``src/api/**``)
    without a full glob-intersection solver.
    """
    from fnmatch import fnmatch

    return left == right or fnmatch(right, left) or fnmatch(left, right)


def detect_scope_conflicts(specs: list[VirtualAgentSpec]) -> list[str]:
    """Report overlapping ``path_scope`` patterns between spawned agents.

    Returns one human-readable conflict line per overlapping spec pair;
    empty when every scoped pair is disjoint. Unscoped specs are skipped —
    an agent without a declared scope is not assumed to write anywhere.
    """
    conflicts: list[str] = []
    scoped = [s for s in specs if s.path_scope]
    for i, first in enumerate(scoped):
        for second in scoped[i + 1 :]:
            overlapping = [
                (a, b)
                for a in (first.path_scope or [])
                for b in (second.path_scope or [])
                if _patterns_overlap(a, b)
            ]
            if overlapping:
                a, b = overlapping[0]
                conflicts.append(
                    f"agents '{first.name}' and '{second.name}' have "
                    f"overlapping path scopes ({a!r} vs {b!r})"
                )
    return conflicts


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
    "contract_for_spec",
    "DEFAULT_VIRTUAL_AGENTS",
    "detect_scope_conflicts",
    "VirtualAgentSpec",
    "max_dynamic_subtasks",
]


def register_dynamic_agent(colony: Any, spec: VirtualAgentSpec) -> str:
    """Register a dynamically generated agent with the colony; return its id."""
    import uuid

    from core.swarm.types import AgentProfile, Capability

    metadata: dict = {"system_prompt": spec.system_prompt}
    if spec.model is not None:
        metadata["model"] = spec.model
    profile = AgentProfile(
        id=f"dynamic_{spec.role}_{uuid.uuid4().hex[:8]}",
        name=spec.name,
        capabilities=[
            Capability(name=cap, proficiency=1.0) for cap in spec.capabilities
        ],
        metadata=metadata,
    )
    colony.register_agent(profile)
    return profile.id


async def decompose_task(
    llm_service: Any,
    colony: Any,
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
