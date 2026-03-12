"""
OpenAI LLM provider implementation.

This module provides the concrete implementation for interacting with
OpenAI's API, supporting both standard chat completions and real-time streaming.
"""

from core.observability.logging import get_logger
from typing import Any, AsyncIterator

import openai

from core.services.llm.cost_control import estimate_tokens
from core.services.llm.exceptions import LLMProviderError
from core.resilience.circuit_breaker import get_circuit_breaker
from core.resilience.retry import retry

logger = get_logger(__name__)


class OpenAIProvider:
    """
    Asynchronous OpenAI API provider.

    Manages an internal AsyncClient and maps generic LLM requests
    to OpenAI-specific API calls.
    """

    def __init__(self, api_key: str):
        """
        Initialize the OpenAI provider.

        Args:
            api_key: Secret API key for OpenAI authentication.
        """
        if not api_key:
            raise LLMProviderError("OpenAI API key is required")

        self.api_key = api_key
        self.client: Any = None

    def _ensure_client(self) -> Any:
        """
        Lazily initialize the AsyncOpenAI client.

        Returns:
            The initialized OpenAI AsyncClient instance.
        """
        if self.client is not None:
            return self.client

        self.client = openai.AsyncClient(api_key=self.api_key)
        logger.info("Initialized OpenAI provider (Async)")
        return self.client

    async def close(self) -> None:
        """
        Close the underlying HTTP client for clean shutdown.
        """
        if self.client is not None:
            try:
                await self.client.close()
                self.client = None
                logger.info("Closed OpenAI provider client")
            except Exception as e:
                logger.warning(f"Error closing OpenAI client: {e}")

    @get_circuit_breaker("openai_provider")
    @retry(max_attempts=3, exponential_base=2.0)
    async def generate(
        self, prompt: str, model: str, json_mode: bool = False, **kwargs
    ) -> tuple[str, int]:
        """
        Execute a standard chat completion request.

        Args:
            prompt: User message content.
            model: Deployment/Model ID (e.g., 'gpt-4o').
            json_mode: If True, enforces 'json_object' response format.
            **kwargs: Passthrough arguments for the OpenAI completion API.

        Returns:
            tuple[str, int]: A tuple containing the response text and total tokens used.

        Raises:
            LLMProviderError: If the API call fails or model parameters are invalid.
        """
        client = self._ensure_client()
        try:
            request_kwargs = {}
            if json_mode:
                request_kwargs["response_format"] = {"type": "json_object"}

            response = await client.chat.completions.create(  # type: ignore[call-overload]
                model=model,
                messages=[{"role": "user", "content": prompt}],
                **request_kwargs,
            )

            content = response.choices[0].message.content.strip()

            # Retrieve official token usage from metadata, fallback to estimation if missing.
            tokens_used = (
                response.usage.total_tokens
                if response.usage
                else estimate_tokens(prompt) + estimate_tokens(content)
            )

            return content, tokens_used

        except Exception as e:
            logger.error(f"OpenAI generation error: {e}")
            raise LLMProviderError(f"OpenAI error: {e}") from e

    @get_circuit_breaker("openai_provider")
    @retry(max_attempts=3, exponential_base=2.0)
    async def generate_stream(
        self, prompt: str, model: str, **kwargs
    ) -> AsyncIterator[tuple[str, int]]:
        """
        Execute a streaming completion request.

        Args:
            prompt: User message content.
            model: Target model ID.
            **kwargs: Passthrough parameters.

        Yields:
            tuple[str, int]: Chunks of text and current estimation of total tokens.
        """
        client = self._ensure_client()
        try:
            stream = await client.chat.completions.create(
                model=model,
                messages=[{"role": "user", "content": prompt}],
                stream=True,
            )

            accumulated_content = ""
            async for chunk in stream:
                content = chunk.choices[0].delta.content or ""
                if content:
                    accumulated_content += content
                    # During streaming, we estimate tokens as metadata is often unavailable per-chunk.
                    tokens = estimate_tokens(prompt) + estimate_tokens(
                        accumulated_content
                    )
                    yield content, tokens

        except Exception as e:
            logger.error(f"OpenAI streaming error: {e}")
            raise LLMProviderError(f"OpenAI streaming error: {e}") from e
