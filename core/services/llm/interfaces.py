"""
LLM Provider and Service interface definitions.
"""

from typing import AsyncIterator, Protocol, Tuple


class LLMProviderProtocol(Protocol):
    """Protocol for LLM providers (Async)."""

    async def generate(
        self, prompt: str, model: str, json_mode: bool = False, **kwargs
    ) -> Tuple[str, int]:
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

    def generate_stream(
        self, prompt: str, model: str, **kwargs
    ) -> AsyncIterator[Tuple[str, int]]:
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
