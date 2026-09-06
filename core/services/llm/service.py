"""
Main LLM service implementation.

Provides a unified interface for LLM operations with caching and cost tracking.
"""

import asyncio
import contextlib
from collections.abc import AsyncIterator
from typing import Any

from core.cache import SemanticLLMCache, TTLCache
from core.config import get_llm_config
from core.lifecycle.deterministic import get_llm_override_kwargs
from core.observability.logging import get_logger
from core.resilience import retry
from core.services.llm._deadline import await_within_deadline
from core.services.llm._telemetry import (
    gen_ai_system,
    report_tokens_to_middleware,
)
from core.services.llm.cost_control import CostTracker
from core.services.llm.exceptions import RateLimitError
from core.services.llm.interfaces import LLMProviderProtocol
from core.services.llm.model_routing import routed_model
from core.services.llm.provider_factory import create_provider

# Re-exported under the historical private names for backward compatibility with
# tests/callers that patched ``service._report_tokens_to_middleware`` /
# ``service._gen_ai_system``.
_report_tokens_to_middleware = report_tokens_to_middleware
_gen_ai_system = gen_ai_system

logger = get_logger(__name__)

# Cap on a server-supplied Retry-After. A provider (or a proxy in front of it)
# can answer with a window far longer than any request is willing to wait;
# honouring it verbatim would pin a worker for minutes. Beyond this we ignore
# the hint and let the caller's own backoff/timeout budget decide.
_MAX_HONOURED_RETRY_AFTER_SECONDS = 120.0


def _parse_retry_after(exc: BaseException) -> float | None:
    """Extract the RFC 9110 ``Retry-After`` window from a provider exception.

    Provider SDKs (OpenAI, Anthropic) surface the HTTP response on the raised
    error, so the header is reachable without depending on any one SDK's types:
    the lookup is duck-typed and every failure path returns ``None``, leaving
    the retry layer on its own backoff curve.

    Only the delta-seconds form is honoured. The HTTP-date form is valid per
    the RFC but rare from these APIs, and parsing it correctly needs the
    server's clock — a skewed one would produce a wildly wrong wait.
    """
    response = getattr(exc, "response", None)
    headers = getattr(response, "headers", None)
    if headers is None:
        return None
    try:
        raw = headers.get("retry-after") or headers.get("Retry-After")
    except Exception:
        logger.debug("retry_after_header_unreadable", exc_info=True)
        return None
    if not raw:
        return None
    try:
        seconds = float(str(raw).strip())
    except (TypeError, ValueError):
        return None  # HTTP-date form, or malformed
    if seconds <= 0 or seconds > _MAX_HONOURED_RETRY_AFTER_SECONDS:
        return None
    return seconds


