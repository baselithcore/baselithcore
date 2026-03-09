"""
Base provider interface for LLM providers.
"""

from typing import Protocol, Iterator


class LLMProvider(Protocol):
    """Protocol for LLM providers."""

    def generate(
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

    def generate_stream(
        self, prompt: str, model: str, **kwargs
    ) -> Iterator[tuple[str, int]]:
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
