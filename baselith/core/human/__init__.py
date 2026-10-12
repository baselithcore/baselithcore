"""
Human-in-the-Loop Module.

Provides mechanisms for agents to request human intervention, approval,
or clarification during execution.
"""

from .executor import get_hitl_executor, shutdown_hitl_executor
from .interaction import (
    DEFAULT_MAX_RECENT_REQUESTS,
    HumanIntervention,
    HumanRequest,
    InteractionStatus,
    InteractionType,
)

__all__ = [
    "DEFAULT_MAX_RECENT_REQUESTS",
    "HumanIntervention",
    "HumanRequest",
    "InteractionStatus",
    "InteractionType",
    "get_hitl_executor",
    "shutdown_hitl_executor",
]
