"""
Anthropic Claude provider implementation.
"""

import os
from collections.abc import AsyncIterator
from typing import Any

from pydantic import SecretStr

from core.observability.logging import get_logger

try:
    import anthropic
except ImportError:
    anthropic = None  # type: ignore

from core.resilience.circuit_breaker import get_circuit_breaker
from core.services.llm.cost_control import estimate_tokens
from core.services.llm.exceptions import LLMProviderError
from core.services.llm.thinking import resolve_thinking

# Provider-specific kwargs handled explicitly (not forwarded verbatim).
_RESERVED_KWARGS = frozenset(
    {"max_tokens", "system", "temperature", "thinking", "effort", "thinking_budget"}
)

# Prompt caching: the system prompt is the stable prefix (instructions +
# tool/RAG/memory context), re-sent on every call. Marking it with an ephemeral
# cache breakpoint lets Anthropic reuse it (~5 min TTL) instead of re-billing it
# in full — typically a large input-cost and latency win on long prefixes.
_PROMPT_CACHE_ENABLED = os.getenv("BASELITH_LLM_PROMPT_CACHE", "true").lower() in (
    "1",
    "true",
    "yes",
    "on",
)
# Anthropic silently ignores cache_control on a prefix shorter than the model
# minimum (~1024 tokens Sonnet / 2048 Haiku). Skip obviously-tiny prompts so we
# don't spend a cache breakpoint on something that can never be cached
# (~4 chars/token heuristic → ~1024 tokens).
_PROMPT_CACHE_MIN_CHARS = 4096

logger = get_logger(__name__)


def _build_system_param(system_prompt: str) -> Any:
    """Return the Anthropic ``system`` argument, cacheable when worthwhile.

    Emits a single ``text`` block carrying an ephemeral ``cache_control``
    breakpoint when caching is enabled and the prompt is long enough to be
    cacheable; otherwise the plain string (or ``NOT_GIVEN`` when empty).
    """
    if not system_prompt:
        return anthropic.NOT_GIVEN
    if _PROMPT_CACHE_ENABLED and len(system_prompt) >= _PROMPT_CACHE_MIN_CHARS:
        return [
            {
                "type": "text",
                "text": system_prompt,
                "cache_control": {"type": "ephemeral"},
            }
        ]
    return system_prompt


class AnthropicProvider:
    """Anthropic Claude LLM provider (Async)."""

    def __init__(
        self,
        api_key: str | SecretStr,
        request_timeout: float = 120.0,
        connect_timeout: float = 5.0,
    ):
        """
        Initialize Anthropic provider.

        Args:
            api_key: Anthropic API key (raw ``str`` or wrapped ``SecretStr``).
            request_timeout: Total per-request deadline in seconds.
            connect_timeout: TCP connect deadline in seconds.
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
        self._request_timeout = request_timeout
        self._connect_timeout = connect_timeout
        self.client: anthropic.AsyncAnthropic | None = None

    def _ensure_client(self) -> anthropic.AsyncAnthropic:
        """
        Lazily initialize the AsyncAnthropic client.

        Returns:
            anthropic.AsyncAnthropic: The initialized Anthropic client.
        """
        if self.client is not None:
            return self.client

        import httpx

        # max_retries=0: LLMService._generate_with_retry is the single retry
        # owner; SDK-internal retries (default 2) would stack with it and
        # amplify 429 storms. Explicit timeout: the SDK default is 600s,
        # which lets one hung request block a caller for ~10 minutes.
        self.client = anthropic.AsyncAnthropic(
            api_key=self._api_key.get_secret_value(),
            max_retries=0,
            timeout=httpx.Timeout(self._request_timeout, connect=self._connect_timeout),
        )
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
                system=_build_system_param(system_prompt),  # type: ignore[arg-type]
                **thinking_kwargs,
                **{k: v for k, v in kwargs.items() if k not in _RESERVED_KWARGS},
            )

            # Anthropic returns a list of content blocks
            content = ""
            for block in response.content:
                if block.type == "text":
                    content += block.text

            content = content.strip()

            # Get exact token usage if available. With prompt caching, cached
            # input arrives as cache_read/cache_creation counters that are NOT
            # included in ``input_tokens``; sum them so usage isn't undercounted
            # on a cache hit.
            usage = response.usage
            if usage:
                cache_write = getattr(usage, "cache_creation_input_tokens", 0) or 0
                cache_read = getattr(usage, "cache_read_input_tokens", 0) or 0
                tokens_used = (
                    usage.input_tokens + usage.output_tokens + cache_write + cache_read
                )
            else:
                tokens_used = estimate_tokens(prompt) + estimate_tokens(content)

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
                system=_build_system_param(system_prompt),  # type: ignore[arg-type]
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
