"""
Agent Pattern Registry & Complexity Classifier.

Implements two concepts from Chapter 1 of
"Building AI Agents: From Design Patterns to Production":

1. **Pattern Registry** (§1.2)
   The four production-proven agentic patterns:
   - ReAct            — Reasoning + Acting, step-by-step with tool calls.
   - Chain-of-Thought — Full reasoning trace before acting (no interleaved tools).
   - Reflection       — Generate → Critique → Revise loop.
   - Plan-and-Execute — Separate planning phase from mechanical execution.

2. **Complexity Classifier** (§1.4 "When NOT to Use an Agent")
   Determines whether a task actually requires an agent or whether a
   simpler pipeline is more appropriate.  The rule of thumb from the PDF:

       "If you can draw the logic as a flowchart with no branches that
        depend on LLM output, you don't need an agent. Just write the code."

Usage::

    from core.reasoning.patterns import PatternSelector, AgentPattern

    # Auto-select the best pattern for a task
    selector = PatternSelector()
    pattern = selector.select("What is the population of Tokyo?")

    # Check whether an agent is needed at all
    from core.reasoning.patterns import ComplexityClassifier
    assessment = ComplexityClassifier.assess("Send confirmation email to order #42")
    if assessment.use_agent:
        print("Use an agent:", assessment.reason)
    else:
        print("Use a pipeline:", assessment.reason)
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum
from typing import Any

# ---------------------------------------------------------------------------
# Pattern enum
# ---------------------------------------------------------------------------


class AgentPattern(str, Enum):
    """
    The four agentic reasoning patterns that have proven themselves
    in production (Chapter 1.2 of the PDF).
    """

    REACT = "react"
    """
    Reasoning + Acting — alternates Thought/Action/Observation steps.
    Best for: research, question answering, multi-step information gathering.
    """

    CHAIN_OF_THOUGHT = "chain_of_thought"
    """
    Plans the full reasoning trace *before* acting (no interleaved tool calls).
    Best for: complex reasoning, maths, multi-step logic that needs the full
    plan visible upfront.
    """

    REFLECTION = "reflection"
    """
    Generate → Critique → Revise.
    Best for: writing, code generation, any task where first drafts improve
    with self-review.
    """

    PLAN_AND_EXECUTE = "plan_and_execute"
    """
    Creates a full plan upfront, then executes each step mechanically.
    Best for: tasks with clear, stable scope that rarely need mid-course
    corrections.
    """


# ---------------------------------------------------------------------------
# Pattern metadata
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class PatternInfo:
    """Descriptor for a registered agent pattern."""

    pattern: AgentPattern
    description: str
    strengths: list[str]
    weaknesses: list[str]
    best_for: list[str]


_REGISTRY: dict[AgentPattern, PatternInfo] = {
    AgentPattern.REACT: PatternInfo(
        pattern=AgentPattern.REACT,
        description=(
            "Alternates between Thought (explicit reasoning) and Action "
            "(tool call), then reads the Observation before deciding next move."
        ),
        strengths=[
            "Transparent — full reasoning trace for debugging",
            "Adaptive — re-plans after each observation",
            "Works well with tool-rich environments",
        ],
        weaknesses=[
            "Higher latency (one LLM call per iteration)",
            "May over-use tools on simple questions",
        ],
        best_for=[
            "Research and question answering",
            "Multi-step information gathering",
            "Tasks where the next step depends on the previous result",
        ],
    ),
    AgentPattern.CHAIN_OF_THOUGHT: PatternInfo(
        pattern=AgentPattern.CHAIN_OF_THOUGHT,
        description=(
            "Reasons through the entire problem before acting. "
            "Does not interleave tool calls with thinking."
        ),
        strengths=[
            "Full plan visible before execution",
            "Useful for multi-step maths or logic",
            "Lower risk of premature action",
        ],
        weaknesses=[
            "Cannot adapt mid-plan to new information",
            "May hallucinate when factual look-up is needed",
        ],
        best_for=[
            "Complex reasoning and mathematics",
            "Tasks with well-defined, stable scope",
            "Structured analysis reports",
        ],
    ),
    AgentPattern.REFLECTION: PatternInfo(
        pattern=AgentPattern.REFLECTION,
        description=(
            "Two-phase loop: generate an output, critique it, then revise. "
            "Repeats until quality threshold is met."
        ),
        strengths=[
            "Improves output quality iteratively",
            "Catches factual errors and logical gaps",
            "Natural fit for content creation",
        ],
        weaknesses=[
            "Multiple LLM calls per task",
            "Risk of over-correction or infinite loop without iteration cap",
        ],
        best_for=[
            "Writing and editing tasks",
            "Code generation and review",
            "Any domain where first drafts benefit from self-review",
        ],
    ),
    AgentPattern.PLAN_AND_EXECUTE: PatternInfo(
        pattern=AgentPattern.PLAN_AND_EXECUTE,
        description=(
            "Creates a full structured plan upfront (planning phase), then "
            "executes each step mechanically without re-planning."
        ),
        strengths=[
            "Predictable execution path",
            "Easy to audit and monitor",
            "Low per-step latency (no re-planning overhead)",
        ],
        weaknesses=[
            "Brittle if the plan is wrong or if context changes",
            "Cannot adapt to unexpected observations mid-run",
        ],
        best_for=[
            "Tasks with clear, pre-defined scope",
            "Batch processing pipelines",
            "Workflows that need human approval before execution",
        ],
    ),
}


class PatternRegistry:
    """Read-only access to the built-in pattern catalogue."""

    @staticmethod
    def get(pattern: AgentPattern) -> PatternInfo:
        """Return the :class:`PatternInfo` for *pattern*."""
        return _REGISTRY[pattern]

    @staticmethod
    def all() -> list[PatternInfo]:
        """Return all registered pattern descriptors."""
        return list(_REGISTRY.values())


# ---------------------------------------------------------------------------
# Pattern selector
# ---------------------------------------------------------------------------


@dataclass
class SelectionResult:
    """Output of :meth:`PatternSelector.select`."""

    pattern: AgentPattern
    confidence: float  # 0.0 – 1.0
    reason: str


class PatternSelector:
    """
    Heuristic selector that maps a task description to the most appropriate
    :class:`AgentPattern`.

    The selection logic follows the guidance in §1.2 of the PDF:
    - Tasks needing step-by-step information gathering → ReAct
    - Tasks needing deep reasoning without tool calls → CoT
    - Tasks needing iterative refinement of output → Reflection
    - Tasks with known, stable steps → Plan-and-Execute

    For production systems, replace or extend :meth:`select` with an
    LLM-based classifier (see §2.3).
    """

    # Simple keyword heuristics (order matters — first match wins)
    _HEURISTICS: list[tuple[re.Pattern, AgentPattern, float, str]] = [
        (
            re.compile(
                r"\b(search|find|look up|research|what is|who is|when did|"
                r"latest|current|recent|fetch|retrieve)\b",
                re.I,
            ),
            AgentPattern.REACT,
            0.85,
            "Task involves information retrieval → ReAct loop with tool calls.",
        ),
        (
            re.compile(
                r"\b(write|draft|create|compose|generate|improve|edit|"
                r"rewrite|revise|proofread)\b",
                re.I,
            ),
            AgentPattern.REFLECTION,
            0.80,
            "Task involves content creation → Reflection (generate-critique-revise).",
        ),
        (
            re.compile(
                r"\b(plan|schedule|organize|steps|execute|workflow|pipeline|"
                r"batch|process|automate)\b",
                re.I,
            ),
            AgentPattern.PLAN_AND_EXECUTE,
            0.75,
            "Task involves structured workflow → Plan-and-Execute.",
        ),
        (
            re.compile(
                r"\b(calculate|compute|reason|analyse|analyze|prove|explain|"
                r"compare|evaluate|logic|math|theorem)\b",
                re.I,
            ),
            AgentPattern.CHAIN_OF_THOUGHT,
            0.75,
            "Task requires deep reasoning → Chain-of-Thought.",
        ),
    ]

    def select(self, task: str) -> SelectionResult:
        """
        Select the best :class:`AgentPattern` for *task*.

        Args:
            task: Natural-language description of the task.

        Returns:
            :class:`SelectionResult` with the chosen pattern and rationale.
        """
        for pattern_re, pattern, confidence, reason in self._HEURISTICS:
            if pattern_re.search(task):
                return SelectionResult(
                    pattern=pattern, confidence=confidence, reason=reason
                )

        # Default fallback
        return SelectionResult(
            pattern=AgentPattern.REACT,
            confidence=0.50,
            reason="No strong signal detected; defaulting to ReAct as the general-purpose pattern.",
        )

    async def select_with_llm(
        self, task: str, llm_service: Any = None
    ) -> SelectionResult:
        """
        LLM-assisted pattern selection for higher accuracy.

        Falls back to :meth:`select` if the LLM is unavailable.

        Args:
            task: Task description.
            llm_service: :class:`~core.services.llm.LLMService` instance.

        Returns:
            :class:`SelectionResult` from LLM or heuristic fallback.
        """
        if llm_service is None:
            try:
                from core.services.llm import get_llm_service

                llm_service = get_llm_service()
            except Exception:
                return self.select(task)

        prompt = (
            "You are an AI architect. Given the task below, choose the best "
            "agentic reasoning pattern from: react, chain_of_thought, reflection, "
            "plan_and_execute.\n\n"
            f"Task: {task}\n\n"
            "Reply with ONLY the pattern name and a one-sentence reason, e.g.:\n"
            "react: Task requires iterative web searches."
        )
        try:
            response = await llm_service.generate_response(prompt=prompt)
            first_line = response.strip().splitlines()[0]
            for p in AgentPattern:
                if p.value in first_line.lower():
                    reason = (
                        first_line.split(":", 1)[-1].strip()
                        if ":" in first_line
                        else first_line
                    )
                    return SelectionResult(pattern=p, confidence=0.90, reason=reason)
        except Exception:
            pass
        return self.select(task)


__all__ = [
    "AgentPattern",
    "ComplexityAssessment",
    "ComplexityClassifier",
    "PatternInfo",
    "PatternRegistry",
    "PatternSelector",
    "SelectionResult",
]

# Back-compat: the complexity classifier moved to ``core.reasoning.complexity``
# (500-line cap). Imported at the bottom to avoid a circular import — this
# module is fully initialized by the time ``complexity`` imports from it.
from core.reasoning.complexity import (  # noqa: E402
    ComplexityAssessment as ComplexityAssessment,
)
from core.reasoning.complexity import (  # noqa: E402
    ComplexityClassifier as ComplexityClassifier,
)
