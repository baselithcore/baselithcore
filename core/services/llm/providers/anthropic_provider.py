"""
Anthropic Claude provider implementation.
"""

from core.observability.logging import get_logger
from typing import Any, AsyncIterator, Optional

from pydantic import SecretStr

try:
    import anthropic
except ImportError:
    anthropic = None  # type: ignore

from core.services.llm.cost_control import estimate_tokens
from core.services.llm.exceptions import LLMProviderError
from core.services.llm.thinking import resolve_thinking
from core.resilience.circuit_breaker import get_circuit_breaker

# Provider-specific kwargs handled explicitly (not forwarded verbatim).
_RESERVED_KWARGS = frozenset(
    {"max_tokens", "system", "temperature", "thinking", "effort", "thinking_budget"}
)

logger = get_logger(__name__)


class AnthropicProvider:
    """Anthropic Claude LLM provider (Async)."""

    def __init__(self, api_key: str | SecretStr):
        """
        Initialize Anthropic provider.

        Args:
            api_key: Anthropic API key (raw ``str`` or wrapped ``SecretStr``).
        """
        if not api_key:
            raise LLMProviderError("Anthropic API key is required")

        if anthropic is None:
            raise LLMProviderError(
                "Anthropic library is not installed. Run 'pip install anthropic'"
            )

        # Keep the credential wrapped so it never appears in repr()/tracebacks/
        # Sentry frames; unwrap only at the SDK boundary in _ensure_client.
        self._api_key: SecretStr = (
            api_key if isinstance(api_key, SecretStr) else SecretStr(api_key)
        )
        self.client: Optional[anthropic.AsyncAnthropic] = None

    def _ensure_client(self) -> anthropic.AsyncAnthropic:
        """
        Lazily initialize the AsyncAnthropic client.

        Returns:
            anthropic.AsyncAnthropic: The initialized Anthropic client.
        """
        if self.client is not None:
            return self.client

        self.client = anthropic.AsyncAnthropic(api_key=self._api_key.get_secret_value())
        logger.info("Initialized Anthropic provider (Async)")
        return self.client

    async def close(self) -> None:
        """
        Release resources and close the underlying Anthropic client.
        """
        if self.client is not None:
            try:
                await self.client.close()
                self.client = None
                logger.info("Closed Anthropic provider client")
            except Exception as e:
                logger.warning(f"Error closing Anthropic client: {e}")

    # Single retry owner is LLMService._generate_with_retry (rate-limit
    # aware). A provider-level blanket retry on Exception would multiply
    # attempts (3x3 upstream calls per request) and pointlessly retry
    # non-transient failures (bad key, invalid request). The circuit
    # breaker stays: failure isolation, not retry.
    @get_circuit_breaker("anthropic_provider")
    async def generate(
        self, prompt: str, model: str, json_mode: bool = False, **kwargs
    ) -> tuple[str, int]:
        """
        Generate a response using Anthropic Claude.

        Args:
            prompt: Input prompt
            model: Model name (e.g., 'claude-3-5-sonnet-20240620')
            json_mode: Whether to request JSON output (handled via system prompt for Claude)
            **kwargs: Additional parameters

        Returns:
            Tuple of (response_text, tokens_used)
        """
        client = self._ensure_client()
        try:
            messages: list[Any] = [{"role": "user", "content": prompt}]

            # If json_mode is requested, we should ideally use a system prompt
            # but for consistency with OpenAI/Ollama providers in this core,
            # we keep it simple or follow their pattern if they have specific json support.
            # Claude currently supports JSON mode via prefilling or system instructions.

            system_prompt = kwargs.get("system", "")
            if json_mode and "json" not in system_prompt.lower():
                system_prompt += "\nOutput MUST be a valid JSON object."

            # Optional extended-thinking budget. Off by default, so callers
            # that pass neither ``effort`` nor ``thinking_budget`` keep the
            # previous behaviour (temperature honoured, no thinking block).
            plan = resolve_thinking(
                effort=kwargs.get("effort"),
                thinking_budget=kwargs.get("thinking_budget"),
                max_tokens=kwargs.get("max_tokens", 4096),
            )
            thinking_kwargs = plan.to_anthropic_kwargs()
            if not plan.enabled:
                thinking_kwargs["temperature"] = kwargs.get("temperature", 0.7)

            response = await client.messages.create(
                model=model,
                messages=messages,
                system=system_prompt or anthropic.NOT_GIVEN,  # type: ignore[arg-type]
                **thinking_kwargs,
                **{k: v for k, v in kwargs.items() if k not in _RESERVED_KWARGS},
            )

            # Anthropic returns a list of content blocks
            content = ""
            for block in response.content:
                if block.type == "text":
                    content += block.text

            content = content.strip()

            # Get exact token usage if available
            tokens_used = (
                response.usage.input_tokens + response.usage.output_tokens
                if response.usage
                else estimate_tokens(prompt) + estimate_tokens(content)
            )

            return content, tokens_used

        except Exception as e:
            logger.error(f"Anthropic generation error: {e}")
            raise LLMProviderError(f"Anthropic error: {e}") from e

    # No @retry here either: decorating an async generator never retried
    # anything (errors surface during iteration, outside the wrapper) —
    # the decorator was dead code. Retrying a partially consumed stream
    # would also duplicate already-yielded chunks.
    @get_circuit_breaker("anthropic_provider")
    async def generate_stream(
        self, prompt: str, model: str, **kwargs
    ) -> AsyncIterator[tuple[str, int]]:
        """
        Generate a streaming response using Anthropic Claude.

        Args:
            prompt: Input prompt
            model: Model name
            **kwargs: Additional parameters

        Yields:
            Tuples of (chunk_text, accumulated_tokens)
        """
        client = self._ensure_client()
        try:
            system_prompt = kwargs.get("system", "")

            async with client.messages.stream(
                model=model,
                max_tokens=kwargs.get("max_tokens", 4096),
                messages=[{"role": "user", "content": prompt}],  # type: ignore[arg-type]
                system=system_prompt or anthropic.NOT_GIVEN,  # type: ignore[arg-type]
                temperature=kwargs.get("temperature", 0.7),
                **{k: v for k, v in kwargs.items() if k not in _RESERVED_KWARGS},
            ) as stream:
                # Estimate prompt tokens once; accumulate per-delta instead of
                # re-tokenizing the full accumulated text on every chunk
                # (which is O(n^2) over the stream).
                tokens = estimate_tokens(prompt)
                async for chunk in stream:
                    # Anthropic stream events: TextEvent, ContentBlockStartEvent, etc.
                    # For text content, we want the delta text from 'text_delta' events
                    if chunk.type == "text_delta":
                        text = chunk.text
                        tokens += estimate_tokens(text)
                        yield text, tokens

        except Exception as e:
            logger.error(f"Anthropic streaming error: {e}")
            raise LLMProviderError(f"Anthropic streaming error: {e}") from e
