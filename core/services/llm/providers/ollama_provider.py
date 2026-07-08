"""
Ollama local LLM provider implementation.

This module enables integration with locally running LLMs via Ollama.
It supports the standard chat interface and automatic server host discovery.
"""

import os
from collections.abc import AsyncIterator
from typing import Any

import httpx

from core.observability.logging import get_logger

try:
    import ollama
except ImportError:
    ollama = None  # type: ignore

from core.services.llm.cost_control import estimate_tokens
from core.services.llm.exceptions import LLMProviderError
from core.services.llm.tool_calling import (
    LLMResult,
    LLMToolSpec,
    ResponseFormat,
    ToolCall,
    ToolChoice,
)

logger = get_logger(__name__)


def _to_ollama_tools(tools: list[LLMToolSpec]) -> list[dict[str, Any]]:
    """Map neutral tool specs to Ollama ``tools`` (function) entries.

    Ollama follows OpenAI's function-tool shape.
    """
    return [
        {
            "type": "function",
            "function": {
                "name": spec.name,
                "description": spec.description,
                "parameters": spec.parameters or {"type": "object"},
            },
        }
        for spec in tools
    ]


class OllamaProvider:
    """
    Asynchronous Ollama API provider.

    Interfaces with the Ollama service using its official Python client.
    Handles version-specific differences in client initialization and response formats.
    """

    # Ollama's chat API accepts ``tools`` and returns ``message.tool_calls``.
    # Forced tool_choice isn't supported upstream, so forcing is best-effort.
    supports_native_tools: bool = True

    def __init__(self, api_base: str | None = None):
        """
        Initialize the Ollama provider.

        Args:
            api_base: Overriding base URL for the Ollama server.
                      If omitted, it resolves via framework services config.
        """
        from core.config.services import get_llm_config

        llm_config = get_llm_config()
        self.api_base = api_base or llm_config.api_base
        # Explicit deadline: without it the underlying httpx client waits
        # forever on a hung local server, pinning the calling worker.
        self._timeout = httpx.Timeout(
            llm_config.request_timeout, connect=llm_config.connect_timeout
        )
        self.client: Any = None

    def _ensure_client(self) -> Any:
        """
        Lazily initialize the Ollama AsyncClient.

        Supports fallback for older versions that rely on OLLAMA_HOST environment variables.

        Returns:
            The initialized Ollama AsyncClient instance.
        """
        if self.client is not None:
            return self.client

        if self.api_base:
            try:
                self.client = ollama.AsyncClient(
                    host=self.api_base, timeout=self._timeout
                )
                logger.info(f"Initialized Ollama provider with base: {self.api_base}")
            except AttributeError:
                # Fallback for older client versions that don't support explicit host injection via constructor.
                os.environ["OLLAMA_HOST"] = self.api_base
                logger.info(f"Set OLLAMA_HOST environment variable: {self.api_base}")
                self.client = ollama.AsyncClient(timeout=self._timeout)
        else:
            self.client = ollama.AsyncClient(timeout=self._timeout)
            logger.info("Initialized Ollama provider with default local settings")

        return self.client

    async def close(self) -> None:
        """
        Close the Ollama async client.
        """
        if self.client is not None:
            try:
                if hasattr(self.client, "close") and callable(self.client.close):
                    await self.client.close()
                self.client = None
                logger.info("Closed Ollama provider client")
            except Exception as e:
                logger.warning(f"Error closing Ollama client: {e}")

    async def generate(
        self, prompt: str, model: str, json_mode: bool = False, **kwargs
    ) -> tuple[str, int]:
        """
        Send a chat completion request to the local Ollama instance.

        Args:
            prompt: User message content.
            model: Name of the local model (e.g., 'llama3').
            json_mode: If True, instructs Ollama to format output as JSON.
            **kwargs: Extra parameters for the model.

        Returns:
            tuple[str, int]: Response text and combined token count.

        Raises:
            LLMProviderError: If server is unreachable or generation fails.
        """
        client = self._ensure_client()
        if not client:
            raise LLMProviderError(
                "Ollama client library not available or failed to initialize"
            )

        try:
            request_kwargs = {}
            if json_mode:
                request_kwargs["format"] = "json"

            messages = []
            if "system" in kwargs:
                messages.append({"role": "system", "content": kwargs["system"]})
            messages.append({"role": "user", "content": prompt})

            # Execute the asynchronous chat request.
            response = await client.chat(  # type: ignore[call-overload]
                model=model,
                messages=messages,
                **request_kwargs,
            )

            # Robust content and token extraction as Ollama schema can vary by version.
            content = self._extract_content(response)
            tokens_used = self._extract_tokens(response, prompt, content)

            return content, tokens_used

        except Exception as e:
            logger.error(f"Ollama generation error: {e}")
            raise LLMProviderError(f"Ollama error: {e}") from e

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
        Generate using Ollama's native tool-calling / structured-output support.

        Tools map to the ``tools`` argument; ``response_format`` maps to the
        ``format`` argument (a JSON Schema, which recent Ollama enforces).
        ``message.tool_calls`` are parsed into :class:`ToolCall` (Ollama already
        returns arguments as a dict; call ids are synthesized since Ollama does
        not assign them). Forced ``tool_choice`` is best-effort: ``none`` omits
        tools, other modes simply expose the tools and let the model decide.

        Args:
            prompt: User turn.
            model: Local model name.
            tools: Tools the model may call.
            tool_choice: Selection policy (best-effort; see above).
            response_format: Optional structured-output constraint.
            **kwargs: ``system``, ``temperature``, ``max_tokens``.

        Returns:
            LLMResult: text and/or structured tool calls with token usage.
        """
        client = self._ensure_client()
        if not client:
            raise LLMProviderError(
                "Ollama client library not available or failed to initialize"
            )

        try:
            messages: list[dict[str, Any]] = []
            if "system" in kwargs:
                messages.append({"role": "system", "content": kwargs["system"]})
            messages.append({"role": "user", "content": prompt})

            request_kwargs: dict[str, Any] = {"model": model, "messages": messages}
            expose_tools = tools and not (
                tool_choice is not None and tool_choice.mode == "none"
            )
            if expose_tools:
                request_kwargs["tools"] = _to_ollama_tools(tools or [])
            if response_format is not None:
                # Recent Ollama accepts a JSON Schema for `format` to enforce
                # structured output; older versions treat any truthy value as
                # JSON mode.
                request_kwargs["format"] = response_format.schema

            options: dict[str, Any] = {}
            if "temperature" in kwargs:
                options["temperature"] = kwargs["temperature"]
            if "max_tokens" in kwargs:
                options["num_predict"] = kwargs["max_tokens"]
            if options:
                request_kwargs["options"] = options

            response = await client.chat(**request_kwargs)

            content = self._extract_content(response)
            tool_calls = self._extract_tool_calls(response)
            tokens_used = self._extract_tokens(response, prompt, content)

            return LLMResult(
                text=content or None,
                tool_calls=tool_calls,
                stop_reason="tool_use" if tool_calls else "stop",
                tokens_used=tokens_used,
                native=True,
                raw=response,
            )

        except Exception as e:
            logger.error(f"Ollama structured generation error: {e}")
            raise LLMProviderError(f"Ollama error: {e}") from e

    def _extract_tool_calls(self, response) -> list[ToolCall]:
        """Parse Ollama tool calls, tolerating dict- and object-shaped schemas.

        Ollama does not assign call ids, so synthesize a stable one per index.
        """
        if isinstance(response, dict):
            message = response.get("message", {})
            raw_calls = (
                message.get("tool_calls", []) if isinstance(message, dict) else []
            )
        else:
            message = getattr(response, "message", None)
            raw_calls = getattr(message, "tool_calls", None) or []

        calls: list[ToolCall] = []
        for idx, call in enumerate(raw_calls):
            if isinstance(call, dict):
                function = call.get("function", {})
                name = function.get("name", "")
                arguments = function.get("arguments", {})
            else:
                function = getattr(call, "function", None)
                name = getattr(function, "name", "")
                arguments = getattr(function, "arguments", {})
            if not name:
                continue
            calls.append(
                ToolCall(
                    id=f"ollama-call-{idx}",
                    name=name,
                    arguments=dict(arguments) if arguments else {},
                )
            )
        return calls

    async def generate_stream(
        self, prompt: str, model: str, **kwargs
    ) -> AsyncIterator[tuple[str, int]]:
        """
        Request a streaming completion from the local Ollama instance.

        Args:
            prompt: User message content.
            model: Target local model name.

        Yields:
            tuple[str, int]: Chunks of text and current estimation of total tokens.
        """
        client = self._ensure_client()
        if not client:
            raise LLMProviderError("Ollama client not initialized")

        try:
            messages = []
            if "system" in kwargs:
                messages.append({"role": "system", "content": kwargs["system"]})
            messages.append({"role": "user", "content": prompt})

            stream = await client.chat(
                model=model,
                messages=messages,
                stream=True,
            )

            # Estimate prompt tokens once; accumulate per-delta instead of
            # re-tokenizing the full accumulated text on every chunk.
            tokens = estimate_tokens(prompt)
            async for chunk in stream:
                content = self._extract_content(chunk)
                if content:
                    tokens += estimate_tokens(content)
                    yield content, tokens

        except Exception as e:
            logger.error(f"Ollama streaming error: {e}")
            raise LLMProviderError(f"Ollama streaming error: {e}") from e

    def _extract_content(self, response) -> str:
        """
        Normalize and extract reasoning content from different Ollama response schemas.

        Args:
            response: Raw dictionary or object response from Ollama.

        Returns:
            str: Cleaned and stripped text content.
        """
        if isinstance(response, dict):
            return response.get("message", {}).get("content", "").strip()
        elif hasattr(response, "message"):
            if hasattr(response.message, "content"):
                return response.message.content.strip()
            return str(response.message)
        return str(response)

    def _extract_tokens(self, response, prompt: str, content: str) -> int:
        """
        Calculate token usage from response metadata with estimation fallback.

        Args:
            response: Raw Ollama response.
            prompt: Original request text.
            content: Generated response text.

        Returns:
            int: combined token count.
        """
        if isinstance(response, dict):
            eval_count = response.get("eval_count", 0)
            prompt_eval = response.get("prompt_eval_count", 0)
            if eval_count > 0 or prompt_eval > 0:
                return eval_count + prompt_eval
        elif hasattr(response, "eval_count") or hasattr(response, "prompt_eval_count"):
            eval_count = getattr(response, "eval_count", 0) or 0
            prompt_eval = getattr(response, "prompt_eval_count", 0) or 0
            if eval_count > 0 or prompt_eval > 0:
                return eval_count + prompt_eval

        # Heuristic fallback if server does not provide usage stats.
        return estimate_tokens(prompt) + estimate_tokens(content)
