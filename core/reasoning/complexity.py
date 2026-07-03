"""
Complexity classifier — agent vs. plain pipeline.

Implements §1.4 of "Building AI Agents: From Design Patterns to Production"
("When NOT to Use an Agent"): decides whether a task warrants an autonomous
agent or a deterministic pipeline. Extracted from
``core/reasoning/patterns.py`` to keep modules under the 500-line cap; the
classes are re-exported there for backward compatibility.

Usage::

    from core.reasoning import ComplexityClassifier

    assessment = ComplexityClassifier.assess("Send confirmation email to #42")
    if assessment.use_agent:
        print("Use an agent:", assessment.reason)
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from core.reasoning.patterns import AgentPattern, PatternSelector


@dataclass
class ComplexityAssessment:
    """
    Result of :meth:`ComplexityClassifier.assess`.

    Attributes:
        use_agent: True when an agent is the right tool; False for a pipeline.
        pattern: Recommended :class:`AgentPattern` (only meaningful when
            ``use_agent`` is True).
        reason: Human-readable rationale.
        signals: List of detected complexity signals that influenced the decision.
    """

    use_agent: bool
    pattern: AgentPattern | None
    reason: str
    signals: list[str]


class ComplexityClassifier:
    """
    Determines whether a task warrants an autonomous agent or a simple
    deterministic pipeline (§1.4 of the PDF).

    Rule of thumb from the PDF:

        "If you can draw the logic as a flowchart with no branches that
         depend on LLM output, you don't need an agent. Just write the code."

    Pipeline signals (do NOT use an agent):
    - Steps are known in advance and don't change.
    - Guaranteed execution order required.
    - Basic CRUD operations.
    - Latency is critical.

    Agent signals (USE an agent):
    - Next step depends on the result of the previous one.
    - Request is ambiguous and may need clarification.
    - Tools to use are not known upfront.
    - Task benefits from self-correction.
    """

    # Keywords that suggest dynamic, branching decisions → agent needed
    _AGENT_SIGNALS: list[tuple[re.Pattern, str]] = [
        (
            re.compile(r"\b(search|look up|find|browse|fetch|retrieve)\b", re.I),
            "requires external data lookup",
        ),
        (
            re.compile(
                r"\b(if|depending|based on|maybe|might|could|unclear|ambiguous)\b", re.I
            ),
            "task contains conditional branching",
        ),
        (
            re.compile(
                r"\b(analyse|analyze|reason|evaluate|decide|judge|choose)\b", re.I
            ),
            "task requires LLM-driven decision making",
        ),
        (
            re.compile(r"\b(write|generate|create|compose|draft)\b", re.I),
            "output quality depends on iterative generation",
        ),
        (
            re.compile(r"\b(correct|fix|improve|refine|review|revise)\b", re.I),
            "task benefits from self-correction",
        ),
        (
            re.compile(r"\b(multiple|several|various|many|different)\b", re.I),
            "multi-step coordination required",
        ),
    ]

    # Keywords that suggest a simple, deterministic pipeline is sufficient
    _PIPELINE_SIGNALS: list[tuple[re.Pattern, str]] = [
        (
            re.compile(
                r"\b(send|email|notify|alert|log|record|save|store|insert|update|delete)\b",
                re.I,
            ),
            "simple CRUD/notification operation",
        ),
        (
            re.compile(
                r"\b(always|every time|fixed|static|predefined|template)\b", re.I
            ),
            "fixed execution path",
        ),
        (
            re.compile(r"\b(validate|check|verify|confirm)\b", re.I),
            "deterministic validation step",
        ),
    ]

    @classmethod
    def assess(cls, task: str) -> ComplexityAssessment:
        """
        Assess whether *task* needs an agent or a plain pipeline.

        Args:
            task: Natural-language description of the task.

        Returns:
            :class:`ComplexityAssessment` with a verdict and rationale.
        """
        agent_signals: list[str] = []
        pipeline_signals: list[str] = []

        for pattern_re, label in cls._AGENT_SIGNALS:
            if pattern_re.search(task):
                agent_signals.append(label)

        for pattern_re, label in cls._PIPELINE_SIGNALS:
            if pattern_re.search(task):
                pipeline_signals.append(label)

        # Scoring: each agent signal is +1, each pipeline signal is -1
        score = len(agent_signals) - len(pipeline_signals)

        if score > 0:
            selector = PatternSelector()
            result = selector.select(task)
            return ComplexityAssessment(
                use_agent=True,
                pattern=result.pattern,
                reason=(
                    f"Agent required ({score} agent signal(s) detected). "
                    f"Recommended pattern: {result.pattern.value}."
                ),
                signals=agent_signals,
            )
        elif score < 0:
            return ComplexityAssessment(
                use_agent=False,
                pattern=None,
                reason=(
                    f"Plain pipeline sufficient ({len(pipeline_signals)} pipeline "
                    "signal(s) detected, no strong agent signal)."
                ),
                signals=pipeline_signals,
            )
        else:
            # Tie: default to agent with low confidence
            return ComplexityAssessment(
                use_agent=True,
                pattern=AgentPattern.REACT,
                reason=(
                    "Signals balanced — defaulting to ReAct agent for safety. "
                    "Consider reviewing if latency is critical."
                ),
                signals=agent_signals + pipeline_signals,
            )


__all__ = ["ComplexityAssessment", "ComplexityClassifier"]
