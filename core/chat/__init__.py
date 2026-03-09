"""
Core Chat Module.

Provides domain-agnostic chat state and utilities.
Migrated from app/chat/ for white-label compatibility.
"""

from core.chat.agent_state import AgentState, _GraphState
from core.chat.guardrails import GuardrailDecision, evaluate_guardrails
from core.chat.prompt import build_prompt, CONVERSATION_SYSTEM_PROMPT
from core.chat.service import (
    ChatService,
    chat_service,
    initialize_chat_service_with_plugins,
)

__all__ = [
    "AgentState",
    "_GraphState",
    "GuardrailDecision",
    "evaluate_guardrails",
    "build_prompt",
    "CONVERSATION_SYSTEM_PROMPT",
    "ChatService",
    "chat_service",
    "initialize_chat_service_with_plugins",
]
