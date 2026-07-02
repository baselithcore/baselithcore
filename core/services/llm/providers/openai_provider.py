"""
OpenAI LLM provider implementation.

This module provides the concrete implementation for interacting with
OpenAI's API, supporting both standard chat completions and real-time streaming.
"""

from core.observability.logging import get_logger

try:
    import openai
except ImportError:
    openai = None  # type: ignore

from collections.abc import AsyncIterator
from typing import TYPE_CHECKING, Any, cast

from pydantic import SecretStr

if TYPE_CHECKING:
    from openai import AsyncOpenAI

from core.resilience.circuit_breaker import get_circuit_breaker
from core.services.llm.cost_control import estimate_tokens
from core.services.llm.exceptions import LLMProviderError

logger = get_logger(__name__)


class OpenAIProvider:
    """
    Asynchronous OpenAI API provider.

    Manages an internal AsyncOpenAI client and maps generic LLM requests
    to OpenAI-specific API calls.
    """

    def __init__(
        self,
        api_key: str | SecretStr,
        request_timeout: float = 120.0,
        connect_timeout: float = 5.0,
    ):
        """
        Initialize the OpenAI provider.

        Args:
            api_key: Secret API key (raw ``str`` or wrapped ``SecretStr``).
            request_timeout: Total per-request deadline in seconds.
            connect_timeout: TCP connect deadline in seconds.
        """
        if not api_key:
            raise LLMProviderError("OpenAI API key is required")

        if openai is None:
            raise LLMProviderError(
                "OpenAI library is not installed. Run 'pip install openai'"
            )

        # Keep the credential wrapped so it never appears in repr()/tracebacks/
        # Sentry frames; unwrap only at the SDK boundary in _ensure_client.
        self._api_key: SecretStr = (
            api_key if isinstance(api_key, SecretStr) else SecretStr(api_key)
        )
        self._request_timeout = request_timeout
        self._connect_timeout = connect_timeout
        self.client: Any = None

    def _ensure_client(self) -> Any:
        """
        Lazily initialize the AsyncOpenAI client.

        Returns:
            The initialized OpenAI AsyncOpenAI instance.
        """
        if self.client is None:
            if openai is None:
                raise LLMProviderError("OpenAI library not installed")

            import httpx

            # max_retries=0: LLMService._generate_with_retry is the single
            # retry owner; SDK-internal retries (default 2) would stack with
            # it and amplify 429 storms. Explicit timeout: the SDK default is
            # 600s, which lets one hung request block a caller for ~10 minutes.
            self.client = cast(
                "AsyncOpenAI",
                openai.AsyncOpenAI(
                    api_key=self._api_key.get_secret_value(),
                    max_retries=0,
                    timeout=httpx.Timeout(
                        self._request_timeout, connect=self._connect_timeout
                    ),
                ),
            )
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

    # Single retry owner is LLMService._generate_with_retry (rate-limit
    # aware). A provider-level blanket retry on Exception would multiply
    # attempts (3x3 upstream calls per request) and pointlessly retry
    # non-transient failures (bad key, invalid request). The circuit
    # breaker stays: failure isolation, not retry.
    @get_circuit_breaker("openai_provider")
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
            request_kwargs = {
                k: v for k, v in kwargs.items() if k not in ["system", "json_mode"]
            }

            system_prompt = kwargs.get("system", "")
            if json_mode:
                request_kwargs["response_format"] = {"type": "json_object"}
                if "json" not in system_prompt.lower() and "json" not in prompt.lower():
                    system_prompt += "\nOutput MUST be a valid JSON object."

            messages = []
            if system_prompt:
                messages.append({"role": "system", "content": system_prompt})
            messages.append({"role": "user", "content": prompt})

            response = await client.chat.completions.create(
                model=model,
                messages=messages,
                **request_kwargs,
            )

            raw_content = response.choices[0].message.content
            content = raw_content.strip() if raw_content else ""

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

    # No @retry here either: decorating an async generator never retried
    # anything (errors surface during iteration, outside the wrapper) —
    # the decorator was dead code. Retrying a partially consumed stream
    # would also duplicate already-yielded chunks.
    @get_circuit_breaker("openai_provider")
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
            request_kwargs = {k: v for k, v in kwargs.items() if k not in ["system"]}

            system_prompt = kwargs.get("system", "")
            messages = []
            if system_prompt:
                messages.append({"role": "system", "content": system_prompt})
            messages.append({"role": "user", "content": prompt})

            stream = await client.chat.completions.create(
                model=model,
                messages=messages,
                stream=True,
                **request_kwargs,
            )

            # During streaming we estimate tokens as metadata is often
            # unavailable per-chunk. Estimate the prompt once and accumulate
            # per-delta instead of re-tokenizing the full text every chunk.
            tokens = estimate_tokens(prompt)
            async for chunk in stream:
                content = str(chunk.choices[0].delta.content or "")
                if content:
                    tokens += estimate_tokens(content)
                    yield content, tokens

        except Exception as e:
            logger.error(f"OpenAI streaming error: {e}")
            raise LLMProviderError(f"OpenAI streaming error: {e}") from e
