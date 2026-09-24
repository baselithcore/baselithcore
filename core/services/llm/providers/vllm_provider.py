"""vLLM provider — self-hosted inference over the OpenAI-compatible server.

vLLM (``vllm serve <model>``) exposes the OpenAI Chat Completions protocol, so
this provider reuses :class:`OpenAIProvider`'s request shaping, tool calling,
structured outputs (``response_format: json_schema``, enforced server-side by
guided decoding) and usage-carrying streams. What it changes is everything
that was wrong about reaching vLLM *as* OpenAI:

* **No key required.** A vLLM server runs keyless unless started with
  ``--api-key``; the SDK still insists on a string, so ``"EMPTY"`` — vLLM's own
  convention — is sent in its place.
* **Its own circuit breaker.** ``vllm_provider``, not ``openai_provider``: a GPU
  box going down must not open the breaker guarding a hosted OpenAI stage of the
  same fallback chain, and the chain skips stages by that name.
* **Its own name.** Errors and logs say vLLM, not OpenAI.
* **Tool calling is a server flag.** It needs ``--enable-auto-tool-choice`` and
  a ``--tool-call-parser``; ``native_tools=False`` sends tool use through the
  prompt-coercion path instead of a request the server rejects.

vLLM-only sampling parameters (``top_k``, ``min_p``, ``repetition_penalty``,
``chat_template_kwargs``) travel in ``extra_body``, which the OpenAI request
shaper forwards untouched.
"""

from __future__ import annotations

import inspect
from collections.abc import AsyncIterator
from typing import Any
from urllib.parse import urlsplit, urlunsplit

from pydantic import SecretStr

from core.resilience.circuit_breaker import get_circuit_breaker
from core.services.llm.exceptions import LLMProviderError
from core.services.llm.images import GeneratedImage
from core.services.llm.messages import Message
from core.services.llm.providers.openai_provider import OpenAIProvider
from core.services.llm.tool_calling import (
    LLMResult,
    LLMToolSpec,
    ResponseFormat,
    ToolChoice,
)

__all__ = ["VLLM_NO_KEY", "VLLMProvider", "normalize_vllm_base_url"]

#: Placeholder key for a server started without ``--api-key``. The SDK refuses
#: an empty key; vLLM ignores the header entirely when it has none configured.
VLLM_NO_KEY = "EMPTY"

_BREAKER = "vllm_provider"

# The parent's methods are wrapped by the ``openai_provider`` breaker. Calling
# the unwrapped functions keeps the protocol logic in one place while the
# overrides below count every outcome against vLLM's own breaker instead.
_generate = inspect.unwrap(OpenAIProvider.generate)
_generate_structured = inspect.unwrap(OpenAIProvider.generate_structured)
_generate_messages = inspect.unwrap(OpenAIProvider.generate_messages)
_generate_stream = inspect.unwrap(OpenAIProvider.generate_stream)


def normalize_vllm_base_url(url: str) -> str:
    """The OpenAI-compatible root of a vLLM server.

    Operators paste either the server (``http://gpu:8000``) or the API root
    (``http://gpu:8000/v1``); the SDK needs the latter, and a missing ``/v1``
    turns every call into a 404 that reads like a wrong model name.

    Args:
        url: The configured endpoint.

    Returns:
        str: The endpoint ending in ``/v1``, without a trailing slash.
    """
    parts = urlsplit(url.strip())
    path = parts.path.rstrip("/")
    if not path.endswith("/v1"):
        path = f"{path}/v1"
    return urlunsplit((parts.scheme, parts.netloc, path, parts.query, ""))