class LLMService:
    """
    Main LLM service with provider abstraction, caching, and cost tracking.

    Implements LLMServiceProtocol.
    """

    def __init__(
        self,
        config: Any | None = None,
        cost_tracker: CostTracker | None = None,
        enable_cache: bool = True,
        enable_semantic_cache: bool = False,
        semantic_threshold: float = 0.85,
    ):
        """
        Initialize LLM service.

        Args:
            config: LLM configuration (uses get_llm_config() if None)
            cost_tracker: Optional cost tracker for token limits
            enable_cache: Whether to enable exact-match response caching
            enable_semantic_cache: Whether to enable semantic similarity cache
            semantic_threshold: Similarity threshold for semantic cache (0.0-1.0)
        """
        self.config = config or get_llm_config()
        self.cost_tracker = cost_tracker
        self.enable_cache = enable_cache and self.config.enable_cache
        self.enable_semantic_cache = enable_semantic_cache

        # Initialize exact-match cache if enabled
        self.cache: TTLCache[str, str] | None = None
        if self.enable_cache:
            self.cache = TTLCache(
                maxsize=self.config.cache_max_size, ttl=self.config.cache_ttl
            )

        # Initialize semantic cache if enabled
        self.semantic_cache: Any | None = None
        if self.enable_semantic_cache:
            self.semantic_cache = SemanticLLMCache(
                maxsize=self.config.cache_max_size,
                ttl=self.config.cache_ttl,
                threshold=semantic_threshold,
            )

        # Initialize provider
        self.provider = self._create_provider()

        # A centrally-pinned model (per-plugin LLM policy). When set it wins
        # over per-call ``model=`` overrides — a pin is governance, not a hint.
        self._pinned_model: str | None = None

        # Single-flight coordinator: coalesce concurrent generate calls for
        # the same cache key so a stampede during a cache miss triggers only
        # one upstream LLM request instead of N.
        from core.cache.single_flight import SingleFlight

        self._inflight: SingleFlight[str] = SingleFlight()

        # Optional per-process cap on concurrent provider calls
        # (LLM_MAX_CONCURRENT_REQUESTS; 0 = unlimited). Lazy: the semaphore is
        # created on first use so it binds to the running loop.
        self._concurrency_semaphore: asyncio.Semaphore | None = None

        logger.info(
            f"Initialized LLMService with provider={self.config.provider}, "
            f"model={self.config.model}, cache={self.enable_cache}, "
            f"semantic_cache={self.enable_semantic_cache}"
        )

    def _resolve_model(
        self, model: str | None, task_category: str | None = None
    ) -> str:
        """Effective model: pinned > per-call > routed > config default."""
        return (
            self._pinned_model
            or model
            or routed_model(self.config, task_category)
            or self.config.model
        )

    def _create_provider(self) -> LLMProviderProtocol:
        """
        Instantiate the concrete LLM provider based on configuration.

        Returns:
            LLMProviderProtocol: The active provider (OpenAI, Anthropic, etc.).
        """
        return create_provider(self.config)

    def _concurrency_guard(self) -> contextlib.AbstractAsyncContextManager[Any]:
        """Per-process cap on in-flight provider calls (0 = unlimited).

        A slot is held only for the provider round-trip, so retries between
        attempts do not pin a slot while backing off.
        """
        # int() coercion keeps legacy test doubles (Mock configs) harmless:
        # anything non-numeric reads as "unlimited".
        try:
            limit = int(getattr(self.config, "max_concurrent_requests", 0) or 0)
        except (TypeError, ValueError):
            limit = 0
        if limit <= 0:
            return contextlib.nullcontext()
        if self._concurrency_semaphore is None:
            self._concurrency_semaphore = asyncio.Semaphore(limit)
        return self._concurrency_semaphore

    @retry(
        max_attempts=3,
        base_delay=1.0,
        max_delay=30.0,
        retryable_exceptions=(RateLimitError,),
    )
    async def _generate_with_retry(
        self, prompt: str, model: str, json_mode: bool, **kwargs: Any
    ) -> tuple[str, int]:
        """
        Generate response with automatic retry on rate limit errors.

        This is the SINGLE retry layer of the LLM stack: providers do not
        retry on their own (a stacked provider-level retry multiplied
        attempts up to 3x3 per request and re-tried non-transient failures).
        Only rate-limit errors are retried; everything else fails fast and
        feeds the provider's circuit breaker.

        Args:
            prompt: Input prompt
            model: Model to use
            json_mode: Whether to request JSON output

        Returns:
            Tuple of (content, tokens_used)

        Raises:
            RateLimitError: If rate limit exceeded (will be retried)
            LLMProviderError: For other provider errors
        """
        try:
            # Apply deterministic overrides (temperature=0 etc)
            overrides = get_llm_override_kwargs()
            merged = {**kwargs, **overrides}

            # Bounded by the ambient LoopBudget's remaining wall-clock time
            # (plain await outside an orchestrated request), so one slow
            # provider call can't outlive the request deadline. The
            # concurrency guard additionally caps in-flight provider calls
            # per process when LLM_MAX_CONCURRENT_REQUESTS is set.
            async with self._concurrency_guard():
                return await await_within_deadline(
                    self.provider.generate(
                        prompt=prompt, model=model, json_mode=json_mode, **merged
                    )
                )
        except Exception as e:
            # Check if it's a rate limit error (429)
            error_str = str(e).lower()
            if (
                "429" in error_str
                or "rate limit" in error_str
                or "too many" in error_str
            ):
                # Carry the provider's own Retry-After through to the retry
                # layer: backing off for less than the window it asked for
                # re-sends into a closed door and deepens the throttle.
                retry_after = _parse_retry_after(e)
                logger.warning(
                    "Rate limit hit, will retry%s: %s",
                    f" after {retry_after:.1f}s (server-requested)"
                    if retry_after is not None
                    else "",
                    e,
                )
                raise RateLimitError(str(e), retry_after=retry_after) from e
            raise

    async def generate_response(
        self,
        prompt: str,
        model: str | None = None,
        json: bool = False,
        system_prompt: str | None = None,
        temperature: float | None = None,
        max_tokens: int | None = None,
        task_category: str | None = None,
        effort: str | None = None,
    ) -> str:
        """
        Generate a response from the LLM.

        Args:
            prompt: Input prompt
            model: Optional model override (uses config default if None)
            json: Whether to request JSON output
            system_prompt: Optional system prompt
            temperature: Optional sampling temperature (provider default if None)
            max_tokens: Optional output token cap (provider default if None)
            task_category: Optional cost-aware routing hint (TaskCategory
                value); ignored unless routing is enabled
            effort: Optional extended-thinking tier (off/low/medium/high).
                When None and ``thinking_enabled`` is set, derived from
                ``task_category``. Only providers with a thinking API honour
                it (currently Anthropic); others ignore the hint.

        Returns:
            Generated response text

        Raises:
            BudgetExceededError: If token limit is exceeded
            LLMProviderError: If there's an error with the provider
        """
        from core.services.llm._generation import generate_response

        return await generate_response(
            self,
            prompt,
            model=model,
            json=json,
            system_prompt=system_prompt,
            temperature=temperature,
            max_tokens=max_tokens,
            task_category=task_category,
            effort=effort,
        )

    async def generate(
        self,
        prompt: str,
        model: str | None = None,
        *,
        tools: "list[Any] | None" = None,
        tool_choice: "Any | None" = None,
        response_format: "Any | None" = None,
        system_prompt: str | None = None,
        temperature: float | None = None,
        max_tokens: int | None = None,
        task_category: str | None = None,
    ) -> "Any":
        """
        Generate a structured response with native tool-calling support.

        Returns an ``LLMResult`` (text and/or parsed tool calls), unlike
        ``generate_response`` which returns plain text. Routes to the provider's
        native tool API when ``LLMConfig.enable_native_tools`` is set and the
        provider supports it, otherwise to a prompt-coercion fallback — so
        callers get a uniform ``LLMResult`` regardless of provider.

        Args:
            prompt: Input prompt (user turn).
            model: Optional model override (config default when None).
            tools: ``list[LLMToolSpec]`` the model may call.
            tool_choice: ``ToolChoice`` selection policy (defaults to auto).
            response_format: Optional ``ResponseFormat`` structured-output
                constraint.
            system_prompt: Optional system prompt.
            temperature: Optional sampling temperature.
            max_tokens: Optional output token cap.
            task_category: Optional cost-aware routing hint (TaskCategory
                value); ignored unless routing is enabled.

        Returns:
            LLMResult: text and/or structured tool calls with usage.
        """
        # Lazy import avoids a module-load cycle and keeps service.py under the
        # module size cap.
        from core.services.llm.structured import generate_structured

        return await generate_structured(
            self,
            prompt,
            model=model,
            tools=tools,
            tool_choice=tool_choice,
            response_format=response_format,
            system_prompt=system_prompt,
            temperature=temperature,
            max_tokens=max_tokens,
            task_category=task_category,
        )

    async def generate_response_stream(
        self,
        prompt: str,
        model: str | None = None,
        system_prompt: str | None = None,
        temperature: float | None = None,
        max_tokens: int | None = None,
    ) -> AsyncIterator[str]:
        """
        Generate a streaming response from the LLM.

        Body lives in ``core.services.llm._streaming`` (module size cap);
        accounting is identical to the non-streaming path, plus a per-chunk
        deadline from the ambient LoopBudget.

        Args:
            prompt: Input prompt
            model: Optional model override (uses config default if None)
            system_prompt: Optional system prompt
            temperature: Optional sampling temperature (provider default if None)
            max_tokens: Optional output token cap (provider default if None)

        Yields:
            Response chunks as they are generated

        Raises:
            BudgetExceededError: If token limit is exceeded
            LLMProviderError: If there's an error with the provider
        """
        from core.services.llm._streaming import stream_response

        async for chunk in stream_response(
            self,
            prompt,
            model=model,
            system_prompt=system_prompt,
            temperature=temperature,
            max_tokens=max_tokens,
        ):
            yield chunk

    async def close(self) -> None:
        """
        Release resources and close the underlying provider connection.
        """
        if hasattr(self, "provider") and hasattr(self.provider, "close"):
            await self.provider.close()
        logger.info("LLMService closed")


# Service resolution (default singleton + per-plugin policy clones) lives in
# ``runtime``; re-exported here for the historical import path.
from core.services.llm.runtime import (  # noqa: E402
    get_llm_service,
    reset_llm_service,
)

__all__ = ["LLMService", "get_llm_service", "reset_llm_service"]
