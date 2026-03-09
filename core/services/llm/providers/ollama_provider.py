"""
Ollama local LLM provider implementation.

This module enables integration with locally running LLMs via Ollama.
It supports the standard chat interface and automatic server host discovery.
"""

from core.observability.logging import get_logger
import os
from typing import Any, AsyncIterator

try:
    import ollama
except ImportError:
    ollama = None  # type: ignore

from core.services.llm.exceptions import LLMProviderError
from core.services.llm.cost_control import estimate_tokens


logger = get_logger(__name__)


class OllamaProvider:
    """
    Asynchronous Ollama API provider.

    Interfaces with the Ollama service using its official Python client.
    Handles version-specific differences in client initialization and response formats.
    """

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
                self.client = ollama.AsyncClient(host=self.api_base)
                logger.info(f"Initialized Ollama provider with base: {self.api_base}")
            except AttributeError:
                # Fallback for older client versions that don't support explicit host injection via constructor.
                os.environ["OLLAMA_HOST"] = self.api_base
                logger.info(f"Set OLLAMA_HOST environment variable: {self.api_base}")
                self.client = ollama.AsyncClient()
        else:
            self.client = ollama.AsyncClient()
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

            # Execute the asynchronous chat request.
            response = await client.chat(  # type: ignore[call-overload]
                model=model,
                messages=[{"role": "user", "content": prompt}],
                **request_kwargs,
            )

            # Robust content and token extraction as Ollama schema can vary by version.
            content = self._extract_content(response)
            tokens_used = self._extract_tokens(response, prompt, content)

            return content, tokens_used

        except Exception as e:
            logger.error(f"Ollama generation error: {e}")
            raise LLMProviderError(f"Ollama error: {e}") from e

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
            stream = await client.chat(
                model=model,
                messages=[{"role": "user", "content": prompt}],
                stream=True,
            )

            accumulated_content = ""
            async for chunk in stream:
                content = self._extract_content(chunk)
                if content:
                    accumulated_content += content
                    # Estimate tokens for the stream flow.
                    tokens = estimate_tokens(prompt) + estimate_tokens(
                        accumulated_content
                    )
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
