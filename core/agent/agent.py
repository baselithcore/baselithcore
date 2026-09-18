"""Typed developer-facing Agent API.

The one-import entry point for building agents on BaselithCore, in the style
popularized by typed-agent frameworks: declare the model, an optional Pydantic
``output_type``, plain-Python tools, and call :meth:`Agent.run`.

    from pydantic import BaseModel
    from core.agent import Agent

    class CityInfo(BaseModel):
        city: str
        population: int

    async def lookup_population(city: str) -> str:
        \"\"\"Look up a city's population.\"\"\"
        ...

    agent = Agent(output_type=CityInfo, tools=[lookup_population])
    result = await agent.run("Tell me about Rome")
    result.output  # -> CityInfo, validated (with automatic retry on failure)

Everything runs on the existing runtime: ``LLMService`` (provider abstraction,
caching, cost accounting, fallback chain, routing), the structured
tool-calling path, and the ambient ``LoopBudget`` — an ``Agent.run`` inside an
orchestrated request charges that request's budget like any other LLM call.

The loop keeps a **real message history** (see
:mod:`core.services.llm.messages`): the assistant turn is appended verbatim,
every tool result of a turn goes back in one user message as a
``tool_result`` block correlated by ``tool_use_id`` and flagged with
``is_error`` on failure, and the history is only ever appended to. It used to
stringify tool output into a regenerated user prompt, which lost the
correlation, lost the failure flag, lost any thinking block the model needed
replayed — and changed the prompt prefix on every iteration, so nothing could
be served from the provider's prompt cache. A service that predates the
message API still works: the history is flattened into a prompt for it.
"""

from __future__ import annotations

import inspect
import json
import re
from collections.abc import AsyncIterator, Callable, Sequence
from dataclasses import dataclass, field
from typing import Any, cast

from pydantic import BaseModel, ValidationError

from core.agent._tool_dispatch import (
    build_tool_specs,
    execute_tool,
    gate_context,
    system_prompt_for,
)
from core.observability.logging import get_logger
from core.orchestration.idempotency import (
    ToolLedger,
    derive_idempotency_key,
    requires_idempotency,
)
from core.reasoning.react import ToolDefinition
from core.services.llm.messages import (
    CONVERGENCE_NUDGE,
    Message,
    ToolResultBlock,
    message_from_result,
    render_as_prompt,
)
from core.services.llm.tool_calling import (
    LLMResult,
    LLMToolSpec,
    ResponseFormat,
    ToolCall,
)

logger = get_logger(__name__)


_FENCE_RE = re.compile(r"^```(?:json)?\s*|\s*```$", re.MULTILINE)


class AgentOutputValidationError(RuntimeError):
    """The model never produced output matching ``output_type``."""

    def __init__(self, message: str, last_error: Exception | None = None) -> None:
        super().__init__(message)
        self.last_error = last_error


@dataclass(frozen=True)
class AgentResult[OutputT]:
    """Outcome of one :meth:`Agent.run` call.

    Attributes:
        output: The validated ``output_type`` instance, or the plain response
            text when no ``output_type`` was declared.
        text: The final raw assistant text.
        tool_calls_made: Names of tools executed, in call order.
        iterations: LLM round-trips performed (tool loops + validation
            retries).
        messages: The conversation the loop actually sent, oldest first —
            user turns, assistant turns verbatim, and the ``tool_result``
            blocks that answered them. Useful for tracing, replay and
            evaluation; empty only for a run that never reached the model.
    """

    output: OutputT
    text: str
    tool_calls_made: list[str] = field(default_factory=list)
    iterations: int = 0
    messages: list[Message] = field(default_factory=list)


