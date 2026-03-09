"""
Baselith-Core Gold Standard Example.

This script demonstrates the "Perfect Baselith Agent" implementation:
1. Async By Default
2. Dependency Injection First
3. Lifecycle Sovereignty (UNINITIALIZED -> STARTING -> READY)
4. Multi-Tenancy by Default
5. Strong Typing & Protocols
6. Structured Error Semantics
7. No Domain Logic in Core (implements specific logic here)

Usage:
    python -m examples.baselith_standard_example
"""

import asyncio
import logging
from core.observability.logging import get_logger
from typing import Any, Dict, Optional

# Core Imports
from core.lifecycle import LifecycleMixin, AgentState, AgentError, FrameworkErrorCode
from core.orchestration.protocols import AgentProtocol
from core.di import DependencyContainer
from core.interfaces import LLMServiceProtocol
from core.context import tenant_context

# Configure Logging
logging.basicConfig(level=logging.INFO)
logger = get_logger("GoldStandardDemo")

# ============================================================================
# 1. Agent Implementation
# ============================================================================

class GoldStandardAgent(LifecycleMixin, AgentProtocol):
    """
    An agent that follows every framework dogma.
    It summarizes input using an injected LLM service.
    """

    def __init__(self, agent_id: str):
        super().__init__()
        self.agent_id = agent_id
        self.llm: Optional[LLMServiceProtocol] = None
        
        # Register hooks for observability
        self.hooks.before_execute.append(self._log_execution_start)

    async def _do_startup(self) -> None:
        """
        Dogma II: DI First & Dogma III: Async Everything.
        We resolve dependencies during startup phase.
        """
        logger.info(f"🚀 [{self.agent_id}] Starting up...")
        
        # In a real app, the container would be globally initialized
        container = DependencyContainer()
        
        try:
            # Dogma IV: Strong Contracts (Protocols)
            self.llm = container.resolve(LLMServiceProtocol)
        except Exception:
            # Fallback for demo if no real service is registered
            logger.warning("No LLMServiceProtocol found, using mock for demo.")
            self.llm = self._create_mock_llm()

        # Simulate async resource loading
        await asyncio.sleep(0.2)
        logger.info(f"✅ [{self.agent_id}] Ready for execution.")

    async def _do_shutdown(self) -> None:
        """Lifecycle clean up."""
        logger.info(f"🛑 [{self.agent_id}] Shutting down...")
        await asyncio.sleep(0.1)

    async def execute(self, input: str, context: Optional[Dict[str, Any]] = None) -> str:
        """
        Dogma V: Lifecycle Sovereignty.
        Dogma VII: Multi-Tenancy.
        """
        if self.state != AgentState.READY:
            raise AgentError(
                f"Agent {self.agent_id} is in state {self.state}, not READY.",
                code=FrameworkErrorCode.AGENT_NOT_READY
            )

        # Dogma VII: Ensure we are in a tenant context
        # In a real system, the orchestrator handles this
        async with tenant_context("standard-demo-tenant"):
            logger.info(f"🧠 [{self.agent_id}] Processing input: '{input[:30]}...'")
            
            try:
                # Dogma VI: Structured Errors
                if not input:
                    raise AgentError(
                        "Empty input received",
                        code=FrameworkErrorCode.AGENT_CONTEXT_INVALID
                    )
                
                # simulate work
                result = await self.llm.generate(f"Summarize this: {input}")
                return f"Result: {result}"
                
            except Exception as e:
                # Handle or wrap errors following Dogma VI
                if isinstance(e, AgentError):
                    raise
                raise AgentError(
                    f"Unexpected execution failure: {str(e)}",
                    code=FrameworkErrorCode.AGENT_EXECUTION_FAILED
                ) from e

    # --- Internal Helpers ---

    def _log_execution_start(self, input: Any, context: Dict[str, Any]) -> None:
        """Standard hook implementation."""
        logger.info(f"🔔 [Hook] Starting execution of {self.agent_id}")

    def _create_mock_llm(self) -> Any:
        """Demo helper."""
        class MockLLM:
            async def generate(self, prompt: str) -> str:
                return f"PROCESSED[{prompt[:20]}...]"
        return MockLLM()

# ============================================================================
# 2. Execution Demo
# ============================================================================

async def run_demo():
    print("\n--- BASELITH-CORE GOLD STANDARD DEMO ---\n")
    
    # 1. Instantiate
    agent = GoldStandardAgent(agent_id="Agent-007")
    print(f"Current State: {agent.state}")
    
    # 2. Startup
    await agent.initialize()
    print(f"Current State: {agent.state}")
    
    # 3. Execute with Multi-Tenancy Context
    try:
        response = await agent.execute("BaselithCore is the future of AI infrastructure.")
        print(f"\nResponse: {response}")
    except AgentError as e:
        print(f"Caught expected error: {e}")
        
    # 4. Shutdown
    await agent.shutdown()
    print(f"Current State: {agent.state}")
    
    print("\n--- DEMO COMPLETE ---\n")

if __name__ == "__main__":
    asyncio.run(run_demo())
