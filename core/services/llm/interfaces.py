"""
LLM Provider and Service interface definitions.
"""

from collections.abc import AsyncIterator
from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
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

    async def generate(
        self, prompt: str, model: str, json_mode: bool = False, **kwargs
    ) -> tuple[str, int]:
        """
        Generate a response.

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
        **kwargs,
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
            LLMResult: text and/or structured tool calls with usage.
        """
        ...

    def generate_stream(
        self, prompt: str, model: str, **kwargs
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