class Agent[OutputT]:
    """A typed agent: model + system prompt + tools + validated output.

    Args:
        model: Optional model override (deployment default when None).
        output_type: Optional Pydantic model the final answer must satisfy.
            Requested natively via the provider's structured-output API and
            validated locally; validation failures are fed back to the model
            and retried up to ``max_retries`` times.
        system_prompt: Optional system prompt.
        tools: Plain callables (sync or async — the JSON schema is inferred
            from type hints and the docstring) or explicit ``ToolDefinition``s.
        max_retries: Validation-failure retries for ``output_type``.
        max_iterations: Hard cap on LLM round-trips (tool loop + retries).
        task_category: Optional cost-aware routing hint (TaskCategory value).
        llm_service: Injected service (tests); defaults to the shared
            context-aware :func:`core.services.llm.get_llm_service`.
        autonomy_policy: Optional
            :class:`~core.orchestration.autonomy.AutonomyPolicy`. When set,
            tools whose category needs approval at the active autonomy level
            are gated through the enforcement chokepoint, and a run with no
            approval channel pauses with ``ApprovalPendingError``. Unlike
            :class:`~core.reasoning.react.ReActAgent` — which manufactures a
            SUPERVISED policy when given none — this defaults to ``None`` and
            leaves the approval gate inert: there is no ambient policy to
            inherit here, and defaulting to one would start demanding approval
            for every effectful tool of every existing typed agent, with no
            channel to approve on. Every other control at the chokepoint
            (contract, plugin capability, budget, rate limit, hooks, audit)
            applies either way.
        tool_ledger: Optional :class:`~core.orchestration.idempotency.ToolLedger`.
            When supplied *and* ``run`` is given a ``run_id``, every tool
            outside the ``read_only`` category is recorded before it executes
            and its result replayed instead of re-executed on a retry of the
            same run. A plain callable is ``destructive`` by default (see
            :class:`~core.reasoning.react.ToolDefinition`), so tools opt out of
            the ledger by declaring ``read_only``, never by omission.
    """

    def __init__(
        self,
        model: str | None = None,
        *,
        output_type: type[OutputT] | None = None,
        system_prompt: str | None = None,
        tools: Sequence[Callable[..., Any] | ToolDefinition] = (),
        max_retries: int = 2,
        max_iterations: int = 6,
        task_category: str | None = None,
        llm_service: Any | None = None,
        autonomy_policy: Any | None = None,
        tool_ledger: ToolLedger | None = None,
    ) -> None:
        self.model = model
        self.output_type = output_type
        self.system_prompt = system_prompt
        self.max_retries = max_retries
        self.max_iterations = max_iterations
        self.task_category = task_category
        self._llm_service = llm_service
        # Read back by ``_tool_dispatch.gate_context``, which also honours the
        # same attribute set directly on an instance by a host.
        self._autonomy_policy = autonomy_policy
        self._tool_ledger = tool_ledger
        self._tools: dict[str, ToolDefinition] = {}
        for tool in tools:
            definition = (
                tool
                if isinstance(tool, ToolDefinition)
                else ToolDefinition(
                    name=tool.__name__,
                    fn=tool,
                    description=inspect.getdoc(tool) or tool.__name__,
                )
            )
            self._tools[definition.name] = definition

    # -- internals ---------------------------------------------------------

    def _service(self) -> Any:
        if self._llm_service is not None:
            return self._llm_service
        from core.services.llm import get_llm_service

        return get_llm_service()

    def _tool_specs(self) -> list[LLMToolSpec] | None:
        """Tool definitions for the model, annotated with their category."""
        return build_tool_specs(self._tools)

    def _response_format(self) -> ResponseFormat | None:
        if self.output_type is None or not issubclass(self.output_type, BaseModel):
            return None
        return ResponseFormat(
            schema=self.output_type.model_json_schema(),
            name=self.output_type.__name__,
            strict=True,
        )

    async def _invoke(self, definition: ToolDefinition, call: ToolCall) -> Any:
        """Call the tool and return its raw value.

        Rendering it for the model (JSON-encoding, ``SkillResult`` unpacking,
        truncation, the injection scan and the untrusted envelope) happens at
        the single seam in :mod:`core.agent._tool_dispatch` — doing any of it
        here would make this a second one.
        """
        result = definition.fn(**(call.arguments or {}))
        if inspect.isawaitable(result):
            result = await result
        return result

    def _ledger_key(
        self, definition: ToolDefinition, call: ToolCall, run_id: str | None, step: int
    ) -> str | None:
        """The idempotency key for this call, or ``None`` when it needs none.

        Three conditions must hold: a ledger was supplied, the caller gave a
        stable ``run_id`` (without one there is nothing to deduplicate against
        — a fresh id per attempt is a different call by definition), and the
        tool is not ``read_only``.
        """
        if self._tool_ledger is None or not run_id:
            return None
        if not requires_idempotency(definition.category):
            return None
        return derive_idempotency_key(run_id, step, call.name, call.arguments)

    def _parse_output(self, text: str) -> OutputT:
        assert self.output_type is not None
        cleaned = _FENCE_RE.sub("", text.strip()).strip()
        model_cls = cast("type[BaseModel]", self.output_type)
        return cast(OutputT, model_cls.model_validate(json.loads(cleaned)))

    # -- public API --------------------------------------------------------

    async def run(
        self, prompt: str, *, run_id: str | None = None
    ) -> AgentResult[OutputT]:
        """Run the agent to completion and return the validated result.

        Drives the tool loop until the model answers without tool calls, then
        (when ``output_type`` is set) validates the answer, feeding validation
        errors back for up to ``max_retries`` correction rounds.

        The conversation is a message history, appended to and never rewritten:
        the assistant turn goes back verbatim, and every tool result of that
        turn returns in one user message as a ``tool_result`` block carrying
        the ``tool_use_id`` it answers and an ``is_error`` flag when the call
        failed.

        Args:
            prompt: The user prompt.
            run_id: Identifier for this run, shared by every attempt at it.
                **Supplying a stable id across retries is what makes
                deduplication possible at all** — a fresh id per attempt is a
                different run by definition, so the ledger has nothing to match
                and every effectful tool executes again. Ignored unless a
                ``tool_ledger`` was supplied.

        Raises:
            AgentOutputValidationError: ``output_type`` never satisfied.
            RuntimeError: ``max_iterations`` exhausted before a final answer.
            BudgetExceededError: An ambient ``LoopBudget`` cap was hit.
            ApprovalPendingError: A tool needs a human decision that is not
                available yet; the run pauses durably. Only reachable when the
                agent was given an ``autonomy_policy``; without one the
                approval gate is inert (see the constructor).
        """
        from core.orchestration.enforcement import enforce_iteration

        service = self._service()
        specs = self._tool_specs()
        response_format = self._response_format()
        system = system_prompt_for(self.system_prompt, bool(self._tools))
        context = gate_context(self)

        history: list[Message] = [Message.user(prompt)]
        tool_calls_made: list[str] = []
        retries_left = self.max_retries
        last_error: Exception | None = None

        for iteration in range(1, self.max_iterations + 1):
            # Charges the ambient request budget for this round trip (a no-op
            # outside an orchestrated request); raises when the cap is hit.
            enforce_iteration(context)
            result = await self._generate(
                service,
                history,
                specs=specs,
                response_format=response_format,
                system=system,
            )
            # Verbatim, before anything else: the API requires the turn that
            # requested the tools — thinking blocks included — to come back
            # unchanged alongside their results.
            history.append(message_from_result(result))

            if result.tool_calls:
                results: list[ToolResultBlock] = []
                for call in result.tool_calls:
                    observation, is_error = await execute_tool(
                        self,
                        call,
                        context=context,
                        run_id=run_id,
                        # The step is the call's position in the run, so a loop
                        # that legitimately calls one tool twice with identical
                        # arguments is not collapsed into one ledger entry.
                        step=len(tool_calls_made),
                    )
                    tool_calls_made.append(call.name)
                    results.append(
                        ToolResultBlock(
                            tool_use_id=call.id,
                            content=observation,
                            is_error=is_error,
                        )
                    )
                # One message for the whole turn: a provider rejects a
                # conversation whose parallel tool calls are answered apart.
                history.append(Message.tool_results(results))
                continue

            text = result.text or ""
            if self.output_type is None:
                return AgentResult(
                    output=text,  # type: ignore[arg-type]
                    text=text,
                    tool_calls_made=tool_calls_made,
                    iterations=iteration,
                    messages=history,
                )
            try:
                parsed = self._parse_output(text)
            except (ValidationError, ValueError) as exc:
                last_error = exc
                if retries_left <= 0:
                    raise AgentOutputValidationError(
                        f"output failed {self.output_type.__name__} validation "
                        f"after {self.max_retries} retries: {exc}",
                        last_error=exc,
                    ) from exc
                retries_left -= 1
                # A correction turn, not a rewritten prompt: the failed answer
                # is already in the history as the assistant turn above.
                history.append(
                    Message.user(
                        f"That failed validation against the required schema: "
                        f"{exc}\nReply again with ONLY a JSON object matching "
                        f"the schema."
                    )
                )
                continue
            return AgentResult(
                output=parsed,
                text=text,
                tool_calls_made=tool_calls_made,
                iterations=iteration,
                messages=history,
            )

        raise RuntimeError(
            f"Agent.run exceeded max_iterations={self.max_iterations} "
            f"(last validation error: {last_error})"
        )

    async def _generate(
        self,
        service: Any,
        history: list[Message],
        *,
        specs: list[LLMToolSpec] | None,
        response_format: ResponseFormat | None,
        system: str | None,
    ) -> LLMResult:
        """One model round-trip for the conversation so far.

        The history is passed as a *copy*: the loop appends to its own list
        after every turn, and handing the live object to the service would let
        a later append rewrite what an earlier call was given (and make every
        traced request look identical).

        A service that does not advertise ``supports_messages is True`` — an
        injected double, or one built before the message API — is called
        through the legacy ``generate(prompt=...)`` path with the history
        rendered as a transcript, plus the convergence nudge a flattened
        conversation needs (it has no ``tool_result`` block to say the work
        came back, so without the instruction it re-requests calls it was
        already answered). It loses the structure, not the conversation.

        The identity check is deliberate: ``getattr`` on a ``Mock`` answers
        with a truthy ``Mock``, and a double that cannot serve a message list
        must not be handed one.
        """
        send = getattr(service, "generate_messages", None)
        if getattr(service, "supports_messages", False) is True and callable(send):
            # ``service`` is intentionally untyped (an LLMService, or whatever
            # a caller injected), so the result is narrowed at this one seam.
            return cast(
                "LLMResult",
                await send(
                    list(history),
                    model=self.model,
                    tools=specs,
                    response_format=response_format,
                    system=system,
                    task_category=self.task_category,
                ),
            )
        transcript = render_as_prompt(history)
        if any(
            isinstance(block, ToolResultBlock)
            for message in history
            for block in message.content
        ):
            transcript = f"{transcript}\n\n{CONVERGENCE_NUDGE}"
        return cast(
            "LLMResult",
            await service.generate(
                transcript,
                model=self.model,
                tools=specs,
                response_format=response_format,
                system_prompt=system,
                task_category=self.task_category,
            ),
        )

    async def run_stream(self, prompt: str) -> AsyncIterator[str]:
        """Stream the plain-text response token by token.

        Streaming is text-only: combining it with ``output_type`` or tools is
        rejected (validated/structured answers need the complete response).
        """
        if self.output_type is not None:
            raise ValueError("run_stream does not support output_type")
        if self._tools:
            raise ValueError("run_stream does not support tools")
        service = self._service()
        async for chunk in service.generate_response_stream(
            prompt,
            model=self.model,
            system_prompt=self.system_prompt,
        ):
            yield chunk
