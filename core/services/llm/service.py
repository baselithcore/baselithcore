"""
Main LLM service implementation.

Provides a unified interface for LLM operations with caching and cost tracking.
"""

from core.observability.logging import get_logger
from typing import Optional, AsyncIterator

from core.cache import TTLCache, SemanticLLMCache
from core.config import get_llm_config
from core.resilience import retry
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
from core.lifecycle.deterministic import get_llm_override_kwargs

logger = get_logger(__name__)


class LLMService:
    """
    Main LLM service with provider abstraction, caching, and cost tracking.

    Implements LLMServiceProtocol.
    """

    def __init__(
        self,
        config=None,
        cost_tracker: Optional[CostTracker] = None,
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
        self.cache: Optional[TTLCache[str, str]] = None
        if self.enable_cache:
            self.cache = TTLCache(
                maxsize=self.config.cache_max_size, ttl=self.config.cache_ttl
            )

        # Initialize semantic cache if enabled
        from typing import Any as LocalAny

        self.semantic_cache: Optional[LocalAny] = None
        if self.enable_semantic_cache:
            self.semantic_cache = SemanticLLMCache(
                maxsize=self.config.cache_max_size,
                ttl=self.config.cache_ttl,
                threshold=semantic_threshold,
            )

        # Initialize provider
        self.provider = self._create_provider()

        logger.info(
            f"Initialized LLMService with provider={self.config.provider}, "
            f"model={self.config.model}, cache={self.enable_cache}, "
            f"semantic_cache={self.enable_semantic_cache}"
        )

    def _create_provider(self) -> LLMProviderProtocol:
        """
        Instantiate the concrete LLM provider based on configuration.

        Returns:
            LLMProviderProtocol: The active provider (OpenAI, Anthropic, etc.).
        """
        if self.config.provider == "openai":
            if not self.config.api_key:
                raise LLMProviderError("OpenAI API key is required")
            return OpenAIProvider(api_key=self.config.api_key)
        elif self.config.provider == "ollama":
            return OllamaProvider(api_base=self.config.api_base)
        elif self.config.provider == "huggingface":
            return HuggingFaceProvider(
                api_key=self.config.api_key,
                use_local=self.config.huggingface_local,
                device=self.config.huggingface_device,
                torch_dtype=self.config.huggingface_dtype,
                trust_remote_code=self.config.huggingface_trust_remote_code,
            )
        elif self.config.provider == "anthropic":
            if not self.config.api_key:
                raise LLMProviderError("Anthropic API key is required")
            return AnthropicProvider(api_key=self.config.api_key)
        else:
            raise LLMProviderError(f"Unsupported provider: {self.config.provider}")

    @retry(
        max_attempts=3,
        base_delay=1.0,
        max_delay=30.0,
        retryable_exceptions=(RateLimitError,),
    )
    async def _generate_with_retry(
        self, prompt: str, model: str, json_mode: bool
    ) -> tuple[str, int]:
        """
        Generate response with automatic retry on rate limit errors.

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

            return await self.provider.generate(
                prompt=prompt, model=model, json_mode=json_mode, **overrides
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
        self, prompt: str, model: str | None = None, json: bool = False
    ) -> str:
        """
        Generate a response from the LLM.

        Args:
            prompt: Input prompt
            model: Optional model override (uses config default if None)
            json: Whether to request JSON output

        Returns:
            Generated response text

        Raises:
            BudgetExceededError: If token limit is exceeded
            LLMProviderError: If there's an error with the provider
        """
        from core.observability import get_tracer

        model = model or self.config.model
        tracer = get_tracer("llm-service")

        with tracer.start_span(
            "llm.generate_response",
            attributes={
                "llm.model": model,
                "llm.json_mode": json,
                "llm.prompt_length": len(prompt),
            },
        ) as span:
            # Check semantic cache first (if enabled)
            if self.semantic_cache is not None:
                semantic_cached = await self.semantic_cache.get_similar(prompt)
                if semantic_cached:
                    span.set_attribute("llm.semantic_cache_hit", True)
                    return semantic_cached

            # Check exact-match cache
            from core.context import get_current_tenant_id

            tenant_id = get_current_tenant_id()
            cache_key = f"{tenant_id}:{model}:{json}:{prompt}"
            if self.cache is not None:
                cached = await self.cache.get(cache_key)
                if cached:
                    logger.info(f"🧠 Cache hit for prompt: '{prompt[:30]}...'")
                    span.set_attribute("llm.cache_hit", True)
                    return cached

            span.set_attribute("llm.cache_hit", False)
            span.set_attribute("llm.semantic_cache_hit", False)

            # Track input tokens
            if self.cost_tracker:
                self.cost_tracker.track_tokens(estimate_tokens(prompt), model="input")

            try:
                # Generate response with retry on rate limit
                content, tokens_used = await self._generate_with_retry(
                    prompt=prompt, model=model, json_mode=json
                )

                span.set_attribute("llm.tokens_used", tokens_used)
                span.set_attribute("llm.response_length", len(content))

                # Track output tokens
                if self.cost_tracker:
                    output_tokens = tokens_used - estimate_tokens(prompt)
                    self.cost_tracker.track_tokens(output_tokens, model=model)

                # Cache response (exact match)
                if self.cache is not None:
                    await self.cache.set(cache_key, content)

                # Cache response (semantic)
                if self.semantic_cache is not None:
                    await self.semantic_cache.set(prompt, content)

                return content

            except BudgetExceededError:
                span.set_attribute("llm.error", "budget_exceeded")
                raise
            except Exception as e:
                span.set_attribute("llm.error", str(e))
                logger.error(f"Error generating response: {e}")
                raise LLMProviderError(f"Generation failed: {e}") from e

    async def generate_response_stream(
        self, prompt: str, model: str | None = None
    ) -> AsyncIterator[str]:
        """
        Generate a streaming response from the LLM.

        Args:
            prompt: Input prompt
            model: Optional model override (uses config default if None)

        Yields:
            Response chunks as they are generated

        Raises:
            BudgetExceededError: If token limit is exceeded
            LLMProviderError: If there's an error with the provider
        """
        from core.observability import get_tracer

        model = model or self.config.model
        tracer = get_tracer("llm-service")

        with tracer.start_span(
            "llm.generate_response_stream",
            attributes={
                "llm.model": model,
                "llm.prompt_length": len(prompt),
                "llm.streaming": True,
            },
        ) as span:
            # Track input tokens
            if self.cost_tracker:
                self.cost_tracker.track_tokens(
                    estimate_tokens(prompt), model="input_stream"
                )

            try:
                accumulated_tokens = 0
                async for chunk, tokens in self.provider.generate_stream(
                    prompt=prompt, model=model
                ):
                    # Track incremental tokens
                    if self.cost_tracker:
                        new_tokens = tokens - accumulated_tokens
                        if new_tokens > 0:
                            self.cost_tracker.track_tokens(new_tokens, model=model)
                        accumulated_tokens = tokens

                    yield chunk

                span.set_attribute("llm.total_tokens", accumulated_tokens)

            except BudgetExceededError:
                span.set_attribute("llm.error", "budget_exceeded")
                raise
            except Exception as e:
                span.set_attribute("llm.error", str(e))
                logger.error(f"Error in streaming generation: {e}")
                raise LLMProviderError(f"Streaming failed: {e}") from e

    async def close(self) -> None:
        """
        Release resources and close the underlying provider connection.
        """
        if hasattr(self, "provider") and hasattr(self.provider, "close"):
            await self.provider.close()
        logger.info("LLMService closed")


# Global instance
_llm_service: Optional[LLMService] = None


def get_llm_service() -> LLMService:
    """
    Get or create the global LLMService singleton.

    Returns:
        LLMService: The shared service instance.
    """
    global _llm_service
    if _llm_service is None:
        _llm_service = LLMService()
    return _llm_service


def reset_llm_service() -> None:
    """
    Clear the global LLMService instance.

    Forces a fresh initialization on the next `get_llm_service` call,
    useful for testing or hot-reloading configuration.
    """
    global _llm_service
    _llm_service = None
