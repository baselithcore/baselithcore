"""
Core Chat Module.

Provides domain-agnostic chat state and utilities.
Migrated from app/chat/ for white-label compatibility.
"""

from core.chat.agent_state import AgentState, _GraphState
from core.chat.guardrails import GuardrailDecision, evaluate_guardrails
from core.chat.prompt import CONVERSATION_SYSTEM_PROMPT, build_prompt
from core.chat.service import (
    ChatService,
    chat_service,
    get_chat_service,
    initialize_chat_service_with_plugins,
)

__all__ = [
    "CONVERSATION_SYSTEM_PROMPT",
    "AgentState",
    "ChatService",
    "GuardrailDecision",
    "_GraphState",
    "build_prompt",
    "chat_service",
    "evaluate_guardrails",
    "get_chat_service",
    "initialize_chat_service_with_plugins",
]
