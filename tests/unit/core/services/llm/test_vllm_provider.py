"""vLLM as a first-class provider.

vLLM speaks the OpenAI Chat Completions protocol, and used to be reachable
only by pretending to be OpenAI (``LLM_PROVIDER=openai`` plus ``LLM_API_BASE``).
That disguise cost three things these tests pin down: a fake API key was
mandatory although a vLLM server usually runs without one, a vLLM outage
tripped the *OpenAI* circuit breaker (and with it every real OpenAI stage of a
fallback chain), and errors were reported as OpenAI's.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from pydantic import SecretStr

from core.resilience.circuit_breaker import (
    CircuitState,
    CircuitStats,
    get_circuit_breaker,
)
from core.services.llm.exceptions import LLMProviderError

_SDK = "core.services.llm.providers.openai_provider.openai"


def _completion(text: str = "ok", total: int = 7) -> MagicMock:
    response = MagicMock()
    response.choices = [MagicMock()]
    response.choices[0].message.content = text
    response.choices[0].message.refusal = None
    response.choices[0].finish_reason = "stop"
    response.usage.total_tokens = total
    return response


@pytest.fixture(autouse=True)
def _fresh_breakers():
    """Breakers are process-wide and bound at decoration time; close both.

    Replacing the registry entry would not help: each decorated method holds
    the instance it was built with, so the state is reset in place.
    """

    def _close() -> None:
        for name in ("vllm_provider", "openai_provider"):
            breaker = get_circuit_breaker(name)
            breaker._state = CircuitState.CLOSED
            breaker._stats = CircuitStats()
            breaker._half_open_attempts = 0

    _close()
    yield
    _close()


class TestBaseUrl:
    @pytest.mark.parametrize(
        ("given", "expected"),
        [
            ("http://gpu:8000", "http://gpu:8000/v1"),
            ("http://gpu:8000/", "http://gpu:8000/v1"),
            ("http://gpu:8000/v1", "http://gpu:8000/v1"),
            ("http://gpu:8000/v1/", "http://gpu:8000/v1"),
            ("https://gw.internal/vllm/v1", "https://gw.internal/vllm/v1"),
            ("https://gw.internal/vllm", "https://gw.internal/vllm/v1"),
        ],
    )
    def test_normalises_to_the_openai_root(self, given, expected):
        from core.services.llm.providers.vllm_provider import normalize_vllm_base_url

        assert normalize_vllm_base_url(given) == expected


class TestInit:
    @patch(_SDK)
    def test_needs_no_api_key(self, mock_openai):
        """A keyless server is the common case; the SDK still wants a string."""
        mock_openai.AsyncOpenAI.return_value = MagicMock()
        from core.services.llm.providers.vllm_provider import VLLMProvider

        VLLMProvider(api_base="http://gpu:8000")._ensure_client()

        kwargs = mock_openai.AsyncOpenAI.call_args.kwargs
        assert kwargs["api_key"] == "EMPTY"
        assert kwargs["base_url"] == "http://gpu:8000/v1"
        assert kwargs["max_retries"] == 0

    @patch(_SDK)
    def test_forwards_a_configured_key(self, mock_openai):
        mock_openai.AsyncOpenAI.return_value = MagicMock()
        from core.services.llm.providers.vllm_provider import VLLMProvider

        provider = VLLMProvider(
            api_base="http://gpu:8000/v1", api_key=SecretStr("token-abc")
        )
        provider._ensure_client()

        assert mock_openai.AsyncOpenAI.call_args.kwargs["api_key"] == "token-abc"
        assert "token-abc" not in repr(vars(provider))

    @pytest.mark.parametrize("base", [None, "", "   "])
    def test_refuses_to_guess_an_endpoint(self, base):
        """No default: vLLM's :8000 is also where this backend listens."""
        from core.services.llm.providers.vllm_provider import VLLMProvider

        with pytest.raises(LLMProviderError, match="LLM_VLLM_API_BASE"):
            VLLMProvider(api_base=base)

    def test_native_tools_follow_the_server_flag(self):
        """Tool calling needs ``--enable-auto-tool-choice`` on the server."""
        from core.services.llm.providers.vllm_provider import VLLMProvider

        assert VLLMProvider(api_base="http://gpu:8000").supports_native_tools
        assert not VLLMProvider(
            api_base="http://gpu:8000", native_tools=False
        ).supports_native_tools
        assert VLLMProvider(api_base="http://gpu:8000").supports_messages


