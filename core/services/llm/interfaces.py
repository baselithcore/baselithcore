"""
LLM Provider and Service interface definitions.
"""

from collections.abc import AsyncIterator
from typing import TYPE_CHECKING, Any, Protocol

if TYPE_CHECKING:
    from core.services.llm.messages import Message
    from core.services.llm.tool_calling import (
        LLMResult,
        LLMToolSpec,
        ResponseFormat,
        ToolChoice,
    )


class LLMProviderProtocol(Protocol):
    """Protocol for LLM providers (Async)."""

    # Capability flag: True when the provider maps tool specs to its native
    # tool-calling API and parses structured tool calls back. When False, the
    # service routes tool/structured requests through the prompt-coercion
    # fallback (see core.services.llm.structured).
    supports_native_tools: bool

    # The neutral message API (``supports_messages`` + ``generate_messages``)
    # is deliberately NOT required here: it is an optional capability, and
    # most providers do not have one. See :class:`MessageCapableProvider`;
    # callers test for it with ``getattr(provider, "supports_messages",
    # False)`` and degrade through
    # :func:`core.services.llm.messages.render_as_prompt`.

    async def generate(
        self, prompt: str, model: str, json_mode: bool = False, **kwargs: Any
    ) -> tuple[str, int]:
        """
        Generate a response.

        Two cross-provider kwargs are conventions rather than provider
        parameters, and a provider that cannot honour them must simply ignore
        them (never forward them to its SDK):

        * ``usage_sink: list[Usage]`` — append the call's metered
          :class:`~core.services.llm.usage.Usage` so the caller gets the
          input/output/cache split this ``(text, total)`` return type cannot
          carry. Without it the orchestration layer has to re-derive output as
          "total minus an estimate of the prompt", which misprices every call.
        * ``allow_refusal: bool`` — when True, return a model refusal as text
          instead of raising
          :class:`~core.services.llm.errors.LLMRefusalError`.

        Args:
            prompt: Input prompt
            model: Model name
            json_mode: Whether to request JSON output
            **kwargs: Additional provider-specific parameters

        Returns:
            Tuple of (response_text, tokens_used)
        """
        ...

    async def generate_structured(
        self,
        prompt: str,
        model: str,
        *,
        tools: "list[LLMToolSpec] | None" = None,
        tool_choice: "ToolChoice | None" = None,
        response_format: "ResponseFormat | None" = None,
        **kwargs: Any,
    ) -> "LLMResult":
        """
        Generate a response using the provider's native tool-calling /
        structured-output API.

        Only defined by providers with ``supports_native_tools = True``.

        Args:
            prompt: Input prompt (user turn).
            model: Model name.
            tools: Tool specifications the model may call.
            tool_choice: Tool-selection policy (defaults to auto).
            response_format: Optional structured-output constraint.
            **kwargs: Additional provider-specific parameters (``system``,
                ``temperature``, ``max_tokens``, ...).

        Returns:
            LLMResult: text and/or structured tool calls, carrying the metered
            ``usage`` split plus ``stop_reason``/``stop_details`` so the
            orchestration layer can apply the refusal/truncation policy.
        """
        ...

    def generate_stream(
        self, prompt: str, model: str, **kwargs: Any
    ) -> AsyncIterator[tuple[str, int]]:
        """
        Generate a streaming response.

        Args:
            prompt: Input prompt
            model: Model name
            **kwargs: Additional provider-specific parameters

        Yields:
            Tuples of (chunk_text, tokens_used_so_far)
        """
        ...

    async def close(self) -> None:
        """Close the provider connection."""
        ...


class MessageCapableProvider(Protocol):
    """A provider that accepts a neutral conversation instead of a prompt.

    Split from :class:`LLMProviderProtocol` because it is a *capability*, not a
    requirement: Anthropic and OpenAI implement it, the rest do not, and
    folding it into the base protocol would make every one of them fail a type
    check for a method they are not expected to have.

    A provider that implements this MUST also set ``supports_messages = True``;
    callers read that flag (never ``hasattr``) before handing over a history,
    and degrade to the string path when it is absent or False.
    """

    # True when the provider maps a neutral message list onto its own message
    # API. False — including by absence — routes the conversation through
    # :func:`core.services.llm.messages.render_as_prompt` and the string path,
    # which cannot carry tool-call correlation, ``is_error`` or thinking
    # blocks.
    supports_messages: bool

    async def generate_messages(
        self,
        messages: "list[Message]",
        model: str,
        *,
        tools: "list[LLMToolSpec] | None" = None,
        system: str | None = None,
        **kwargs: Any,
    ) -> "LLMResult":
        """
        Generate one turn from a neutral conversation history.

        The message-based counterpart of :meth:`generate_structured`, and the
        only shape that can express what an agentic loop actually needs: a
        ``tool_result`` correlated to the ``tool_use`` that produced it, an
        ``is_error`` flag on a failed call, an assistant turn replayed verbatim
        (thinking blocks included), and an append-only prefix the provider's
        prompt cache can reuse across iterations.

        Only defined by providers with ``supports_messages = True``; the others
        raise :class:`NotImplementedError`, and callers must check the flag.

        Args:
            messages: Conversation so far, oldest first.
            model: Model name.
            tools: Tool specifications the model may call.
            system: System prompt. Passed separately from the history — it is
                the stable prefix, and where a provider that supports declared
                prompt caching gets its breakpoint.
            **kwargs: ``tool_choice``, ``response_format``, and the provider's
                usual parameters.

        Returns:
            LLMResult: text and/or structured tool calls, carrying the metered
            ``usage`` split, the stop reason, and ``message`` — the assistant
            turn to append to the history.
        """
        ...
