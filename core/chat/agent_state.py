"""
Agent State Model.

Defines the shared state between chat agent steps.
Migrated from app/chat/agent_state.py
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence, Tuple, TypedDict

from core.chat.guardrails import GuardrailDecision


@dataclass
class AgentState:
    """Shared state between chat agent steps."""

    request: Any  # ChatRequest - avoid circular import
    user_query: str = ""
    rerank_query: str = ""
    normalized_query: str = ""
    conversation_id: Optional[str] = None
    history_turns: Sequence[Any] = ()
    history_text: str = ""
    query_vector: Optional[List[float]] = None
    hits: Sequence[Any] = ()
    ranked_hits: Sequence[Any] = ()
    context: str = ""
    doc_sources: List[Dict[str, Any]] = field(default_factory=list)
    source_metrics: Dict[str, Any] = field(default_factory=dict)
    cache_key: Optional[Tuple[str, str]] = None
    answer: Optional[Any] = None
    done: bool = False
    next_action: str = "validate_input"
    logs: List[str] = field(default_factory=list)
    guardrail_decision: Optional[GuardrailDecision] = None
    clarification_reason: Optional[str] = None
    # Generic plugin data storage - plugins can store their data here
    # Example: plugin_data["issue_tracker"] = {"project_plan": ..., "issues": [...]}
    plugin_data: Dict[str, Any] = field(default_factory=dict)
    rag_only: bool = False

    def log(self, message: str) -> None:
        """Append log message."""
        self.logs.append(message)


class _GraphState(TypedDict):
    """Graph state wrapper for LangGraph compatibility."""

    agent_state: AgentState


__all__ = ["AgentState", "_GraphState"]
