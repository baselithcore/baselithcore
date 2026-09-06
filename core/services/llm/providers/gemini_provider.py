"""Google Gemini provider (google-genai SDK).

Optional dependency: install with ``pip install "baselith-core[gemini]"``.
The import is guarded — the provider raises :class:`LLMProviderError` at
call time when the SDK is absent, so the core package never hard-depends
on ``google-genai``.

Native tool-calling and structured outputs are supported: tool specs map to
Gemini ``function_declarations`` and a ``ResponseFormat`` maps to
``response_mime_type="application/json"`` + ``response_schema``.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

from core.observability.logging import get_logger
from core.resilience.circuit_breaker import get_circuit_breaker
from core.services.llm.exceptions import LLMProviderError, describe_exception
from core.services.llm.tool_calling import (
    LLMResult,
    LLMToolSpec,
    ResponseFormat,
    ToolCall,
    ToolChoice,
)

logger = get_logger(__name__)


def _to_function_declarations(tools: list[LLMToolSpec]) -> list[dict[str, Any]]:
    """Map neutral tool specs to Gemini function declarations."""
    return [
        {
            "name": t.name,
            "description": t.description,
            "parameters": t.parameters,
        }
        for t in tools
    ]


class GeminiProvider:
    """LLM provider backed by the Google Gemini API (google-genai SDK)."""

    supports_native_tools: bool = True

    def __init__(
        self,
        api_key: str,
        request_timeout: float = 120.0,
    ):
        """Initialize the provider (client built lazily on first call)."""
        self._api_key = api_key
        self._request_timeout = request_timeout
        self._client: Any | None = None

    def _ensure_client(self) -> Any:
        """Build the async google-genai client on first use."""
        if self._client is None:
            try:
                from google import genai
            except ImportError as exc:
                raise LLMProviderError(
                    "google-genai is not installed — "
                    'install with: pip install "baselith-core[gemini]"'
                ) from exc
            # http_options timeout is milliseconds in google-genai.
            self._client = genai.Client(
                api_key=self._api_key,
                http_options={"timeout": int(self._request_timeout * 1000)},
            )
        return self._client

    async def close(self) -> None:
        """Release the underlying client (no persistent transport to close)."""
        self._client = None

    @staticmethod
    def _usage_tokens(response: Any) -> int:
        """Total tokens from usage metadata, tolerating missing fields."""
        usage = getattr(response, "usage_metadata", None)
        if usage is None:
            return 0
        prompt = getattr(usage, "prompt_token_count", 0) or 0
        candidates = getattr(usage, "candidates_token_count", 0) or 0
        return int(prompt) + int(candidates)

    def _config(
        self,
        json_mode: bool = False,
        *,
        system: str | None = None,
        temperature: float | None = None,
        max_tokens: int | None = None,
        tools: list[LLMToolSpec] | None = None,
        response_format: ResponseFormat | None = None,
        tool_choice: ToolChoice | None = None,
    ) -> dict[str, Any]:
        """Build the generate_content config dict from neutral parameters."""
        config: dict[str, Any] = {}
        if system:
            config["system_instruction"] = system
        if temperature is not None:
            config["temperature"] = temperature
        if max_tokens is not None:
            config["max_output_tokens"] = max_tokens
        if tools:
            config["tools"] = [
                {"function_declarations": _to_function_declarations(tools)}
            ]
            mode = getattr(tool_choice, "mode", None)
            if mode in ("any", "tool"):
                config["tool_config"] = {"function_calling_config": {"mode": "ANY"}}
            elif mode == "none":
                config["tool_config"] = {"function_calling_config": {"mode": "NONE"}}
        if response_format is not None:
            config["response_mime_type"] = "application/json"
            config["response_schema"] = response_format.schema
        elif json_mode:
            config["response_mime_type"] = "application/json"
        return config

    @get_circuit_breaker("gemini_provider")
    async def generate(
        self, prompt: str, model: str, json_mode: bool = False, **kwargs: Any
    ) -> tuple[str, int]:
        """Generate a text completion.

        Returns:
            tuple[str, int]: Response text and total token count.
        """
        client = self._ensure_client()
        try:
            response = await client.aio.models.generate_content(
                model=model,
                contents=prompt,
                config=self._config(
                    json_mode,
                    system=kwargs.get("system"),
                    temperature=kwargs.get("temperature"),
                    max_tokens=kwargs.get("max_tokens"),
                ),
            )
            return response.text or "", self._usage_tokens(response)
        except LLMProviderError:
            raise
        except Exception as e:
            logger.error(f"Gemini generation error: {describe_exception(e)}")
            raise LLMProviderError(f"Gemini error: {describe_exception(e)}") from e

    @get_circuit_breaker("gemini_provider")
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
        """Generate with native tool-calling / structured output."""
        client = self._ensure_client()
        try:
            response = await client.aio.models.generate_content(
                model=model,
                contents=prompt,
                config=self._config(
                    system=kwargs.get("system"),
                    temperature=kwargs.get("temperature"),
                    max_tokens=kwargs.get("max_tokens"),
                    tools=tools,
                    tool_choice=tool_choice,
                    response_format=response_format,
                ),
            )
            tool_calls = self._extract_tool_calls(response)
            return LLMResult(
                text=getattr(response, "text", None) or None,
                tool_calls=tool_calls,
                stop_reason="tool_use" if tool_calls else "end_turn",
                tokens_used=self._usage_tokens(response),
                native=True,
                raw=response,
            )
        except LLMProviderError:
            raise
        except Exception as e:
            logger.error(f"Gemini structured generation error: {describe_exception(e)}")
            raise LLMProviderError(f"Gemini error: {describe_exception(e)}") from e

    @staticmethod
    def _extract_tool_calls(response: Any) -> list[ToolCall]:
        """Pull function calls out of the first candidate, if any."""
        calls: list[ToolCall] = []
        for idx, fc in enumerate(getattr(response, "function_calls", None) or []):
            calls.append(
                ToolCall(
                    id=getattr(fc, "id", None) or f"call_{idx}",
                    name=fc.name,
                    arguments=dict(fc.args or {}),
                )
            )
        return calls

    @get_circuit_breaker("gemini_provider")
    async def generate_stream(
        self, prompt: str, model: str, **kwargs: Any
    ) -> AsyncIterator[tuple[str, int]]:
        """Yield ``(chunk_text, tokens_so_far)`` tuples for a streamed reply."""
        client = self._ensure_client()
        try:
            stream = await client.aio.models.generate_content_stream(
                model=model,
                contents=prompt,
                config=self._config(
                    system=kwargs.get("system"),
                    temperature=kwargs.get("temperature"),
                    max_tokens=kwargs.get("max_tokens"),
                ),
            )
            total = 0
            async for chunk in stream:
                text = getattr(chunk, "text", None) or ""
                total = self._usage_tokens(chunk) or total
                if text:
                    yield text, total
        except LLMProviderError:
            raise
        except Exception as e:
            logger.error(f"Gemini streaming error: {describe_exception(e)}")
            raise LLMProviderError(f"Gemini error: {describe_exception(e)}") from e
