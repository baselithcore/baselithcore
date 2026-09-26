"""
OpenAI LLM provider implementation.

This module provides the concrete implementation for interacting with
OpenAI's API, supporting both standard chat completions and real-time streaming.
"""

from core.observability.logging import get_logger

try:
    import openai
except ImportError:
    openai = None  # type: ignore[assignment]

from collections.abc import AsyncIterator
from typing import TYPE_CHECKING, Any, cast

from pydantic import SecretStr

if TYPE_CHECKING:
    from openai import AsyncOpenAI

from core.resilience.circuit_breaker import get_circuit_breaker
from core.services.llm.cost_control import estimate_tokens
from core.services.llm.errors import LLMRefusalError, map_provider_exception
from core.services.llm.exceptions import LLMProviderError, describe_exception
from core.services.llm.images import GeneratedImage
from core.services.llm.messages import Message
from core.services.llm.providers._openai_images import (
    DEFAULT_IMAGE_MODEL,
    DEFAULT_IMAGE_SIZE,
)
from core.services.llm.providers._openai_mapping import (
    to_openai_tool_choice,
    to_openai_tools,
)
from core.services.llm.providers._openai_request import (
    extract_usage,
    refusal_of,
)
from core.services.llm.providers._openai_request import (
    request_kwargs as _forwardable_kwargs,
)
from core.services.llm.stop_reasons import (
    STOP_REFUSAL,
    TRUNCATION_STOP_REASONS,
    raise_for_refusal,
)
from core.services.llm.tool_calling import (
    LLMResult,
    LLMToolSpec,
    ResponseFormat,
    ToolChoice,
)
from core.services.llm.usage import Usage

logger = get_logger(__name__)

# Image defaults live with the image path; kept under the historical
# private names for callers that import them.
_DEFAULT_IMAGE_MODEL = DEFAULT_IMAGE_MODEL
_DEFAULT_IMAGE_SIZE = DEFAULT_IMAGE_SIZE


# Tool/response mapping lives in ``_openai_mapping`` (shared with the
# structured and message paths); kept under the historical private names for
# call sites that import them.
_to_openai_tools = to_openai_tools
_to_openai_tool_choice = to_openai_tool_choice


