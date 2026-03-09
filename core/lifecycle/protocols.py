"""
Formal Component Lifecycle Definitions.

Codifies the operational contracts for framework components.
Specifies standard execution states (Starting, Ready, Running) and
protocol-based interfaces for startup, shutdown, health monitoring,
and state transitions.
"""

from __future__ import annotations

from abc import abstractmethod
from enum import Enum
from typing import Any, Callable, Dict, List, Optional, Protocol, runtime_checkable

from pydantic import BaseModel, Field


class AgentState(str, Enum):
    """Standard execution states for an agent."""

    UNINITIALIZED = "uninitialized"
    STARTING = "starting"
    READY = "ready"
    RUNNING = "running"
    PAUSED = "paused"
    STOPPING = "stopping"
    STOPPED = "stopped"
    ERROR = "error"
    RECOVERING = "recovering"


class HealthStatus(BaseModel):
    """Standard format for health check results."""

    is_healthy: bool = Field(..., description="Whether the component is healthy")
    status: AgentState = Field(..., description="Current agent state")
    details: Dict[str, Any] = Field(
        default_factory=dict, description="Additional health details"
    )
    error: Optional[str] = Field(None, description="Error message if unhealthy")


class AgentHooks(BaseModel):
    """
    Standard hook points for agent lifecycle.

    Hooks allow injecting custom logic at specific points in the agent's
    execution lifecycle without modifying the core logic.
    """

    before_execute: List[Callable[[Any, Dict[str, Any]], Any]] = Field(
        default_factory=list
    )
    after_execute: List[Callable[[Any, Any, Dict[str, Any]], Any]] = Field(
        default_factory=list
    )
    on_error: List[Callable[[Exception, Dict[str, Any]], Any]] = Field(
        default_factory=list
    )
    on_state_change: List[Callable[[AgentState, AgentState], Any]] = Field(
        default_factory=list
    )


@runtime_checkable
class AgentLifecycle(Protocol):
    """
    Operational contract for agent-like entities.

    Enforces a standardized lifecycle for all agents, ensuring they can be
    consistently managed by the central orchestrator. Includes mandatory
    hooks for resource acquisition (startup), cleanup (shutdown), and
    vital sign monitoring (health_check).
    """

    @property
    @abstractmethod
    def state(self) -> AgentState:
        """Current state of the agent."""
        ...

    @property
    @abstractmethod
    def hooks(self) -> AgentHooks:
        """Access to lifecycle hooks."""
        ...

    @abstractmethod
    async def startup(self) -> None:
        """
        Initialize resources and prepare for execution.

        Should raise FatalError if initialization fails.
        Should transition state to READY.
        """
        ...

    @abstractmethod
    async def shutdown(self) -> None:
        """
        Cleanup resources and stop execution.

        Should be idempotent.
        Should transition state to STOPPED.
        """
        ...

    @abstractmethod
    async def health_check(self) -> HealthStatus:
        """
        Check vital signs of the agent.

        Returns:
            HealthStatus indicating if agent is capable of handling requests.
        """
        ...

    @abstractmethod
    async def reset(self) -> None:
        """
        Reset agent internal state (memory, cache, etc).

        Does not imply full restart, just clearing conversation context/state.
        """
        ...

    @abstractmethod
    async def pause(self) -> None:
        """Temporarily pause execution (e.g. for maintenance or backpressure)."""
        ...

    @abstractmethod
    async def resume(self) -> None:
        """Resume execution from paused state."""
        ...
