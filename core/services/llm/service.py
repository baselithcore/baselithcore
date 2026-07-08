"""
Main LLM service implementation.

Provides a unified interface for LLM operations with caching and cost tracking.
"""

import hashlib
from collections.abc import AsyncIterator
from typing import Any

from core.cache import SemanticLLMCache, TTLCache
from core.config import get_llm_config
from core.lifecycle.deterministic import get_llm_override_kwargs
from core.middleware.cost_control import (
    BudgetExceededError as MiddlewareBudgetExceededError,
)
from core.observability.logging import get_logger
from core.resilience import retry
from core.services.llm._deadline import await_within_deadline
from core.services.llm._telemetry import gen_ai_system, report_tokens_to_middleware
from core.services.llm.cost_control import CostTracker, estimate_tokens
from core.services.llm.exceptions import (
    BudgetExceededError,
    LLMProviderError,
    RateLimitError,
)
from core.services.llm.interfaces import LLMProviderProtocol
from core.services.llm.providers.anthropic_provider import AnthropicProvider
from core.services.llm.providers.huggingface_provider import HuggingFaceProvider
from core.services.llm.providers.ollama_provider import OllamaProvider
from core.services.llm.providers.openai_provider import OpenAIProvider

# Re-exported under the historical private names for backward compatibility with
# tests/callers that patched ``service._report_tokens_to_middleware`` /
# ``service._gen_ai_system``.
_report_tokens_to_middleware = report_tokens_to_middleware
_gen_ai_system = gen_ai_system

logger = get_logger(__name__)


