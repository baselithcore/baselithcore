"""The typed agent's tool loop, as an async generator of events.

:meth:`Agent.run` used to own this loop inline. It lives here so the same
loop can be *awaited* (``run``: wait for :class:`Completed`) or *observed*
(``run_events``: relay every event) without a second implementation that
could drift. Behaviour is unchanged: the loop still raises — only
``run_events`` turns an exception into a :class:`Failed` event.
"""

from __future__ import annotations

from collections.abc import AsyncGenerator, Sequence
from typing import TYPE_CHECKING, Any

from pydantic import ValidationError

from core.agent._tool_dispatch import gate_context, system_prompt_for
from core.agent._tool_runtime import execute_tool_calls
from core.agent.events import (
    AgentEvent,
    Completed,
    TextDelta,
    ToolCallFinished,
    ToolCallStarted,
)
from core.orchestration.call_keys import CallOccurrences
from core.services.llm.messages import Message, ToolResultBlock, message_from_result

if TYPE_CHECKING:  # pragma: no cover - typing only
    from core.agent.agent import Agent
    from core.orchestration.checkpoint import CheckpointManager


def validate_history(history: Sequence[Message] | None) -> list[Message]:
    """Copy a caller's prior turns, refusing one a provider would reject.

    A history that ends on an assistant ``tool_use`` with no answering
    ``tool_result`` is an invalid conversation for every provider: appending a
    new user prompt after it fails at the API, far from the caller's bug.

    Args:
        history: Prior turns, oldest first, or ``None``.

    Returns:
        A new list (the caller's list is never mutated).

    Raises:
        ValueError: The last message has unanswered ``tool_use`` blocks.
    """
    turns = list(history or ())
    if turns and turns[-1].role == "assistant" and turns[-1].tool_uses:
        raise ValueError(
            "history ends with an assistant tool_use that has no tool_result; "
            "pass the history up to the last completed turn"
        )
    return turns


async def drive(
    agent: Agent[Any],
    prompt: str,
    *,
    history: Sequence[Message] | None,
    run_id: str | None,
    checkpoint: CheckpointManager | None,
) -> AsyncGenerator[AgentEvent, None]:
    """Run the tool loop, yielding events; the last one is :class:`Completed`.

    Raises exactly what :meth:`Agent.run` documents (validation, iteration
    cap, budget, approval-pending). Never yields
    :class:`~core.agent.events.Failed`.
    """
    from core.agent.agent import AgentOutputValidationError, AgentResult
    from core.orchestration.enforcement import enforce_iteration

    turns = validate_history(history)
    service = agent._service()
    specs = agent._tool_specs()
    response_format = agent._response_format()
    system = system_prompt_for(agent.system_prompt, bool(agent._tools))
    context = gate_context(agent)

    if checkpoint is not None and not run_id:
        run_id = checkpoint.run_id
    occurrences = CallOccurrences()
    messages: list[Message] = [*turns, Message.user(prompt)]
    tool_calls_made: list[str] = []
    retries_left = agent.max_retries
    last_error: Exception | None = None
    plain_text = agent.output_type is None

    for iteration in range(1, agent.max_iterations + 1):
        # Charges the ambient request budget for this round trip (a no-op
        # outside an orchestrated request); raises when the cap is hit.
        enforce_iteration(context)
        result = await agent._generate(
            service,
            messages,
            specs=specs,
            response_format=response_format,
            system=system,
        )
        # Verbatim, before anything else: the API requires the turn that
        # requested the tools — thinking blocks included — to come back
        # unchanged alongside their results.
        messages.append(message_from_result(result))
        if plain_text and result.text:
            yield TextDelta(text=result.text)

        if result.tool_calls:
            calls = list(result.tool_calls)
            for call in calls:
                yield ToolCallStarted(
                    call_id=call.id,
                    name=call.name,
                    arguments=dict(call.arguments or {}),
                )
            # Gated in order, then overlapped: the provider emitted every
            # call of this turn before seeing any result, so they are
            # independent and running them serially paid the sum of their
            # latencies. Each call's occurrence in the run keeps a loop
            # that legitimately calls one tool twice with identical
            # arguments from collapsing into one ledger entry.
            outcomes = await execute_tool_calls(
                agent,
                calls,
                context=context,
                run_id=run_id,
                step_offset=len(tool_calls_made),
                occurrences=occurrences,
                checkpoint=checkpoint,
            )
            results: list[ToolResultBlock] = []
            for call, (observation, is_error) in zip(calls, outcomes, strict=True):
                tool_calls_made.append(call.name)
                results.append(
                    ToolResultBlock(
                        tool_use_id=call.id,
                        content=observation,
                        is_error=is_error,
                    )
                )
                yield ToolCallFinished(
                    call_id=call.id,
                    name=call.name,
                    content=observation,
                    is_error=is_error,
                )
            # One message for the whole turn: a provider rejects a
            # conversation whose parallel tool calls are answered apart.
            messages.append(Message.tool_results(results))
            continue

        text = result.text or ""
        if plain_text:
            yield Completed(
                result=AgentResult(
                    output=text,
                    text=text,
                    tool_calls_made=tool_calls_made,
                    iterations=iteration,
                    messages=messages,
                )
            )
            return
        try:
            parsed = agent._parse_output(text)
        except (ValidationError, ValueError) as exc:
            last_error = exc
            if retries_left <= 0:
                assert agent.output_type is not None
                raise AgentOutputValidationError(
                    f"output failed {agent.output_type.__name__} validation "
                    f"after {agent.max_retries} retries: {exc}",
                    last_error=exc,
                ) from exc
            retries_left -= 1
            # A correction turn, not a rewritten prompt: the failed answer
            # is already in the history as the assistant turn above.
            messages.append(
                Message.user(
                    f"That failed validation against the required schema: "
                    f"{exc}\nReply again with ONLY a JSON object matching "
                    f"the schema."
                )
            )
            continue
        yield Completed(
            result=AgentResult(
                output=parsed,
                text=text,
                tool_calls_made=tool_calls_made,
                iterations=iteration,
                messages=messages,
            )
        )
        return

    raise RuntimeError(
        f"Agent.run exceeded max_iterations={agent.max_iterations} "
        f"(last validation error: {last_error})"
    )


__all__ = ["drive", "validate_history"]
