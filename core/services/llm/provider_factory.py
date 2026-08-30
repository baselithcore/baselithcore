"""Provider instantiation for :class:`~core.services.llm.service.LLMService`.

Maps an ``LLMConfig`` onto a concrete provider client, applying the shared
timeout policy and failing loudly when a required credential is absent.
"""

from __future__ import annotations

from typing import Any

from core.services.llm.exceptions import LLMProviderError
from core.services.llm.interfaces import LLMProviderProtocol
from core.services.llm.providers.anthropic_provider import AnthropicProvider
from core.services.llm.providers.huggingface_provider import HuggingFaceProvider
from core.services.llm.providers.ollama_provider import OllamaProvider
from core.services.llm.providers.openai_provider import OpenAIProvider


def create_provider(config: Any) -> LLMProviderProtocol:
    """
    Instantiate the concrete LLM provider described by *config*.

    Args:
        config: An ``LLMConfig``-shaped object naming the provider and its
            credentials.

    Returns:
        LLMProviderProtocol: The active provider (OpenAI, Anthropic, etc.).

    Raises:
        LLMProviderError: When the provider is unsupported or a provider that
            requires an API key was configured without one.
    """
    api_key_str = config.api_key.get_secret_value() if config.api_key else None
    request_timeout = getattr(config, "request_timeout", 120.0)
    connect_timeout = getattr(config, "connect_timeout", 5.0)

    if config.provider == "openai":
        if not api_key_str:
            raise LLMProviderError("OpenAI API key is required")
        return OpenAIProvider(
            api_key=api_key_str,
            request_timeout=request_timeout,
            connect_timeout=connect_timeout,
            # Custom OpenAI-compatible endpoint (Azure gateway, vLLM, LiteLLM,
            # OpenRouter). None keeps the SDK default.
            base_url=getattr(config, "api_base", None),
        )
    elif config.provider == "ollama":
        return OllamaProvider(api_base=config.api_base)
    elif config.provider == "huggingface":
        return HuggingFaceProvider(
            api_key=api_key_str,
            use_local=config.huggingface_local,
            device=config.huggingface_device,
            torch_dtype=config.huggingface_dtype,
            trust_remote_code=config.huggingface_trust_remote_code,
        )
    elif config.provider == "anthropic":
        backend = getattr(config, "anthropic_backend", "api") or "api"
        if backend == "api" and not api_key_str:
            raise LLMProviderError("Anthropic API key is required")
        return AnthropicProvider(
            api_key=api_key_str,
            request_timeout=request_timeout,
            connect_timeout=connect_timeout,
            # bedrock/vertex authenticate via the cloud credential chain.
            backend=backend,
            aws_region=getattr(config, "anthropic_aws_region", None),
            vertex_project=getattr(config, "anthropic_vertex_project", None),
            vertex_region=getattr(config, "anthropic_vertex_region", None),
        )
    elif config.provider == "gemini":
        if not api_key_str:
            raise LLMProviderError("Gemini API key is required")
        # Lazy import: google-genai is an optional extra ([gemini]).
        from core.services.llm.providers.gemini_provider import GeminiProvider

        return GeminiProvider(
            api_key=api_key_str,
            request_timeout=request_timeout,
        )
    else:
        raise LLMProviderError(f"Unsupported provider: {config.provider}")


__all__ = ["create_provider"]