class OpenAIProvider:
    """
    Asynchronous OpenAI API provider.

    Manages an internal AsyncOpenAI client and maps generic LLM requests
    to OpenAI-specific API calls.
    """

    # OpenAI maps tool specs to its native function-calling API and parses
    # ``message.tool_calls`` back into structured tool calls.
    supports_native_tools: bool = True

    # Chat Completions is already a message API: tool calls and their results
    # travel as correlated messages, and an append-only history keeps the
    # prefix stable for OpenAI's automatic prompt caching.
    supports_messages: bool = True

    #: Name used in error messages and logs. Subclasses speaking the same
    #: protocol to another server (``VLLMProvider``) override it, so an outage
    #: there is never reported as OpenAI's.
    provider_label: str = "OpenAI"

    def __init__(
        self,
        api_key: str | SecretStr,
        request_timeout: float = 120.0,
        connect_timeout: float = 5.0,
        base_url: str | None = None,
    ):
        """
        Initialize the OpenAI provider.

        Args:
            api_key: Secret API key (raw ``str`` or wrapped ``SecretStr``).
            request_timeout: Total per-request deadline in seconds.
            connect_timeout: TCP connect deadline in seconds.
            base_url: Optional custom endpoint for OpenAI-compatible servers
                (Azure OpenAI via gateway, vLLM, LiteLLM, OpenRouter, ...).
                ``None`` keeps the SDK default (api.openai.com).
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
        self._base_url = base_url
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
            client_kwargs: dict[str, Any] = {
                "api_key": self._api_key.get_secret_value(),
                "max_retries": 0,
                "timeout": httpx.Timeout(
                    self._request_timeout, connect=self._connect_timeout
                ),
            }
            if self._base_url:
                client_kwargs["base_url"] = self._base_url
            self.client = cast("AsyncOpenAI", openai.AsyncOpenAI(**client_kwargs))
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
        self, prompt: str, model: str, json_mode: bool = False, **kwargs: Any
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
            # Cross-provider hints ("effort", "allow_refusal", ...) and the
            # renamed token cap are handled by the shaper; Chat Completions
            # rejects unknown kwargs, so nothing else may pass through.
            request_kwargs = _forwardable_kwargs(kwargs)

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

            choice = response.choices[0]
            message = choice.message
            raw_content = message.content
            content = raw_content.strip() if raw_content else ""

            usage, reported_total = extract_usage(response)
            self._record_usage(kwargs, usage)
            tokens_used = (
                usage.total
                or reported_total
                or (estimate_tokens(prompt) + estimate_tokens(content))
            )

            refusal = refusal_of(message)
            if refusal and not kwargs.get("allow_refusal", False):
                raise_for_refusal(STOP_REFUSAL, {"explanation": refusal}, model=model)
            finish_reason = getattr(choice, "finish_reason", None)
            if (
                isinstance(finish_reason, str)
                and finish_reason in TRUNCATION_STOP_REASONS
            ):
                logger.warning(
                    "llm_response_truncated",
                    extra={"model": model, "stop_reason": finish_reason},
                )

            return content, tokens_used

        except LLMRefusalError:
            # The call succeeded and was billed; the model simply declined.
            # ``raise_for_refusal`` already logged it at warning with the
            # refusal detail, so an error-level "generation error" here would
            # both double-report it and misclassify it.
            raise
        except Exception as e:
            logger.error(
                f"{self.provider_label} generation error: {describe_exception(e)}"
            )
            raise map_provider_exception(e, provider=self.provider_label) from e

    @staticmethod
    def _record_usage(kwargs: dict[str, Any], usage: Usage) -> None:
        """Publish exact usage to a caller-supplied sink, when there is one.

        The ``(text, total_tokens)`` return type cannot carry the input/output
        split, and re-deriving it downstream from a tokenizer estimate
        misprices every call. An opt-in list lets a caller receive the metered
        record without changing that contract.
        """
        sink = kwargs.get("usage_sink")
        if isinstance(sink, list) and not usage.is_empty:
            sink.append(usage)

    @get_circuit_breaker("openai_provider")
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
        """
        Generate using OpenAI's native function-calling / structured outputs.

        Body lives in ``_openai_structured`` (module size cap). Tool specs map
        to ``tools`` (function type) selected via ``tool_choice``;
        ``response_format`` maps to a ``json_schema`` response format.
        ``message.tool_calls`` are parsed back into :class:`ToolCall` (the
        function ``arguments`` JSON string is parsed there — callers never
        re-parse).

        Args:
            prompt: User turn.
            model: Model name.
            tools: Tools the model may call.
            tool_choice: Selection policy (defaults to auto when tools present).
            response_format: Optional structured-output constraint.
            **kwargs: ``system``, ``temperature``, ``max_tokens``.

        Returns:
            LLMResult: text and/or structured tool calls with token usage.
        """
        from core.services.llm.providers._openai_structured import generate_structured

        return await generate_structured(
            self,
            prompt,
            model,
            tools=tools,
            tool_choice=tool_choice,
            response_format=response_format,
            **kwargs,
        )

    @get_circuit_breaker("openai_provider")
    async def generate_messages(
        self,
        messages: list[Message],
        model: str,
        *,
        tools: list[LLMToolSpec] | None = None,
        system: str | None = None,
        **kwargs: Any,
    ) -> LLMResult:
        """Generate one turn from a neutral message history.

        Body lives in ``_openai_messages`` (module size cap).

        Args:
            messages: Conversation so far, oldest first.
            model: Model name.
            tools: Tools the model may call.
            system: System prompt, sent as the leading ``system`` message.
            **kwargs: ``tool_choice``, ``response_format``, and the same
                surface as :meth:`generate_structured`.

        Returns:
            LLMResult: text and/or tool calls, plus ``message`` — the assistant
            turn as neutral blocks, for replay on the next iteration.
        """
        from core.services.llm.providers._openai_messages import generate_messages

        return await generate_messages(
            self, messages, model, tools=tools, system=system, **kwargs
        )

    @get_circuit_breaker("openai_provider")
    async def generate_image(
        self,
        prompt: str,
        *,
        model: str | None = None,
        size: str | None = None,
        quality: str | None = None,
        **kwargs: Any,
    ) -> GeneratedImage:
        """Generate one image and return its bytes.

        Body lives in ``_openai_images`` (module size cap).

        Args:
            prompt: The whole brief for the image.
            model: Image model; ``gpt-image-1`` when None.
            size: Provider size string; a landscape cover when None.
            quality: Quality tier; omitted from the request when None.
            **kwargs: Passthrough parameters.

        Returns:
            The decoded image and the model that produced it.
        """
        from core.services.llm.providers._openai_images import generate_image

        return await generate_image(
            self._ensure_client(),
            prompt,
            model=model,
            size=size,
            quality=quality,
            **kwargs,
        )

    # No @retry here either: decorating an async generator never retried
    # anything (errors surface during iteration, outside the wrapper) —
    # the decorator was dead code. Retrying a partially consumed stream
    # would also duplicate already-yielded chunks.
    @get_circuit_breaker("openai_provider")
    async def generate_stream(
        self, prompt: str, model: str, **kwargs: Any
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
            request_kwargs = _forwardable_kwargs(kwargs)

            system_prompt = kwargs.get("system", "")
            messages = []
            if system_prompt:
                messages.append({"role": "system", "content": system_prompt})
            messages.append({"role": "user", "content": prompt})

            stream = await client.chat.completions.create(
                model=model,
                messages=messages,
                stream=True,
                # Ask for the terminal usage chunk so the final count is the
                # provider's exact billing figure, not a tokenizer estimate.
                stream_options={"include_usage": True},
                **request_kwargs,
            )

            # During streaming we estimate tokens per-delta (metadata is not
            # available mid-stream); the terminal usage chunk then replaces the
            # running estimate with the exact billed total.
            tokens = estimate_tokens(prompt)
            async for chunk in stream:
                # With include_usage the final chunk carries usage and an empty
                # choices list — guard before indexing.
                if chunk.choices:
                    content = str(chunk.choices[0].delta.content or "")
                    if content:
                        tokens += estimate_tokens(content)
                        yield content, tokens
                usage = getattr(chunk, "usage", None)
                total = getattr(usage, "total_tokens", None) if usage else None
                # Only the terminal chunk carries a real integer count; the
                # strict type check also keeps this inert for test doubles.
                if isinstance(total, int) and total > 0:
                    tokens = total
                    yield "", tokens

        except Exception as e:
            logger.error(
                f"{self.provider_label} streaming error: {describe_exception(e)}"
            )
            raise map_provider_exception(
                e, provider=self.provider_label, action="streaming"
            ) from e
