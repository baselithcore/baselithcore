"""
Anthropic Claude provider implementation.
"""

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
from core.services.llm.providers._anthropic_mapping import (
    _apply_tool_cache_control,
    _build_system_param,
    _to_anthropic_tool_choice,
    _to_anthropic_tools,
)
from core.services.llm.thinking import resolve_thinking
from core.services.llm.tool_calling import (
    LLMResult,
    LLMToolSpec,
    ResponseFormat,
    ToolCall,
    ToolChoice,
)

# Provider-specific kwargs handled explicitly (not forwarded verbatim).
_RESERVED_KWARGS = frozenset(
    {"max_tokens", "system", "temperature", "thinking", "effort", "thinking_budget"}
)

logger = get_logger(__name__)


class AnthropicProvider:
    """Anthropic Claude LLM provider (Async)."""

    # Anthropic maps tool specs to its native ``tools`` API and parses
    # ``tool_use`` content blocks back into structured tool calls.
    supports_native_tools: bool = True

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
                tokens_used = estimate_tokens(prompt, model) + estimate_tokens(
                    content, model
                )

            return content, tokens_used

        except Exception as e:
            logger.error(f"Anthropic generation error: {e}")
            raise LLMProviderError(f"Anthropic error: {e}") from e

    @get_circuit_breaker("anthropic_provider")
    async def generate_structured(
        self,
        prompt: str,
        model: str,
        *,
        tools: list[LLMToolSpec] | None = None,
        tool_choice: ToolChoice | None = None,
        response_format: ResponseFormat | None = None,
        **kwargs,
    ) -> LLMResult:
        """
        Generate using Anthropic's native tool-calling / structured-output API.

        Tool specs map to ``tools`` and are selected via ``tool_choice``;
        ``response_format`` maps to ``output_config.format`` (json_schema).
        ``tool_use`` content blocks are parsed back into :class:`ToolCall`.

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
        client = self._ensure_client()
        try:
            create_kwargs: dict[str, Any] = {
                "model": model,
                "max_tokens": kwargs.get("max_tokens", 4096),
                "messages": [{"role": "user", "content": prompt}],
                "system": _build_system_param(kwargs.get("system", "")),
                "temperature": kwargs.get("temperature", 0.7),
            }
            if tools:
                create_kwargs["tools"] = _apply_tool_cache_control(
                    _to_anthropic_tools(tools)
                )
                choice = tool_choice or ToolChoice(mode="auto")
                create_kwargs["tool_choice"] = _to_anthropic_tool_choice(choice)
            if response_format is not None:
                # Modern structured-outputs surface (output_config.format), not
                # the deprecated top-level output_format. Requires a
                # structured-outputs-capable model.
                create_kwargs["output_config"] = {
                    "format": {
                        "type": "json_schema",
                        "schema": response_format.schema,
                    }
                }

            response = await client.messages.create(**create_kwargs)

            text_parts: list[str] = []
            tool_calls: list[ToolCall] = []
            for block in response.content:
                if block.type == "text":
                    text_parts.append(block.text)
                elif block.type == "tool_use":
                    tool_calls.append(
                        ToolCall(
                            id=block.id,
                            name=block.name,
                            # Anthropic returns parsed input; never re-parse.
                            arguments=dict(block.input or {}),
                        )
                    )

            usage = response.usage
            if usage:
                cache_write = getattr(usage, "cache_creation_input_tokens", 0) or 0
                cache_read = getattr(usage, "cache_read_input_tokens", 0) or 0
                tokens_used = (
                    usage.input_tokens + usage.output_tokens + cache_write + cache_read
                )
            else:
                tokens_used = estimate_tokens(prompt, model) + estimate_tokens(
                    "".join(text_parts), model
                )

            text = "".join(text_parts).strip() or None
            return LLMResult(
                text=text,
                tool_calls=tool_calls,
                stop_reason=getattr(response, "stop_reason", None),
                tokens_used=tokens_used,
                native=True,
                raw=response,
            )

        except Exception as e:
            logger.error(f"Anthropic structured generation error: {e}")
            raise LLMProviderError(f"Anthropic error: {e}") from e

    # No @retry on the streaming generators: decorating an async generator
    # never retried anything (errors surface during iteration, outside the
    # wrapper) and retrying a partially consumed stream would duplicate
    # already-yielded events.
    async def generate_structured_stream(
        self,
        prompt: str,
        model: str,
        *,
        tools: list[LLMToolSpec] | None = None,
        tool_choice: ToolChoice | None = None,
        **kwargs,
    ) -> "AsyncIterator[Any]":
        """Stream a structured generation as neutral ``StreamEvent``s.

        Emits ``TextDelta`` per text chunk, ``ToolCallStarted`` /
        ``ToolCallDelta`` while the model writes a tool invocation, then a
        terminal ``StreamEnd`` built from the SDK's accumulated final message
        (parsed tool inputs, exact usage) — identical ``LLMResult`` shape to
        :meth:`generate_structured`.
        """
        # Lazy: stream_events imports the service layer (avoid import cycle).
        from core.services.llm.stream_events import (
            StreamEnd,
            TextDelta,
            ToolCallDelta,
            ToolCallStarted,
        )

        client = self._ensure_client()
        create_kwargs: dict[str, Any] = {
            "model": model,
            "max_tokens": kwargs.get("max_tokens", 4096),
            "messages": [{"role": "user", "content": prompt}],
            "system": _build_system_param(kwargs.get("system", "")),
            "temperature": kwargs.get("temperature", 0.7),
        }
        if tools:
            create_kwargs["tools"] = _apply_tool_cache_control(
                _to_anthropic_tools(tools)
            )
            choice = tool_choice or ToolChoice(mode="auto")
            create_kwargs["tool_choice"] = _to_anthropic_tool_choice(choice)

        try:
            async with client.messages.stream(**create_kwargs) as stream:
                # Track tool_use block ids by content-block index so the
                # partial-JSON deltas can be attributed to their call.
                open_tools: dict[int, str] = {}
                async for event in stream:
                    # The SDK stream yields a wide event union; getattr-based
                    # dispatch keeps this tolerant of SDK additions.
                    ev: Any = event
                    etype = getattr(ev, "type", "")
                    if etype == "content_block_start":
                        block = getattr(ev, "content_block", None)
                        if block is not None and getattr(block, "type", "") == (
                            "tool_use"
                        ):
                            open_tools[getattr(ev, "index", -1)] = block.id
                            yield ToolCallStarted(id=block.id, name=block.name)
                    elif etype == "text_delta":
                        yield TextDelta(ev.text)
                    elif etype == "input_json_delta":
                        call_id = open_tools.get(getattr(ev, "index", -1))
                        if call_id is not None:
                            yield ToolCallDelta(
                                id=call_id,
                                arguments_delta=ev.partial_json,
                            )

                final = await stream.get_final_message()

            text_parts: list[str] = []
            tool_calls: list[ToolCall] = []
            for block in final.content:
                if block.type == "text":
                    text_parts.append(block.text)
                elif block.type == "tool_use":
                    tool_calls.append(
                        ToolCall(
                            id=block.id,
                            name=block.name,
                            arguments=dict(block.input or {}),
                        )
                    )
            usage = final.usage
            cache_write = getattr(usage, "cache_creation_input_tokens", 0) or 0
            cache_read = getattr(usage, "cache_read_input_tokens", 0) or 0
            tokens_used = (
                usage.input_tokens + usage.output_tokens + cache_write + cache_read
            )
            yield StreamEnd(
                LLMResult(
                    text="".join(text_parts).strip() or None,
                    tool_calls=tool_calls,
                    stop_reason=getattr(final, "stop_reason", None),
                    tokens_used=tokens_used,
                    native=True,
                    raw=final,
                )
            )
        except Exception as e:
            logger.error(f"Anthropic structured streaming error: {e}")
            raise LLMProviderError(f"Anthropic streaming error: {e}") from e

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
                tokens = estimate_tokens(prompt, model)
                async for chunk in stream:
                    # Anthropic stream events: TextEvent, ContentBlockStartEvent, etc.
                    # For text content, we want the delta text from 'text_delta' events
                    if chunk.type == "text_delta":
                        text = chunk.text
                        tokens += estimate_tokens(text, model)
                        yield text, tokens

        except Exception as e:
            logger.error(f"Anthropic streaming error: {e}")
            raise LLMProviderError(f"Anthropic streaming error: {e}") from e
