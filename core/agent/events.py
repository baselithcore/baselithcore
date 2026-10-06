"""Typed events emitted by :meth:`core.agent.Agent.run_events`.

The tool loop used to be observable only through its final
:class:`~core.agent.agent.AgentResult`. A conversational host renders the
turn while it happens — text as it is produced, each tool call as it starts
and finishes — so the loop now yields these events and ``Agent.run`` simply
waits for the last one.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:  # pragma: no cover - typing only
    from core.agent.agent import AgentResult


@dataclass(frozen=True, slots=True)
class TextDelta:
    """Assistant text produced by one model turn (plain-text agents only)."""

    text: str


@dataclass(frozen=True, slots=True)
class ToolCallStarted:
    """The model asked for a tool; it is about to be gated and executed."""

    call_id: str
    name: str
    arguments: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class ToolCallFinished:
    """A tool call returned (``is_error`` when it failed or was refused).

    ``content`` is the observation exactly as the model received it, i.e.
    wrapped in core's untrusted-tool-output envelope, not the raw tool result.
    """

    call_id: str
    name: str
    content: str
    is_error: bool = False


@dataclass(frozen=True, slots=True)
class Completed:
    """The run finished; ``result`` is what :meth:`Agent.run` returns."""

    result: AgentResult[Any]


@dataclass(frozen=True, slots=True)
class Failed:
    """The run raised; only :meth:`Agent.run_events` emits this (as its last event)."""

    error: Exception


AgentEvent = TextDelta | ToolCallStarted | ToolCallFinished | Completed | Failed

__all__ = [
    "AgentEvent",
    "Completed",
    "Failed",
    "TextDelta",
    "ToolCallFinished",
    "ToolCallStarted",
]
