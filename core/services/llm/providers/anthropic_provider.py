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
    anthropic = None  # type: ignore[assignment]

from core.resilience.circuit_breaker import get_circuit_breaker
from core.services.llm.cost_control import estimate_tokens
from core.services.llm.errors import LLMRefusalError, map_provider_exception
from core.services.llm.exceptions import LLMProviderError, describe_exception
from core.services.llm.messages import Message
from core.services.llm.providers._anthropic_mapping import (
    _build_system_param,
)
from core.services.llm.providers._anthropic_request import (
    RESERVED_KWARGS,
    build_request_kwargs,
    forwardable_kwargs,
)
from core.services.llm.providers._anthropic_response import check_stop_reason
from core.services.llm.stop_reasons import (
    MAX_PAUSE_TURN_CONTINUATIONS,
    is_paused,
)
from core.services.llm.tool_calling import (
    LLMResult,
    LLMToolSpec,
    ResponseFormat,
    ToolChoice,
)
from core.services.llm.usage import Usage

# Kept under the historical private name: external call sites (and tests)
# import it to know which kwargs the provider consumes itself.
_RESERVED_KWARGS = RESERVED_KWARGS

logger = get_logger(__name__)


class AnthropicProvider:
    """Anthropic Claude LLM provider (Async)."""

    # Anthropic maps tool specs to its native ``tools`` API and parses
    # ``tool_use`` content blocks back into structured tool calls.
    supports_native_tools: bool = True

    # Anthropic's Messages API is the native shape for an agentic loop:
    # tool_use/tool_result blocks correlate by id, failures carry is_error,
    # thinking replays verbatim, and an append-only history keeps the prompt
    # prefix cacheable across iterations.
    supports_messages: bool = True

    #: Serving backends. ``api`` (default) needs an Anthropic key; ``bedrock``
    #: and ``vertex`` use the Anthropic SDK's native cloud clients, which
    #: authenticate through the cloud's own credential chain (AWS SigV4 /
    #: Google ADC) — no Anthropic API key.
    BACKENDS = ("api", "bedrock", "vertex")

    def __init__(
        self,
        api_key: str | SecretStr | None,
        request_timeout: float = 120.0,
        connect_timeout: float = 5.0,
        backend: str = "api",
        aws_region: str | None = None,
        vertex_project: str | None = None,
        vertex_region: str | None = None,
        betas: list[str] | None = None,
        extra_headers: dict[str, str] | None = None,
        extra_body: dict[str, Any] | None = None,
    ):
        """
        Initialize Anthropic provider.

        Args:
            api_key: Anthropic API key (``api`` backend only; raw ``str`` or
                wrapped ``SecretStr``). Ignored by ``bedrock``/``vertex``.
            request_timeout: Total per-request deadline in seconds.
            connect_timeout: TCP connect deadline in seconds.
            backend: ``api`` | ``bedrock`` | ``vertex``.
            aws_region: Bedrock region; ``None`` defers to the SDK's
                ``AWS_REGION`` resolution.
            vertex_project: Vertex project id; ``None`` defers to
                ``GOOGLE_CLOUD_PROJECT``.
            vertex_region: Vertex region; ``None`` defers to
                ``CLOUD_ML_REGION``.
            betas: Beta feature flags applied to every call. When set, requests
                route through ``client.beta.messages`` and carry ``betas``.
                Per-call ``betas=`` are merged on top.
            extra_headers: Headers added to every request (gateway routing,
                cost attribution, org-specific tags).
            extra_body: Extra top-level request fields, for API surface this
                provider does not model yet — the escape hatch that keeps a new
                API feature from requiring a provider release.
        """
        if backend not in self.BACKENDS:
            raise LLMProviderError(
                f"Unknown Anthropic backend {backend!r}; expected one of "
                f"{self.BACKENDS}"
            )
        if backend == "api" and not api_key:
            raise LLMProviderError("Anthropic API key is required")

        if anthropic is None:
            raise LLMProviderError(
                "Anthropic library is not installed. Run 'pip install anthropic'"
            )

        # Keep the credential wrapped so it never appears in repr()/tracebacks/
        # Sentry frames; unwrap only at the SDK boundary in _ensure_client.
        self._api_key: SecretStr | None = (
            api_key
            if isinstance(api_key, SecretStr) or api_key is None
            else SecretStr(api_key)
        )
        self._request_timeout = request_timeout
        self._connect_timeout = connect_timeout
        self._backend = backend
        self._aws_region = aws_region
        self._vertex_project = vertex_project
        self._vertex_region = vertex_region
        self._betas: list[str] = list(betas or [])
        self._extra_headers: dict[str, str] = dict(extra_headers or {})
        self._extra_body: dict[str, Any] = dict(extra_body or {})
        self.client: anthropic.AsyncAnthropic | None = None

    def _ensure_client(self) -> anthropic.AsyncAnthropic:
        """
        Lazily initialize the AsyncAnthropic client.

        Returns:
            anthropic.AsyncAnthropic: The initialized Anthropic client.
        """
        if self.client is not None:
            return self.client

        from core.services.llm.providers._anthropic_client import build_async_client

        self.client = build_async_client(
            anthropic,
            backend=self._backend,
            api_key=self._api_key.get_secret_value() if self._api_key else None,
            request_timeout=self._request_timeout,
            connect_timeout=self._connect_timeout,
            aws_region=self._aws_region,
            vertex_project=self._vertex_project,
            vertex_region=self._vertex_region,
        )
        logger.info("Initialized Anthropic provider (Async, backend=%s)", self._backend)
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

    def _messages_api(self, kwargs: dict[str, Any]) -> tuple[Any, dict[str, Any]]:
        """Resolve the messages endpoint and its transport-level extras.

        Beta features live on ``client.beta.messages`` and are selected per
        request with ``betas=``; without any, the stable endpoint is used so a
        beta header is never sent by accident.

        Args:
            kwargs: Caller kwargs (``betas``, ``extra_headers``,
                ``extra_body``).

        Returns:
            tuple: The endpoint object and the extras to splat into the call.
        """
        client = self._ensure_client()
        extra: dict[str, Any] = {}
        headers = {**self._extra_headers, **(kwargs.get("extra_headers") or {})}
        if headers:
            extra["extra_headers"] = headers
        body = {**self._extra_body, **(kwargs.get("extra_body") or {})}
        if body:
            extra["extra_body"] = body

        betas = list(self._betas)
        betas.extend(b for b in (kwargs.get("betas") or []) if b not in betas)
        if betas:
            extra["betas"] = betas
            return client.beta.messages, extra
        return client.messages, extra

    async def _create(
        self, kwargs: dict[str, Any], create_kwargs: dict[str, Any]
    ) -> tuple[Any, list[Any], Usage]:
        """Run one turn, resuming ``pause_turn`` up to the continuation budget.

        A long-running turn can come back with ``stop_reason == "pause_turn"``
        and *no* end to the work: the API expects the conversation to be resent
        with the partial assistant content appended. Returning it as a finished
        answer silently truncates whatever the model was doing, so the loop is
        closed here — the only layer holding the wire-level message list.

        Args:
            kwargs: Caller kwargs (for betas/headers routing).
            create_kwargs: Fully-shaped request for ``messages.create``.

        A continuation that fails is NOT fatal: the turns already completed
        were billed and their content is real, so the partial answer is
        returned with ``stop_reason == "pause_turn"`` (the caller can see it is
        unfinished) instead of discarding paid-for work. A failure on the first
        call has nothing to fall back on and propagates.

        Returns:
            tuple: the final response, every content block across turns, and
            the merged usage (each continuation is billed separately).
        """
        api, extra = self._messages_api(kwargs)
        messages = list(create_kwargs.get("messages") or [])
        blocks: list[Any] = []
        usage = Usage()
        response: Any = None
        #: The last response that asked to be resumed; the answer a failed
        #: continuation falls back to (always set once turn > 0).
        paused_response: Any = None

        for turn in range(MAX_PAUSE_TURN_CONTINUATIONS + 1):
            try:
                response = await api.create(
                    **{**create_kwargs, "messages": messages}, **extra
                )
            except Exception as exc:
                if turn == 0:
                    raise
                logger.warning(
                    "anthropic_pause_turn_continuation_failed",
                    extra={
                        "model": create_kwargs.get("model"),
                        "turn": turn,
                        "error": describe_exception(exc),
                    },
                )
                return paused_response, blocks, usage
            turn_blocks = list(response.content or [])
            blocks.extend(turn_blocks)
            usage = usage.merge(Usage.from_anthropic(getattr(response, "usage", None)))
            if not is_paused(getattr(response, "stop_reason", None)):
                return response, blocks, usage
            # Held so a failed continuation can still answer with the paused
            # stop reason rather than a success the model never reached.
            paused_response = response
            messages = [*messages, {"role": "assistant", "content": turn_blocks}]

        logger.warning(
            "anthropic_pause_turn_budget_exhausted",
            extra={
                "model": create_kwargs.get("model"),
                "continuations": MAX_PAUSE_TURN_CONTINUATIONS,
            },
        )
        return response, blocks, usage

    @staticmethod
    def _record_usage(kwargs: dict[str, Any], usage: Usage) -> None:
        """Publish exact usage to a caller-supplied sink, when there is one.

        The legacy ``generate`` contract returns ``(text, total_tokens)``, from
        which the orchestration layer used to re-derive the input/output split
        by subtracting a tokenizer estimate — mispricing every call, since
        output bills at up to 5x input. An opt-in list lets a caller receive
        the metered record without changing that return type.
        """
        sink = kwargs.get("usage_sink")
        if isinstance(sink, list):
            sink.append(usage)

    # Single retry owner is LLMService._generate_with_retry (rate-limit
    # aware). A provider-level blanket retry on Exception would multiply
    # attempts (3x3 upstream calls per request) and pointlessly retry
    # non-transient failures (bad key, invalid request). The circuit
    # breaker stays: failure isolation, not retry.
    @get_circuit_breaker("anthropic_provider")
    async def generate(
        self, prompt: str, model: str, json_mode: bool = False, **kwargs: Any
    ) -> tuple[str, int]:
        """
        Generate a response using Anthropic Claude.

        Args:
            prompt: Input prompt
            model: Model name (e.g., 'claude-opus-5')
            json_mode: Whether to request JSON output (handled via system prompt)
            **kwargs: ``system``, ``max_tokens``, sampling parameters (forwarded
                only where the family accepts them), ``effort`` /
                ``thinking_budget``, ``betas``, ``extra_headers``,
                ``extra_body``, ``allow_refusal`` and ``usage_sink``.

        Returns:
            Tuple of (response_text, tokens_used)

        Raises:
            LLMRefusalError: The model declined to answer and the caller did
                not pass ``allow_refusal=True``.
            LLMProviderError: Any other provider failure, mapped to the neutral
                taxonomy in :mod:`core.services.llm.errors`.
        """
        try:
            system_prompt = kwargs.get("system", "")
            if json_mode and "json" not in system_prompt.lower():
                system_prompt += "\nOutput MUST be a valid JSON object."

            create_kwargs: dict[str, Any] = {
                "model": model,
                "messages": [{"role": "user", "content": prompt}],
                "system": _build_system_param(system_prompt),
                **build_request_kwargs(model, kwargs),
                **forwardable_kwargs(kwargs),
            }
            response, blocks, usage = await self._create(kwargs, create_kwargs)

            content = "".join(
                block.text for block in blocks if block.type == "text"
            ).strip()

            if usage.is_empty:
                usage = Usage.estimate(
                    estimate_tokens(prompt, model), estimate_tokens(content, model)
                )
            self._record_usage(kwargs, usage)
            check_stop_reason(response, model=model, kwargs=kwargs)
            return content, usage.total

        except LLMRefusalError:
            # The call succeeded and was billed; the model simply declined.
            # ``raise_for_refusal`` already logged it at warning with the
            # refusal category, so an error-level "generation error" here
            # would both double-report it and misclassify it.
            raise
        except Exception as e:
            logger.error(f"Anthropic generation error: {describe_exception(e)}")
            raise map_provider_exception(e, provider="Anthropic") from e

    @get_circuit_breaker("anthropic_provider")
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
        """Generate using Anthropic's native tool-calling / structured API.

        Body lives in ``_anthropic_structured`` (module size cap).

        Args:
            prompt: User turn.
            model: Model name.
            tools: Tools the model may call.
            tool_choice: Selection policy (defaults to auto when tools present).
            response_format: Optional structured-output constraint.
            **kwargs: Same surface as :meth:`generate`.

        Returns:
            LLMResult: text and/or structured tool calls, with the metered
            usage split and the stop reason the caller needs to interpret it.
        """
        from core.services.llm.providers._anthropic_structured import (
            generate_structured,
        )

        return await generate_structured(
            self,
            prompt,
            model,
            tools=tools,
            tool_choice=tool_choice,
            response_format=response_format,
            **kwargs,
        )

    @get_circuit_breaker("anthropic_provider")
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

        Body lives in ``_anthropic_messages`` (module size cap).

        Args:
            messages: Conversation so far, oldest first.
            model: Model name.
            tools: Tools the model may call.
            system: System prompt; carries the prompt-cache breakpoint.
            **kwargs: ``tool_choice``, ``response_format``, and the same
                surface as :meth:`generate`.

        Returns:
            LLMResult: text and/or tool calls, plus ``message`` — the assistant
            turn verbatim, thinking blocks included, for replay on the next
            iteration.
        """
        from core.services.llm.providers._anthropic_messages import generate_messages

        return await generate_messages(
            self, messages, model, tools=tools, system=system, **kwargs
        )

    # No @retry on the streaming generators: decorating an async generator
    # never retried anything (errors surface during iteration, outside the
    # wrapper) and retrying a partially consumed stream would duplicate
    # already-yielded events. Bodies live in ``_anthropic_streaming`` for the
    # module size cap.
    async def generate_structured_stream(
        self,
        prompt: str,
        model: str,
        *,
        tools: list[LLMToolSpec] | None = None,
        tool_choice: ToolChoice | None = None,
        **kwargs: Any,
    ) -> AsyncIterator[Any]:
        """Stream a structured generation as neutral ``StreamEvent``s.

        Args:
            prompt: User turn.
            model: Model name.
            tools: Tools the model may call.
            tool_choice: Selection policy (defaults to auto when tools present).
            **kwargs: Same surface as :meth:`generate`.

        Yields:
            ``TextDelta`` / ``ToolCallStarted`` / ``ToolCallDelta`` events, then
            exactly one ``StreamEnd`` carrying the authoritative result.
        """
        from core.services.llm.providers._anthropic_streaming import stream_structured

        async for event in stream_structured(
            self, prompt, model, tools=tools, tool_choice=tool_choice, **kwargs
        ):
            yield event

    @get_circuit_breaker("anthropic_provider")
    async def generate_stream(
        self, prompt: str, model: str, **kwargs: Any
    ) -> AsyncIterator[tuple[str, int]]:
        """
        Generate a streaming response using Anthropic Claude.

        Args:
            prompt: Input prompt
            model: Model name
            **kwargs: Same surface as :meth:`generate`.

        Yields:
            Tuples of (chunk_text, accumulated_tokens); the count becomes the
            provider's metered figure once the usage events arrive.
        """
        from core.services.llm.providers._anthropic_streaming import stream_text

        async for chunk in stream_text(self, prompt, model, **kwargs):
            yield chunk
