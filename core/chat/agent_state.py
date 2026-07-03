"""
Agent State Model.

Defines the shared state between chat agent steps.
Migrated from app/chat/agent_state.py
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any, ClassVar, TypedDict

from core.chat.guardrails import GuardrailDecision
from core.evaluation.trajectory import ToolCall


@dataclass
class AgentState:
    """Shared state between chat agent steps."""

    # Sliding-window caps to prevent unbounded growth on long-running sessions.
    # Older entries are pruned in-place when the cap is exceeded. Tune via
    # ``AgentState.MAX_TRAJECTORY_ENTRIES`` / ``MAX_LOG_ENTRIES`` at process
    # start; per-instance overrides flow through ``record_tool_call``/``log``.
    MAX_TRAJECTORY_ENTRIES: ClassVar[int] = 200
    MAX_LOG_ENTRIES: ClassVar[int] = 500

    request: Any  # ChatRequest - avoid circular import
    user_query: str = ""
    rerank_query: str = ""
    normalized_query: str = ""
    conversation_id: str | None = None
    history_turns: Sequence[Any] = ()
    history_text: str = ""
    query_vector: list[float] | None = None
    hits: Sequence[Any] = ()
    ranked_hits: Sequence[Any] = ()
    context: str = ""
    doc_sources: list[dict[str, Any]] = field(default_factory=list)
    source_metrics: dict[str, Any] = field(default_factory=dict)
    cache_key: tuple[str, str] | None = None
    answer: Any | None = None
    done: bool = False
    next_action: str = "validate_input"
    logs: list[str] = field(default_factory=list)
    guardrail_decision: GuardrailDecision | None = None
    clarification_reason: str | None = None
    # Generic plugin data storage - plugins can store their data here
    # Example: plugin_data["issue_tracker"] = {"project_plan": ..., "issues": [...]}
    plugin_data: dict[str, Any] = field(default_factory=dict)
    rag_only: bool = False
    # Loop instrumentation: track iteration, retries, cost, and the
    # tool-call trajectory for trajectory-aware evaluation.
    iteration_count: int = 0
    retry_count: int = 0
    cost_usd: float = 0.0
    scratchpad_ref: str | None = None
    trajectory: list[ToolCall] = field(default_factory=list)
    # Counters preserved across pruning so callers can detect dropped entries.
    trajectory_dropped: int = 0
    logs_dropped: int = 0

    def log(self, message: str) -> None:
        """Append log message, pruning the oldest if the cap is exceeded."""
        self.logs.append(message)
        overflow = len(self.logs) - self.MAX_LOG_ENTRIES
        if overflow > 0:
            del self.logs[:overflow]
            self.logs_dropped += overflow

    def record_tool_call(self, call: ToolCall) -> None:
        """Append a tool invocation, pruning the oldest if the cap is exceeded."""
        self.trajectory.append(call)
        overflow = len(self.trajectory) - self.MAX_TRAJECTORY_ENTRIES
        if overflow > 0:
            del self.trajectory[:overflow]
            self.trajectory_dropped += overflow


class _GraphState(TypedDict):
    """Graph state wrapper for LangGraph compatibility."""

    agent_state: AgentState


__all__ = ["AgentState", "_GraphState"]
