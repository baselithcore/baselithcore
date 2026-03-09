"""
Lifecycle management package.

Provides protocols, mixins, and error definitions for managing
the lifecycle of agents and components in the framework.
"""

from .protocols import AgentLifecycle, AgentHooks, AgentState, HealthStatus
from .errors import (
    FrameworkErrorCode,
    BaseFrameworkError,
    LifecycleError,
    AgentError,
    RecoverableError,
    FatalError,
)
from .mixins import LifecycleMixin

__all__ = [
    # Protocols & Data Structures
    "AgentLifecycle",
    "AgentHooks",
    "AgentState",
    "HealthStatus",
    # Mixins
    "LifecycleMixin",
    # Errors
    "FrameworkErrorCode",
    "BaseFrameworkError",
    "LifecycleError",
    "AgentError",
    "RecoverableError",
    "FatalError",
]
