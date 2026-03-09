"""
Reusable mixins for lifecycle implementation.

Provides default implementations for the AgentLifecycle protocol
to ease adoption and ensure consistency.
"""

import asyncio
from core.observability.logging import get_logger
from typing import Any, Dict, Optional

from .protocols import AgentHooks, AgentState, HealthStatus
from .errors import LifecycleError, FrameworkErrorCode

logger = get_logger(__name__)


class LifecycleMixin:
    """
    Default implementation of AgentLifecycle protocol.

    Usage:
        class MyAgent(LifecycleMixin):
            async def _do_startup(self):
                # Custom startup logic
                pass
    """

    def __init__(self, *args, **kwargs):
        self._state: AgentState = AgentState.UNINITIALIZED
        self._hooks: AgentHooks = AgentHooks()
        self._shutdown_event = asyncio.Event()
        # Ensure we don't break MRO if used with other classes
        super().__init__(*args, **kwargs)

    @property
    def state(self) -> AgentState:
        return self._state

    @property
    def hooks(self) -> AgentHooks:
        return self._hooks

    async def startup(self) -> None:
        """
        Template method for startup sequence.
        1. Check state
        2. Transition to STARTING
        3. Call _do_startup (custom logic)
        4. Transition to READY
        """
        if self._state not in (
            AgentState.UNINITIALIZED,
            AgentState.STOPPED,
            AgentState.ERROR,
        ):
            logger.warning(f"Agent startup called while in state {self._state}")
            return

        logger.info(f"Starting agent {self.__class__.__name__}...")
        await self._transition_state(AgentState.STARTING)

        try:
            await self._do_startup()
            await self._transition_state(AgentState.READY)
            logger.info(f"Agent {self.__class__.__name__} is READY")
        except Exception as e:
            logger.exception(f"Agent startup failed: {e}")
            await self._transition_state(AgentState.ERROR)
            raise LifecycleError(
                f"Failed to start agent: {e}",
                code=FrameworkErrorCode.LIFECYCLE_START_FAILED,
                context={"original_error": str(e)},
            ) from e

    async def shutdown(self) -> None:
        """
        Template method for shutdown sequence.
        1. Transition to STOPPING
        2. Call _do_shutdown (custom logic)
        3. Transition to STOPPED
        """
        if self._state == AgentState.STOPPED:
            return

        logger.info(f"Shutting down agent {self.__class__.__name__}...")
        await self._transition_state(AgentState.STOPPING)

        try:
            await self._do_shutdown()
        except Exception as e:
            logger.error(f"Error during shutdown: {e}")
            # Continue shutdown anyway
        finally:
            await self._transition_state(AgentState.STOPPED)
            self._shutdown_event.set()
            logger.info(f"Agent {self.__class__.__name__} STOPPED")

    async def health_check(self) -> HealthStatus:
        """Default health check implementation."""
        is_healthy = self._state in (
            AgentState.READY,
            AgentState.RUNNING,
            AgentState.PAUSED,
        )

        details = {}
        error_msg = None

        # Allow custom health checks
        try:
            custom_status = await self._do_health_check()
            if custom_status:
                details.update(custom_status)
        except Exception as e:
            is_healthy = False
            error_msg = str(e)
            details["check_error"] = str(e)

        if self._state == AgentState.ERROR:
            is_healthy = False
            error_msg = "Agent is in ERROR state"

        return HealthStatus(
            is_healthy=is_healthy, status=self._state, details=details, error=error_msg
        )

    async def reset(self) -> None:
        """Reset agent state."""
        logger.info(f"Resetting agent {self.__class__.__name__}...")
        await self._do_reset()

    async def pause(self) -> None:
        if self._state == AgentState.RUNNING:
            await self._transition_state(AgentState.PAUSED)

    async def resume(self) -> None:
        if self._state == AgentState.PAUSED:
            await self._transition_state(AgentState.RUNNING)

    # --- Extension Points (Override these) ---

    async def _do_startup(self) -> None:
        """Override to implement custom startup logic."""
        pass

    async def _do_shutdown(self) -> None:
        """Override to implement custom shutdown logic."""
        pass

    async def _do_reset(self) -> None:
        """Override to implement custom reset logic."""
        pass

    async def _do_health_check(self) -> Optional[Dict[str, Any]]:
        """Override to add custom health check details."""
        return None

    # --- Internal Helpers ---

    async def _transition_state(self, new_state: AgentState) -> None:
        """Handle state transitions and trigger hooks."""
        old_state = self._state
        self._state = new_state

        # Notify hooks
        for hook in self._hooks.on_state_change:
            try:
                if asyncio.iscoroutinefunction(hook):
                    await hook(old_state, new_state)
                else:
                    hook(old_state, new_state)
            except Exception as e:
                logger.error(f"Error in state change hook: {e}")
