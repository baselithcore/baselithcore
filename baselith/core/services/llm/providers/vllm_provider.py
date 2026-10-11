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

* **Answers, not reasoning.** A thinking model (Qwen3, DeepSeek-R1) on a
  server started without ``--reasoning-parser`` returns its reasoning inline,
  closed by ``</think>``. Every text this provider returns — whole or streamed —
  goes through :mod:`baselith.core.services.llm.reasoning_text`, which drops it; on a
  server that parses reasoning itself the filter is inert.

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

from baselith.core.observability.logging import get_logger
from baselith.core.resilience.circuit_breaker import get_circuit_breaker
from baselith.core.services.llm._message_types import TextBlock
from baselith.core.services.llm.cost_control import estimate_tokens
from baselith.core.services.llm.errors import map_provider_exception
from baselith.core.services.llm.exceptions import LLMProviderError, describe_exception
from baselith.core.services.llm.images import GeneratedImage
from baselith.core.services.llm.messages import Message
from baselith.core.services.llm.providers._openai_request import request_kwargs
from baselith.core.services.llm.providers._vllm_routing import VLLMRoutingMixin
from baselith.core.services.llm.providers.openai_provider import OpenAIProvider
from baselith.core.services.llm.reasoning_text import (
    ReasoningStreamFilter,
    can_stream_live,
    strip_reasoning,
)
from baselith.core.services.llm.tool_calling import (
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

logger = get_logger(__name__)

# The parent's methods are wrapped by the ``openai_provider`` breaker. Calling
# the unwrapped functions keeps the protocol logic in one place while the
# overrides below count every outcome against vLLM's own breaker instead.
_generate = inspect.unwrap(OpenAIProvider.generate)
_generate_structured = inspect.unwrap(OpenAIProvider.generate_structured)
_generate_messages = inspect.unwrap(OpenAIProvider.generate_messages)


def _clean(result: LLMResult) -> LLMResult:
    """*result* with inline reasoning dropped from its text and text blocks."""
    if result.text:
        result.text = strip_reasoning(result.text)
    if result.message is not None:
        for block in result.message.content:
            if isinstance(block, TextBlock):
                block.text = strip_reasoning(block.text)
    return result


def _thinking_off_for_json(kwargs: dict[str, Any]) -> dict[str, Any]:
    """*kwargs* with the chat template's thinking switched off, for JSON mode.

    ``response_format: json_object`` constrains every generated token to JSON,
    but a thinking template (Qwen3) opens ``<think>`` in the prompt: the model's
    reasoning is then forced *inside* the object, and the reply comes back as
    garbled JSON. ``enable_thinking: false`` closes the block in the template so
    the constrained output is the answer alone. A caller that set
    ``enable_thinking`` itself keeps its choice; any other ``extra_body`` is
    merged, never replaced, and never mutated. Templates without the variable
    ignore it.
    """
    extra_body = dict(kwargs.get("extra_body") or {})
    template = dict(extra_body.get("chat_template_kwargs") or {})
    if "enable_thinking" in template:
        return kwargs
    template["enable_thinking"] = False
    extra_body["chat_template_kwargs"] = template
    return {**kwargs, "extra_body": extra_body}


def _reasoning_of(delta: Any) -> str | None:
    """The reasoning a parsing server streams in its own delta field."""
    for name in ("reasoning_content", "reasoning"):
        value = getattr(delta, name, None)
        if isinstance(value, str) and value:
            return value
    return None


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


class VLLMProvider(VLLMRoutingMixin, OpenAIProvider):
    """Provider for self-hosted vLLM OpenAI-compatible servers.

    One server, or several (``LLM_VLLM_ENDPOINTS``) — then each call goes to the
    server whose ``/v1/models`` serves the requested model.
    """

    provider_label: str = "vLLM"

    def __init__(
        self,
        api_base: str | None,
        api_key: str | SecretStr | None = None,
        request_timeout: float = 120.0,
        connect_timeout: float = 5.0,
        native_tools: bool = True,
        endpoints: list[str] | None = None,
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
            native_tools: Whether the servers were started with
                ``--enable-auto-tool-choice``.
            endpoints: Every server, in priority order (``LLM_VLLM_ENDPOINTS``);
                calls are routed among them by model. ``api_base`` joins the
                list when it is not already on it.

        Raises:
            LLMProviderError: When no endpoint is configured.
        """
        routed = [normalize_vllm_base_url(e) for e in (endpoints or []) if e.strip()]
        if api_base and api_base.strip():
            single = normalize_vllm_base_url(api_base)
            if single not in routed:
                routed.append(single)
        if not routed:
            raise LLMProviderError(
                "vLLM endpoint is required: set LLM_VLLM_ENDPOINTS (several "
                "servers) or LLM_VLLM_API_BASE (e.g. http://gpu-host:8000/v1)"
            )
        key: str | SecretStr = api_key if api_key else VLLM_NO_KEY
        if isinstance(key, SecretStr) and not key.get_secret_value().strip():
            key = VLLM_NO_KEY
        super().__init__(
            api_key=key,
            request_timeout=request_timeout,
            connect_timeout=connect_timeout,
            base_url=routed[0],
        )
        self._endpoints = list(dict.fromkeys(routed))
        self._clients = {}
        # Instance attribute on purpose: the capability depends on how this
        # particular server was launched, not on the class.
        self.supports_native_tools = native_tools

    def _routing_key(self) -> str | None:
        """The key catalog probes send; none for a keyless server."""
        key = self._api_key.get_secret_value()
        return None if key == VLLM_NO_KEY else key

    @get_circuit_breaker(_BREAKER)
    async def generate(
        self, prompt: str, model: str, json_mode: bool = False, **kwargs: Any
    ) -> tuple[str, int]:
        """Chat completion against the vLLM server.

        Args:
            prompt: User message content.
            model: The server's ``--served-model-name`` (or the model path).
            json_mode: If True, requests a ``json_object`` response format
                and turns the chat template's thinking off (unless the caller
                set ``chat_template_kwargs.enable_thinking``).
            **kwargs: Same surface as :meth:`OpenAIProvider.generate`, plus
                ``extra_body`` for vLLM-only sampling parameters.

        Returns:
            tuple[str, int]: Response text and total tokens used.
        """
        if json_mode:
            kwargs = _thinking_off_for_json(kwargs)
        with self._routed(await self._endpoint_for(model)):
            text, tokens = await _generate(self, prompt, model, json_mode, **kwargs)
        return strip_reasoning(text), tokens

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
        with self._routed(await self._endpoint_for(model)):
            result: LLMResult = await _generate_structured(
                self,
                prompt,
                model,
                tools=tools,
                tool_choice=tool_choice,
                response_format=response_format,
                **kwargs,
            )
        return _clean(result)

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
        with self._routed(await self._endpoint_for(model)):
            result: LLMResult = await _generate_messages(
                self, messages, model, tools=tools, system=system, **kwargs
            )
        return _clean(result)

    @get_circuit_breaker(_BREAKER)
    async def generate_messages_stream(
        self,
        messages: list[Message],
        model: str,
        *,
        tools: list[LLMToolSpec] | None = None,
        system: str | None = None,
        **kwargs: Any,
    ) -> AsyncIterator[Any]:
        """Stream one turn; inline reasoning never reaches the text.

        A model not known to be free of inline reasoning (see
        :func:`~baselith.core.services.llm.reasoning_text.can_stream_live`) holds its
        answer until ``</think>`` (the reasoning is dropped, the answer then
        streams live); a known non-reasoner streams from the first chunk.

        Args:
            messages: Conversation so far, oldest first.
            model: Model name.
            tools: Tools the model may call (delivered whole, never streamed).
            system: System prompt.
            **kwargs: Same surface as :meth:`generate_messages`.

        Yields:
            ``TextDelta`` per visible text chunk, then one ``StreamEnd``.
        """
        from baselith.core.services.llm.providers._openai_messages_stream import (
            stream_messages,
        )

        # Held as a client, not a context variable: see ``generate_stream``.
        client = self._client_for(await self._endpoint_for(model))
        from baselith.core.config.services import get_llm_config

        live = can_stream_live(model, get_llm_config().vllm_live_stream_models)
        async for event in stream_messages(
            client,
            messages,
            model,
            tools=tools,
            system=system,
            guard=ReasoningStreamFilter(eager=live),
            **kwargs,
        ):
            yield event

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
        # Resolved up front and held as a client, not a context variable: an
        # async generator may be resumed (or closed) from another context.
        client = self._client_for(await self._endpoint_for(model))
        try:
            system_prompt = kwargs.get("system", "")
            messages: list[dict[str, str]] = []
            if system_prompt:
                messages.append({"role": "system", "content": system_prompt})
            messages.append({"role": "user", "content": prompt})
            stream = await client.chat.completions.create(
                model=model,
                messages=messages,
                stream=True,
                stream_options={"include_usage": True},
                **request_kwargs(kwargs),
            )
            guard = ReasoningStreamFilter()
            tokens = estimate_tokens(prompt)
            billed: int | None = None
            async for chunk in stream:
                if chunk.choices:
                    delta = chunk.choices[0].delta
                    # A server that parses reasoning streams it apart: the
                    # content is clean, so nothing is held back from here on.
                    held = guard.passthrough() if _reasoning_of(delta) else ""
                    text = held + guard.feed(str(getattr(delta, "content", "") or ""))
                    if text:
                        tokens += estimate_tokens(text)
                        yield text, tokens
                total = getattr(getattr(chunk, "usage", None), "total_tokens", None)
                if isinstance(total, int) and total > 0:
                    billed = total
            tail = guard.finish()
            if tail:
                tokens += estimate_tokens(tail)
                yield tail, tokens
            if billed is not None:
                yield "", billed
        except Exception as e:
            logger.error(f"vLLM streaming error: {describe_exception(e)}")
            raise map_provider_exception(
                e, provider=self.provider_label, action="streaming"
            ) from e

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