class VLLMProvider(OpenAIProvider):
    """Provider for a self-hosted vLLM OpenAI-compatible server."""

    provider_label: str = "vLLM"

    def __init__(
        self,
        api_base: str | None,
        api_key: str | SecretStr | None = None,
        request_timeout: float = 120.0,
        connect_timeout: float = 5.0,
        native_tools: bool = True,
    ):
        """
        Initialize the vLLM provider.

        Args:
            api_base: The server's endpoint, with or without ``/v1``. Required:
                vLLM's default port is 8000, which is also where this backend
                listens, so a guessed default would call the framework itself.
            api_key: The value of the server's ``--api-key``; ``None`` for a
                keyless server.
            request_timeout: Total per-request deadline in seconds.
            connect_timeout: TCP connect deadline in seconds.
            native_tools: Whether the server was started with
                ``--enable-auto-tool-choice``.

        Raises:
            LLMProviderError: When no endpoint is configured.
        """
        if not api_base or not api_base.strip():
            raise LLMProviderError(
                "vLLM endpoint is required: set LLM_VLLM_API_BASE "
                "(e.g. http://gpu-host:8000/v1)"
            )
        key: str | SecretStr = api_key if api_key else VLLM_NO_KEY
        if isinstance(key, SecretStr) and not key.get_secret_value().strip():
            key = VLLM_NO_KEY
        super().__init__(
            api_key=key,
            request_timeout=request_timeout,
            connect_timeout=connect_timeout,
            base_url=normalize_vllm_base_url(api_base),
        )
        # Instance attribute on purpose: the capability depends on how this
        # particular server was launched, not on the class.
        self.supports_native_tools = native_tools

    @get_circuit_breaker(_BREAKER)
    async def generate(
        self, prompt: str, model: str, json_mode: bool = False, **kwargs: Any
    ) -> tuple[str, int]:
        """Chat completion against the vLLM server.

        Args:
            prompt: User message content.
            model: The server's ``--served-model-name`` (or the model path).
            json_mode: If True, requests a ``json_object`` response format.
            **kwargs: Same surface as :meth:`OpenAIProvider.generate`, plus
                ``extra_body`` for vLLM-only sampling parameters.

        Returns:
            tuple[str, int]: Response text and total tokens used.
        """
        result: tuple[str, int] = await _generate(
            self, prompt, model, json_mode, **kwargs
        )
        return result

    @get_circuit_breaker(_BREAKER)
    async def generate_structured(
        self,
        prompt: str,
        model: str,
        *,
        tools: list[LLMToolSpec] | None = None,
        tool_choice: ToolChoice | None = None,
        response_format: ResponseFormat | None = None,
        **kwargs: Any,
    ) -> LLMResult:
        """Native tool calling / guided-decoding structured output.

        Args:
            prompt: User turn.
            model: Model name.
            tools: Tools the model may call.
            tool_choice: Selection policy.
            response_format: Optional structured-output constraint.
            **kwargs: ``system``, ``temperature``, ``max_tokens``.

        Returns:
            LLMResult: text and/or tool calls with token usage.
        """
        result: LLMResult = await _generate_structured(
            self,
            prompt,
            model,
            tools=tools,
            tool_choice=tool_choice,
            response_format=response_format,
            **kwargs,
        )
        return result

    @get_circuit_breaker(_BREAKER)
    async def generate_messages(
        self,
        messages: list[Message],
        model: str,
        *,
        tools: list[LLMToolSpec] | None = None,
        system: str | None = None,
        **kwargs: Any,
    ) -> LLMResult:
        """One turn from a neutral message history.

        Args:
            messages: Conversation so far, oldest first.
            model: Model name.
            tools: Tools the model may call.
            system: System prompt.
            **kwargs: ``tool_choice``, ``response_format`` and the rest of the
                :meth:`generate_structured` surface.

        Returns:
            LLMResult: text and/or tool calls, plus the replayable assistant turn.
        """
        result: LLMResult = await _generate_messages(
            self, messages, model, tools=tools, system=system, **kwargs
        )
        return result

    @get_circuit_breaker(_BREAKER)
    async def generate_stream(
        self, prompt: str, model: str, **kwargs: Any
    ) -> AsyncIterator[tuple[str, int]]:
        """Streaming completion; the terminal chunk carries exact usage.

        Args:
            prompt: User message content.
            model: Model name.
            **kwargs: Passthrough parameters.

        Yields:
            tuple[str, int]: Text chunks and the running token count.
        """
        async for item in _generate_stream(self, prompt, model, **kwargs):
            yield item

    async def generate_image(
        self,
        prompt: str,
        *,
        model: str | None = None,
        size: str | None = None,
        quality: str | None = None,
        **kwargs: Any,
    ) -> GeneratedImage:
        """Refuse image generation: vLLM serves no images endpoint.

        Raising here, before any request, keeps the failure a configuration
        message instead of a 404 from the inherited OpenAI images path.

        Raises:
            LLMProviderError: Always.
        """
        raise LLMProviderError(
            "vLLM does not serve image generation; route images to a provider "
            "that does (e.g. openai)"
        )
