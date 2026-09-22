"""One model round-trip for a message history, whatever the service supports.

Two agent loops need the same thing — send a conversation, get an
:class:`~core.services.llm.tool_calling.LLMResult` back — and for a while only
one of them had it. The typed :class:`~core.agent.agent.Agent` spoke the
message API; the ReAct loop the orchestrator actually runs rebuilt a flat
string prompt every turn, which threw away exactly what
:mod:`core.services.llm.messages` documents as the cost of doing so: the
``tool_use_id`` correlation, the ``is_error`` flag, the verbatim assistant turn
with its thinking blocks, and any hope of a prompt-cache hit, since a rebuilt
prefix is a new prefix.

This is that round-trip, once, so the two loops cannot drift apart again and a
third one has somewhere to start.
"""

from __future__ import annotations

from typing import Any, cast

from core.services.llm.messages import (
    CONVERGENCE_NUDGE,
    Message,
    ToolResultBlock,
    render_as_prompt,
)
from core.services.llm.tool_calling import LLMResult, LLMToolSpec, ResponseFormat

__all__ = ["generate_over_messages", "service_supports_messages"]


def service_supports_messages(service: Any) -> bool:
    """Whether ``service`` can be handed a message list.

    The identity check against ``True`` is deliberate: ``getattr`` on a
    ``Mock`` answers with a truthy ``Mock``, and a double that cannot serve a
    message list must not be handed one.

    Args:
        service: An ``LLMService``, or whatever a caller injected.

    Returns:
        True when the message API is available on this service.
    """
    send = getattr(service, "generate_messages", None)
    return getattr(service, "supports_messages", False) is True and callable(send)


async def generate_over_messages(
    service: Any,
    history: list[Message],
    *,
    specs: list[LLMToolSpec] | None = None,
    response_format: ResponseFormat | None = None,
    system: str | None = None,
    model: str | None = None,
    task_category: str | None = None,
) -> LLMResult:
    """Send the conversation so far and return the model's reply.

    The history is passed as a *copy*: a loop appends to its own list after
    every turn, and handing the live object to the service would let a later
    append rewrite what an earlier call was given — and make every traced
    request look identical.

    A service that does not advertise ``supports_messages`` — an injected
    double, or one built before the message API — is called through the legacy
    ``generate(prompt=...)`` path with the history rendered as a transcript,
    plus the convergence nudge a flattened conversation needs: it has no
    ``tool_result`` block to say the work came back, so without the
    instruction it re-requests calls it was already answered. It loses the
    structure, not the conversation.

    Args:
        service: The LLM service.
        history: The conversation, oldest first.
        specs: Tool definitions offered to the model.
        response_format: Structured-output schema, when one is wanted.
        system: System prompt.
        model: Model override, or ``None`` for the deployment default.
        task_category: Cost-aware routing hint.

    Returns:
        The model's reply.
    """
    if service_supports_messages(service):
        return cast(
            "LLMResult",
            await service.generate_messages(
                list(history),
                model=model,
                tools=specs,
                response_format=response_format,
                system=system,
                task_category=task_category,
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
            model=model,
            tools=specs,
            response_format=response_format,
            system_prompt=system,
            task_category=task_category,
        ),
    )
