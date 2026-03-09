"""
Reference Agent Implementation: FAQ Agent.

This agent demonstrates the full lifecycle contract:
1. Inherits from LifecycleMixin
2. Implements _do_startup/_do_shutdown
3. Registers custom hooks
4. Uses standard Error Code semantics
"""

from core.observability.logging import get_logger
from typing import Any, Dict, Optional

from core.lifecycle import (
    AgentLifecycle,
    LifecycleMixin,
    AgentState,
    FrameworkErrorCode,
    AgentError,
    RecoverableError
)
from core.orchestration.protocols import AgentProtocol

logger = get_logger(__name__)


class FAQAgent(LifecycleMixin, AgentProtocol):
    """
    A minimal agent that answers FAQs.
    Used to demonstrate framework architecture patterns.
    """

    def __init__(self, knowledge_base: Dict[str, str]):
        super().__init__()
        self.kb = knowledge_base
        
        # Register hooks
        self.hooks.before_execute.append(self._validate_input)
        self.hooks.on_error.append(self._handle_error)

    async def _do_startup(self) -> None:
        """Simulate resource loading."""
        logger.info("[FAQAgent] Loading knowledge base...")
        if not self.kb:
            raise AgentError(
                "Knowledge base cannot be empty",
                code=FrameworkErrorCode.LIFECYCLE_START_FAILED
            )
        # Simulate async initialization
        import asyncio
        await asyncio.sleep(0.1)
        logger.info(f"[FAQAgent] Loaded {len(self.kb)} QA pairs.")

    async def _do_shutdown(self) -> None:
        """Simulate cleanup."""
        logger.info("[FAQAgent] Saving stats before shutdown...")
        # Simulate async cleanup
        import asyncio
        await asyncio.sleep(0.1)

    async def execute(self, input: str, context: Optional[Dict[str, Any]] = None) -> str:
        """
        Main execution logic.
        
        Args:
            input: User query string
            context: Execution context
            
        Returns:
            Answer string
        """
        if self.state != AgentState.READY:
            raise AgentError(
                f"Agent not ready (state={self.state})",
                code=FrameworkErrorCode.AGENT_NOT_READY
            )
            
        # Hook trigger: before_execute is called automatically if managed by orchestrator,
        # but here we call it manually for demonstration if running standalone.
        # In a real orchestrator, this would be wrapped.
        
        query = input.lower().strip()
        
        # Artificial delay to demonstrate async
        import asyncio
        await asyncio.sleep(0.05)
        
        if query in self.kb:
            return self.kb[query]
            
        # Fallback / "Unknown" response
        return "I don't have an answer for that yet."

    # --- Hook Implementations ---

    def _validate_input(self, input: Any, context: Dict[str, Any]) -> None:
        """Hook to validate input before execution."""
        if not isinstance(input, str):
            raise AgentError(
                "Input must be a string",
                code=FrameworkErrorCode.AGENT_CONTEXT_INVALID
            )
        if len(input) > 100:
             logger.warning("[Hook] Input too long, might be truncated")

    def _handle_error(self, error: Exception, context: Dict[str, Any]) -> None:
        """Hook to log errors."""
        logger.error(f"[Hook] Captured error: {error}")