class LLMService:
    """
    Main LLM service with provider abstraction, caching, and cost tracking.

    Implements LLMServiceProtocol.
    """

    def __init__(
        self,
        config=None,
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
        from typing import Any as LocalAny

        self.semantic_cache: LocalAny | None = None
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

        logger.info(
            f"Initialized LLMService with provider={self.config.provider}, "
            f"model={self.config.model}, cache={self.enable_cache}, "
            f"semantic_cache={self.enable_semantic_cache}"
        )

    def _resolve_model(self, model: str | None) -> str:
        """Effective model: policy-pinned first, then per-call, then config."""
        return self._pinned_model or model or self.config.model

    def _create_provider(self) -> LLMProviderProtocol:
        """
        Instantiate the concrete LLM provider based on configuration.

        Returns:
            LLMProviderProtocol: The active provider (OpenAI, Anthropic, etc.).
        """
        api_key_str = (
            self.config.api_key.get_secret_value() if self.config.api_key else None
        )
        request_timeout = getattr(self.config, "request_timeout", 120.0)
        connect_timeout = getattr(self.config, "connect_timeout", 5.0)
        if self.config.provider == "openai":
            if not api_key_str:
                raise LLMProviderError("OpenAI API key is required")
            return OpenAIProvider(
                api_key=api_key_str,
                request_timeout=request_timeout,
                connect_timeout=connect_timeout,
            )
        elif self.config.provider == "ollama":
            return OllamaProvider(api_base=self.config.api_base)
        elif self.config.provider == "huggingface":
            return HuggingFaceProvider(
                api_key=api_key_str,
                use_local=self.config.huggingface_local,
                device=self.config.huggingface_device,
                torch_dtype=self.config.huggingface_dtype,
                trust_remote_code=self.config.huggingface_trust_remote_code,
            )
        elif self.config.provider == "anthropic":
            if not api_key_str:
                raise LLMProviderError("Anthropic API key is required")
            return AnthropicProvider(
                api_key=api_key_str,
                request_timeout=request_timeout,
                connect_timeout=connect_timeout,
            )
        else:
            raise LLMProviderError(f"Unsupported provider: {self.config.provider}")

    @retry(
        max_attempts=3,
        base_delay=1.0,
        max_delay=30.0,
        retryable_exceptions=(RateLimitError,),
    )
    async def _generate_with_retry(
        self, prompt: str, model: str, json_mode: bool, **kwargs
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
            # provider call can't outlive the request deadline.
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
                logger.warning(f"Rate limit hit, will retry: {e}")
                raise RateLimitError(str(e)) from e
            raise

    async def generate_response(
        self,
        prompt: str,
        model: str | None = None,
        json: bool = False,
        system_prompt: str | None = None,
        temperature: float | None = None,
        max_tokens: int | None = None,
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

        Returns:
            Generated response text

        Raises:
            BudgetExceededError: If token limit is exceeded
            LLMProviderError: If there's an error with the provider
        """
        from core.observability import get_tracer

        # Lazy: a module-level import of core.orchestration would be circular
        # (orchestration handlers import this service).
        from core.orchestration.limits import (
            BudgetExceededError as LoopBudgetExceededError,
        )

        model = self._resolve_model(model)
        tracer = get_tracer("llm-service")

        # OTel GenAI semantic conventions (gen_ai.*) so standard GenAI dashboards
        # and semconv-aware backends light up. App-specific fields live under the
        # gen_ai.baselith.* extension namespace.
        span_attributes: dict[str, Any] = {
            "gen_ai.operation.name": "chat",
            "gen_ai.system": _gen_ai_system(self.config.provider),
            "gen_ai.request.model": model,
            "gen_ai.baselith.json_mode": json,
            "gen_ai.baselith.prompt_length": len(prompt),
        }
        if temperature is not None:
            span_attributes["gen_ai.request.temperature"] = temperature
        if max_tokens is not None:
            span_attributes["gen_ai.request.max_tokens"] = max_tokens

        with tracer.start_span(
            f"chat {model}",
            attributes=span_attributes,
        ) as span:
            # Check semantic cache first (if enabled)
            if self.semantic_cache is not None:
                semantic_cached = await self.semantic_cache.get_similar(prompt)
                if semantic_cached:
                    span.set_attribute("gen_ai.baselith.semantic_cache_hit", True)
                    return semantic_cached

            # Check exact-match cache. The hash covers every input that can
            # change the completion (system prompt and sampling params, not
            # just the user prompt) so two callers with the same prompt but
            # different system prompts never share a cached answer.
            from core.context import get_current_tenant_id

            tenant_id = get_current_tenant_id()
            key_material = "\x1f".join(
                (prompt, system_prompt or "", repr(temperature), repr(max_tokens))
            )
            prompt_hash = hashlib.sha256(key_material.encode()).hexdigest()
            cache_key = f"{tenant_id}:{model}:{json}:{prompt_hash}"
            if self.cache is not None:
                cached = await self.cache.get(cache_key)
                if cached:
                    logger.debug("Cache hit for prompt hash: %s", prompt_hash[:16])
                    span.set_attribute("gen_ai.baselith.cache_hit", True)
                    return cached

            span.set_attribute("gen_ai.baselith.cache_hit", False)
            span.set_attribute("gen_ai.baselith.semantic_cache_hit", False)

            async def _generate_and_cache() -> str:
                # Re-check the cache after acquiring the single-flight slot:
                # an earlier concurrent caller may have populated it while we
                # were queued, in which case we skip the upstream call.
                if self.cache is not None:
                    fresh = await self.cache.get(cache_key)
                    if fresh:
                        span.set_attribute("gen_ai.baselith.cache_hit", True)
                        return fresh

                # Track input tokens
                input_tokens = estimate_tokens(prompt)
                _report_tokens_to_middleware(input_tokens, model="input")
                if self.cost_tracker:
                    self.cost_tracker.track_tokens(input_tokens, model="input")

                extra_kwargs: dict = {}
                if system_prompt:
                    extra_kwargs["system"] = system_prompt
                if temperature is not None:
                    extra_kwargs["temperature"] = temperature
                if max_tokens is not None:
                    extra_kwargs["max_tokens"] = max_tokens
                content, tokens_used = await self._generate_with_retry(
                    prompt=prompt, model=model, json_mode=json, **extra_kwargs
                )

                output_tokens = max(tokens_used - input_tokens, 0)
                span.set_attribute("gen_ai.usage.input_tokens", input_tokens)
                span.set_attribute("gen_ai.usage.output_tokens", output_tokens)
                span.set_attribute("gen_ai.baselith.response_length", len(content))
                _report_tokens_to_middleware(output_tokens, model=model)
                if self.cost_tracker:
                    self.cost_tracker.track_tokens(output_tokens, model=model)

                # Charge real dollar cost against the ambient per-request
                # LoopBudget (no-op outside an orchestrated request). Raises
                # LoopBudgetExceededError when the request blows its USD cap.
                from core.orchestration.budget_context import charge_llm_cost

                charge_llm_cost(model, input_tokens, output_tokens)

                # Cache response (exact match)
                if self.cache is not None:
                    await self.cache.set(cache_key, content)

                # Cache response (semantic)
                if self.semantic_cache is not None:
                    await self.semantic_cache.set(prompt, content)

                return content

            try:
                return await self._inflight.do(cache_key, _generate_and_cache)
            except (
                BudgetExceededError,
                MiddlewareBudgetExceededError,
                LoopBudgetExceededError,
            ):
                span.set_attribute("gen_ai.baselith.error", "budget_exceeded")
                raise
            except Exception as e:
                span.set_attribute("gen_ai.baselith.error", str(e))
                logger.error(f"Error generating response: {e}")
                raise LLMProviderError(f"Generation failed: {e}") from e

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
        from core.observability import get_tracer

        # Lazy: a module-level import of core.orchestration would be circular
        # (orchestration handlers import this service).
        from core.orchestration.limits import (
            BudgetExceededError as LoopBudgetExceededError,
        )

        model = self._resolve_model(model)
        tracer = get_tracer("llm-service")

        with tracer.start_span(
            f"chat {model}",
            attributes={
                "gen_ai.operation.name": "chat",
                "gen_ai.system": _gen_ai_system(self.config.provider),
                "gen_ai.request.model": model,
                "gen_ai.baselith.prompt_length": len(prompt),
                "gen_ai.baselith.streaming": True,
            },
        ) as span:
            # Track input tokens
            stream_input_tokens = estimate_tokens(prompt)
            _report_tokens_to_middleware(stream_input_tokens, model="input_stream")
            if self.cost_tracker:
                self.cost_tracker.track_tokens(
                    stream_input_tokens, model="input_stream"
                )

            try:
                accumulated_tokens = 0
                stream_kwargs: dict = {}
                if system_prompt:
                    stream_kwargs["system"] = system_prompt
                if temperature is not None:
                    stream_kwargs["temperature"] = temperature
                if max_tokens is not None:
                    stream_kwargs["max_tokens"] = max_tokens
                async for chunk, tokens in self.provider.generate_stream(
                    prompt=prompt, model=model, **stream_kwargs
                ):
                    # Track incremental tokens
                    new_tokens = tokens - accumulated_tokens
                    if new_tokens > 0:
                        _report_tokens_to_middleware(new_tokens, model=model)
                        if self.cost_tracker:
                            self.cost_tracker.track_tokens(new_tokens, model=model)
                    accumulated_tokens = tokens

                    yield chunk

                span.set_attribute("gen_ai.usage.output_tokens", accumulated_tokens)

                # Charge the completed stream against the ambient per-request
                # LoopBudget (no-op outside an orchestrated request). Charged
                # once at stream end so a mid-stream abort is never triggered
                # by the charge itself.
                from core.orchestration.budget_context import charge_llm_cost

                charge_llm_cost(
                    model,
                    stream_input_tokens,
                    max(accumulated_tokens - stream_input_tokens, 0),
                )

            except (
                BudgetExceededError,
                MiddlewareBudgetExceededError,
                LoopBudgetExceededError,
            ):
                span.set_attribute("gen_ai.baselith.error", "budget_exceeded")
                raise
            except Exception as e:
                span.set_attribute("gen_ai.baselith.error", str(e))
                logger.error(f"Error in streaming generation: {e}")
                raise LLMProviderError(f"Streaming failed: {e}") from e

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
