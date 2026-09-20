"""The reference agent: every framework convention, in one runnable file.

Seven properties distinguish an agent this framework can run in production
from one that merely works on a laptop. Each is demonstrated below and named
where it appears:

1. **Async by default** — every I/O boundary is awaited, and blocking work is
   moved off the event loop rather than run on it.
2. **Dependency injection** — collaborators are resolved from the container at
   startup, never constructed inline, so tests can substitute them.
3. **Lifecycle sovereignty** — ``UNINITIALIZED -> STARTING -> READY`` is
   explicit, and work is refused outside ``READY`` instead of half-running.
4. **Multi-tenancy** — execution happens inside a tenant context, so every
   downstream read and write is scoped without passing a tenant id by hand.
5. **Typing and protocols** — the agent is typed against
   :class:`~core.interfaces.LLMServiceProtocol`, not a concrete service.
6. **Structured errors** — failures carry a
   :class:`~core.lifecycle.FrameworkErrorCode`, so a caller can branch on the
   cause instead of matching on message text.
7. **No domain logic in core** — the summarising behaviour lives here, in the
   example. ``core/`` stays domain-agnostic; anything domain-specific belongs
   in a plugin.

Run it:

    python -m examples.baselith_standard_example

No configuration, API key or running service is needed: with nothing
registered in the container, step 2 falls back to a stub LLM so the lifecycle
and error paths stay observable on their own.
"""

from __future__ import annotations

import asyncio
from typing import Any

from core.context import reset_tenant_context, set_tenant_context
from core.di import DependencyContainer, ServiceNotFoundError
from core.interfaces import LLMServiceProtocol
from core.lifecycle import AgentError, AgentState, FrameworkErrorCode, LifecycleMixin
from core.observability.logging import get_logger
from core.orchestration.protocols import AgentProtocol

# The framework configures structlog on startup. An example that calls
# logging.basicConfig() here would override that for anything importing it.
logger = get_logger("reference-agent")

TENANT = "reference-demo-tenant"


class _StubLLM:
    """Stand-in for an unregistered LLM service, so the demo runs offline."""

    async def generate(self, prompt: str) -> str:
        """Echo a bounded slice of the prompt back."""
        return f"PROCESSED[{prompt[:20]}...]"


class ReferenceAgent(LifecycleMixin, AgentProtocol):
    """Summarises text through an injected LLM service.

    Args:
        agent_id: Identifier carried into logs and errors raised by this agent.
    """

    def __init__(self, agent_id: str) -> None:
        super().__init__()
        self.agent_id = agent_id
        self.llm: LLMServiceProtocol | _StubLLM | None = None
        self.hooks.before_execute.append(self._log_execution_start)

    async def _do_startup(self) -> None:
        """Resolve collaborators (property 2) while awaiting I/O (property 1)."""
        logger.info("agent.starting", agent_id=self.agent_id)

        # A real deployment resolves the container once at boot and passes it
        # in; constructing one here keeps the example to a single file.
        container = DependencyContainer()
        try:
            self.llm = container.resolve(LLMServiceProtocol)
        except ServiceNotFoundError:
            # Narrow on purpose. "Nothing registered" is the expected offline
            # case and is handled; anything else is a real wiring fault and
            # must surface rather than be swallowed into a stub.
            logger.warning("agent.llm_unregistered", agent_id=self.agent_id)
            self.llm = _StubLLM()

        await asyncio.sleep(0.2)  # stands in for real async resource loading
        logger.info("agent.ready", agent_id=self.agent_id)

    async def _do_shutdown(self) -> None:
        """Release what startup acquired, in the same explicit way."""
        logger.info("agent.stopping", agent_id=self.agent_id)
        await asyncio.sleep(0.1)

    async def execute(self, prompt: str, context: dict[str, Any] | None = None) -> str:
        """Summarise ``prompt``.

        Args:
            prompt: Text to summarise.
            context: Optional execution metadata, unused by this agent.

        Returns:
            The summary produced by the LLM service.

        Raises:
            AgentError: If the agent is not ``READY`` (``AGENT_NOT_READY``),
                the prompt is empty (``AGENT_CONTEXT_INVALID``), or the
                service call fails (``AGENT_EXECUTION_FAILED``).
        """
        # The mixin owns startup and shutdown; execute-time hooks are the
        # agent's to fire, at the point they actually mean something.
        for hook in self.hooks.before_execute:
            hook(prompt, context or {})

        # Property 3: refuse work outside READY rather than half-running it.
        if self.state != AgentState.READY:
            raise AgentError(
                f"Agent {self.agent_id} is in state {self.state}, not READY.",
                code=FrameworkErrorCode.AGENT_NOT_READY,
            )

        # Property 6: validate before the expensive call, with a code a caller
        # can branch on.
        if not prompt:
            raise AgentError(
                "Empty prompt received",
                code=FrameworkErrorCode.AGENT_CONTEXT_INVALID,
            )

        # Property 4: everything downstream is scoped to this tenant. A real
        # orchestrator enters the context; an entrypoint like this one does it
        # itself. The token restores whatever was bound before, which is what
        # makes nesting safe — hence try/finally rather than a bare reset.
        token = set_tenant_context(TENANT)
        try:
            logger.info("agent.executing", agent_id=self.agent_id, chars=len(prompt))
            assert self.llm is not None  # guaranteed by _do_startup
            try:
                return await self.llm.generate(f"Summarize this: {prompt}")
            except AgentError:
                raise  # already structured; re-wrapping would lose the code
            except Exception as exc:
                raise AgentError(
                    f"Unexpected execution failure: {exc}",
                    code=FrameworkErrorCode.AGENT_EXECUTION_FAILED,
                ) from exc
        finally:
            reset_tenant_context(token)

    def _log_execution_start(self, prompt: Any, context: dict[str, Any]) -> None:
        """Observability hook, called by the lifecycle mixin before execute."""
        logger.info("agent.hook.before_execute", agent_id=self.agent_id)


async def run_demo() -> None:
    """Drive the agent through its full lifecycle, including a refusal."""
    print("\n--- BASELITHCORE REFERENCE AGENT ---\n")

    agent = ReferenceAgent(agent_id="reference-001")
    print(f"State: {agent.state}")

    # Property 3: executing before startup is an error, not undefined
    # behaviour. Shown first, because it is the part most examples omit.
    try:
        await agent.execute("too early")
    except AgentError as exc:
        print(f"Refused before startup, as designed: {exc.code}")

    await agent.startup()
    print(f"State: {agent.state}")

    response = await agent.execute("BaselithCore orchestrates agentic systems.")
    print(f"\nResponse: {response}")

    try:
        await agent.execute("")
    except AgentError as exc:
        print(f"Refused empty prompt: {exc.code}")

    await agent.shutdown()
    print(f"State: {agent.state}")

    print("\n--- DONE ---\n")


if __name__ == "__main__":
    asyncio.run(run_demo())
