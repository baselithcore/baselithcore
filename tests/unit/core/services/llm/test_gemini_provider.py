"""Unit tests for the Gemini provider (google-genai SDK mocked)."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from core.services.llm.exceptions import LLMProviderError
from core.services.llm.providers.gemini_provider import GeminiProvider
from core.services.llm.tool_calling import LLMToolSpec, ResponseFormat


def _response(text="hello", prompt_tokens=10, out_tokens=5, function_calls=None):
    return SimpleNamespace(
        text=text,
        function_calls=function_calls or [],
        usage_metadata=SimpleNamespace(
            prompt_token_count=prompt_tokens, candidates_token_count=out_tokens
        ),
    )


def _provider_with(response) -> GeminiProvider:
    provider = GeminiProvider(api_key="k")
    client = MagicMock()
    client.aio.models.generate_content = AsyncMock(return_value=response)
    provider._client = client
    return provider


class TestGenerate:
    async def test_returns_text_and_usage_tokens(self):
        provider = _provider_with(_response())
        text, tokens = await provider.generate("hi", model="gemini-2.5-flash")
        assert text == "hello"
        assert tokens == 15

    async def test_system_and_sampling_mapped_to_config(self):
        provider = _provider_with(_response())
        await provider.generate(
            "hi",
            model="gemini-2.5-flash",
            system="be terse",
            temperature=0.2,
            max_tokens=99,
        )
        config = provider._client.aio.models.generate_content.await_args.kwargs[
            "config"
        ]
        assert config["system_instruction"] == "be terse"
        assert config["temperature"] == 0.2
        assert config["max_output_tokens"] == 99

    async def test_json_mode_sets_mime_type(self):
        provider = _provider_with(_response(text="{}"))
        await provider.generate("hi", model="m", json_mode=True)
        config = provider._client.aio.models.generate_content.await_args.kwargs[
            "config"
        ]
        assert config["response_mime_type"] == "application/json"

    async def test_sdk_error_wrapped(self):
        provider = GeminiProvider(api_key="k")
        client = MagicMock()
        client.aio.models.generate_content = AsyncMock(side_effect=RuntimeError("boom"))
        provider._client = client
        with pytest.raises(LLMProviderError, match="Gemini error"):
            await provider.generate("hi", model="m")

    async def test_missing_sdk_raises_actionable_error(self, monkeypatch):
        import sys

        provider = GeminiProvider(api_key="k")
        monkeypatch.setitem(sys.modules, "google", None)
        with pytest.raises(LLMProviderError, match="google-genai is not installed"):
            await provider.generate("hi", model="m")


class TestGenerateStructured:
    async def test_tools_map_to_function_declarations(self):
        provider = _provider_with(_response())
        spec = LLMToolSpec(
            name="lookup",
            description="find things",
            parameters={"type": "object", "properties": {}},
        )
        await provider.generate_structured("hi", "m", tools=[spec])
        config = provider._client.aio.models.generate_content.await_args.kwargs[
            "config"
        ]
        declaration = config["tools"][0]["function_declarations"][0]
        assert declaration["name"] == "lookup"

    async def test_function_calls_become_tool_calls(self):
        fc = SimpleNamespace(id=None, name="lookup", args={"q": "x"})
        provider = _provider_with(_response(text=None, function_calls=[fc]))
        result = await provider.generate_structured("hi", "m")
        assert result.stop_reason == "tool_use"
        assert result.tool_calls[0].name == "lookup"
        assert result.tool_calls[0].arguments == {"q": "x"}
        assert result.native is True

    async def test_response_format_maps_to_schema(self):
        provider = _provider_with(_response(text="{}"))
        fmt = ResponseFormat(schema={"type": "object"}, name="Out")
        await provider.generate_structured("hi", "m", response_format=fmt)
        config = provider._client.aio.models.generate_content.await_args.kwargs[
            "config"
        ]
        assert config["response_schema"] == {"type": "object"}
        assert config["response_mime_type"] == "application/json"

    def test_supports_native_tools(self):
        assert GeminiProvider.supports_native_tools is True


class TestServiceWiring:
    def test_create_provider_requires_api_key(self, monkeypatch):
        from unittest.mock import patch

        from core.config.services import LLMConfig
        from core.services.llm.service import LLMService

        for var in (
            "LLM_API_KEY",
            "LLM_GEMINI_API_KEY",
            "GEMINI_API_KEY",
            "GOOGLE_API_KEY",
        ):
            monkeypatch.delenv(var, raising=False)
        config = LLMConfig(provider="gemini", model="gemini-2.5-flash", api_key=None)
        with (
            patch("core.services.llm.service.get_llm_config", return_value=config),
            pytest.raises(LLMProviderError, match="Gemini API key"),
        ):
            LLMService(config=config, enable_cache=False)

    def test_create_provider_builds_gemini(self):
        from pydantic import SecretStr

        from core.config.services import LLMConfig
        from core.services.llm.service import LLMService

        config = LLMConfig(
            provider="gemini", model="gemini-2.5-flash", LLM_API_KEY=SecretStr("k")
        )
        service = LLMService(config=config, enable_cache=False)
        assert isinstance(service.provider, GeminiProvider)

    def test_api_key_for_gemini_dedicated_field(self, monkeypatch):
        from pydantic import SecretStr

        from core.config.services import LLMConfig
        from core.services.llm import runtime
        from core.services.llm.runtime import api_key_for, provider_configured

        config = LLMConfig(provider="ollama", LLM_GEMINI_API_KEY=SecretStr("gk"))
        key = api_key_for(config, "gemini")
        assert key is not None and key.get_secret_value() == "gk"
        # google-genai is an optional extra; the key alone is not enough.
        monkeypatch.setattr(runtime, "_gemini_sdk_available", lambda: True)
        assert provider_configured(config, "gemini") is True

    def test_gemini_unconfigured_without_the_optional_sdk(self, monkeypatch):
        """A key without ``baselith-core[gemini]`` must not advertise a pin.

        The SDK is imported lazily at first use, so a service built for such a
        deployment would only blow up on the first call.
        """
        from pydantic import SecretStr

        from core.config.services import LLMConfig
        from core.services.llm import runtime
        from core.services.llm.runtime import provider_configured

        config = LLMConfig(provider="ollama", LLM_GEMINI_API_KEY=SecretStr("gk"))
        monkeypatch.setattr(runtime, "_gemini_sdk_available", lambda: False)
        assert provider_configured(config, "gemini") is False
