"""
Lifecycle management package.

Provides protocols, mixins, and error definitions for managing
the lifecycle of agents and components in the framework.
"""

from .errors import (
    AgentError,
    BaseFrameworkError,
    FatalError,
    FrameworkErrorCode,
    LifecycleError,
    RecoverableError,
)
from .mixins import LifecycleMixin
from .protocols import AgentHooks, AgentLifecycle, AgentState, HealthStatus

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
