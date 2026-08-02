"""Data structures shared by the ReAct loop.

Kept separate from :mod:`core.reasoning.react` so the agent module stays
focused on the loop itself. Every name here is re-exported from
``core.reasoning.react`` — import from there, not from this module.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class StepType(str, Enum):
    """Type of a single ReAct trace entry."""

    THOUGHT = "thought"
    ACTION = "action"
    OBSERVATION = "observation"
    FINAL_ANSWER = "final_answer"


@dataclass
class TraceStep:
    """
    One entry in the agent's reasoning trace.

    Attributes:
        step_type: The role this entry plays (Thought, Action, …).
        iteration: Loop counter when this step was produced.
        content: The textual content of the step.
        tool_name: Populated when ``step_type`` is ACTION.
        tool_args: Raw arguments string passed to the tool.
    """

    step_type: StepType
    iteration: int
    content: str
    tool_name: str | None = None
    tool_args: str | None = None

    def __str__(self) -> str:
        prefix = self.step_type.value.capitalize()
        if self.step_type is StepType.ACTION and self.tool_name:
            return (
                f"[iter={self.iteration}] {prefix}: {self.tool_name}({self.tool_args})"
            )
        return f"[iter={self.iteration}] {prefix}: {self.content}"


@dataclass
class ReActResult:
    """
    Complete output of a ReAct agent run.

    Attributes:
        final_answer: The answer produced by the agent.
        trace: Ordered list of Thought/Action/Observation steps.
        iterations_used: How many loop iterations were consumed.
        hit_limit: True when the run ended because ``max_iterations`` was reached.
    """

    final_answer: str
    trace: list[TraceStep] = field(default_factory=list)
    iterations_used: int = 0
    hit_limit: bool = False


@dataclass
class ToolDefinition:
    """
    Descriptor for a tool the ReAct agent may call.

    Attributes:
        name: Identifier used in the prompt and parsed from LLM output.
        fn: Callable that executes the tool. May be sync or async.
        description: Short, human-readable explanation for the system prompt.
        parameters: Optional JSON-Schema object describing the tool's
            arguments, used by the native tool-calling loop. When None the
            schema is inferred from ``fn``'s signature.
        category: Autonomy category (read_only | mutating | destructive |
            external_side_effect) consulted by the approval gate when the
            agent runs with an ``autonomy_policy``. Defaults to the most
            permissive category, so tools with side effects MUST declare
            theirs explicitly to be gated.
    """

    name: str
    fn: Callable[..., Any]
    description: str
    parameters: dict[str, Any] | None = None
    category: str = "read_only"


__all__ = [
    "ReActResult",
    "StepType",
    "ToolDefinition",
    "TraceStep",
]
