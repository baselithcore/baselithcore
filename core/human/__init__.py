"""
Human-in-the-Loop Module.

Provides mechanisms for agents to request human intervention, approval,
or clarification during execution.
"""

from .interaction import (
    HumanIntervention,
    HumanRequest,
    InteractionType,
    InteractionStatus,
)

__all__ = [
    "HumanIntervention",
    "HumanRequest",
    "InteractionType",
    "InteractionStatus",
]