@pytest.mark.asyncio
class TestCalls:
    async def test_generate_uses_the_openai_protocol(self):
        client = AsyncMock()
        client.chat.completions.create.return_value = _completion("hi", 12)
        with patch(_SDK) as mock_openai:
            mock_openai.AsyncOpenAI.return_value = client
            from core.services.llm.providers.vllm_provider import VLLMProvider

            provider = VLLMProvider(api_base="http://gpu:8000")
            text, tokens = await provider.generate(
                "ping", model="Qwen/Qwen3-8B", max_tokens=64
            )

        assert (text, tokens) == ("hi", 12)
        sent = client.chat.completions.create.call_args.kwargs
        assert sent["model"] == "Qwen/Qwen3-8B"
        assert sent["max_completion_tokens"] == 64

    async def test_extra_body_reaches_the_server(self):
        """vLLM-only sampling knobs travel in ``extra_body``, untouched."""
        client = AsyncMock()
        client.chat.completions.create.return_value = _completion()
        extra = {"top_k": 20, "chat_template_kwargs": {"enable_thinking": False}}
        with patch(_SDK) as mock_openai:
            mock_openai.AsyncOpenAI.return_value = client
            from core.services.llm.providers.vllm_provider import VLLMProvider

            await VLLMProvider(api_base="http://gpu:8000").generate(
                "ping", model="m", extra_body=extra
            )

        assert client.chat.completions.create.call_args.kwargs["extra_body"] == extra

    async def test_errors_name_vllm_not_openai(self):
        client = AsyncMock()
        client.chat.completions.create.side_effect = Exception("boom")
        with patch(_SDK) as mock_openai:
            mock_openai.AsyncOpenAI.return_value = client
            from core.services.llm.providers.vllm_provider import VLLMProvider

            with pytest.raises(LLMProviderError) as exc_info:
                await VLLMProvider(api_base="http://gpu:8000").generate("p", model="m")

        assert "vLLM" in str(exc_info.value)
        assert "OpenAI" not in str(exc_info.value)

    async def test_an_outage_trips_its_own_breaker_only(self):
        """The regression: a vLLM outage used to open ``openai_provider``."""
        client = AsyncMock()
        client.chat.completions.create.side_effect = Exception("down")
        with patch(_SDK) as mock_openai:
            mock_openai.AsyncOpenAI.return_value = client
            from core.services.llm.providers.vllm_provider import VLLMProvider

            provider = VLLMProvider(api_base="http://gpu:8000")
            breaker = get_circuit_breaker("vllm_provider")
            for _ in range(breaker.fail_max):
                with pytest.raises(LLMProviderError):
                    await provider.generate("p", model="m")

        assert breaker.state == CircuitState.OPEN
        assert get_circuit_breaker("openai_provider").state == CircuitState.CLOSED

    async def test_stream_counts_against_its_own_breaker(self):
        async def _chunks():
            yield SimpleNamespace(
                choices=[SimpleNamespace(delta=SimpleNamespace(content="a"))],
                usage=None,
            )
            raise RuntimeError("stream cut")

        client = AsyncMock()
        client.chat.completions.create.return_value = _chunks()
        with patch(_SDK) as mock_openai:
            mock_openai.AsyncOpenAI.return_value = client
            from core.services.llm.providers.vllm_provider import VLLMProvider

            provider = VLLMProvider(api_base="http://gpu:8000")
            seen: list[str] = []
            with pytest.raises(LLMProviderError, match="vLLM"):
                async for text, _ in provider.generate_stream("p", model="m"):
                    seen.append(text)

        # Held back: until a ``</think>`` or the end of the stream, a leading
        # piece may be a thinking model's reasoning (see reasoning_text).
        assert seen == []
        assert get_circuit_breaker("vllm_provider")._stats.failures == 1
        assert get_circuit_breaker("openai_provider")._stats.failures == 0

    async def test_image_generation_is_refused_up_front(self):
        from core.services.llm.providers.vllm_provider import VLLMProvider

        with pytest.raises(LLMProviderError, match="image"):
            await VLLMProvider(api_base="http://gpu:8000").generate_image("a cat")


class TestFactory:
    def _config(self, **overrides):
        base = {
            "provider": "vllm",
            "model": "Qwen/Qwen3-8B",
            "api_key": None,
            "api_base": None,
            "vllm_api_base": "http://gpu:8000",
            "vllm_api_key": None,
            "vllm_native_tools": True,
            "request_timeout": 30.0,
            "connect_timeout": 2.0,
        }
        base.update(overrides)
        return SimpleNamespace(**base)

    def test_builds_a_vllm_provider(self):
        from core.services.llm.provider_factory import create_provider
        from core.services.llm.providers.vllm_provider import VLLMProvider

        provider = create_provider(self._config())

        assert isinstance(provider, VLLMProvider)
        assert provider._base_url == "http://gpu:8000/v1"
        assert provider._request_timeout == 30.0

    def test_policy_clone_endpoint_wins(self):
        """A policy clone carries the resolved endpoint in ``api_base``."""
        from core.services.llm.provider_factory import create_provider

        provider = create_provider(
            self._config(api_base="http://other:9000/v1", vllm_api_base=None)
        )
        assert provider._base_url == "http://other:9000/v1"

    def test_dedicated_key_outranks_the_primary_one(self):
        """``LLM_API_KEY`` may be an OpenAI key (``LLM_OPENAI_API_KEY`` alias)."""
        from core.services.llm.provider_factory import create_provider

        provider = create_provider(
            self._config(
                api_key=SecretStr("sk-openai"), vllm_api_key=SecretStr("vllm-tok")
            )
        )
        assert provider._api_key.get_secret_value() == "vllm-tok"

    def test_a_hosted_key_never_reaches_the_server(self):
        """The leak the review caught: ``LLM_API_KEY`` also answers to
        ``LLM_OPENAI_API_KEY``, so switching the default provider to vLLM used
        to send whatever OpenAI key sat in the environment to the GPU box."""
        from core.services.llm.provider_factory import create_provider
        from core.services.llm.providers.vllm_provider import VLLM_NO_KEY

        provider = create_provider(self._config(api_key=SecretStr("sk-openai")))
        assert provider._api_key.get_secret_value() == VLLM_NO_KEY

    def test_dedicated_endpoint_outranks_the_primary_base(self):
        """Same precedence as ``api_base_for``: the dedicated field first."""
        from core.services.llm.provider_factory import create_provider

        provider = create_provider(
            self._config(
                api_base="http://other:9000/v1", vllm_api_base="http://gpu:8000"
            )
        )
        assert provider._base_url == "http://gpu:8000/v1"

    def test_native_tools_flag_is_forwarded(self):
        from core.services.llm.provider_factory import create_provider

        provider = create_provider(self._config(vllm_native_tools=False))
        assert provider.supports_native_tools is False

    def test_missing_endpoint_fails_loudly(self):
        from core.services.llm.provider_factory import create_provider

        with pytest.raises(LLMProviderError, match="LLM_VLLM_API_BASE"):
            create_provider(self._config(vllm_api_base=None))
