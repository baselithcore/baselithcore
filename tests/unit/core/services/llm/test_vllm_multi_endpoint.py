"""The vLLM provider sends each call to the server serving its model."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from core.services.llm.exceptions import LLMProviderError
from core.services.llm.preflight import VLLMProbe
from core.services.llm.vllm_endpoints import get_vllm_registry

A = "http://gpu:8002/v1"
B = "http://gpu:8003/v1"
_SDK = "core.services.llm.providers.openai_provider.openai"
_PROBE = "core.services.llm.vllm_endpoints.probe_vllm"


@pytest.fixture(autouse=True)
def _fresh_registry():
    get_vllm_registry().reset()
    yield
    get_vllm_registry().reset()


def _completion(text: str) -> MagicMock:
    response = MagicMock()
    response.choices = [MagicMock()]
    response.choices[0].message.content = text
    response.choices[0].message.refusal = None
    response.choices[0].finish_reason = "stop"
    response.usage.total_tokens = 5
    return response


def _clients():
    """One fake SDK client per base_url, answering with its own base."""
    made: dict[str, AsyncMock] = {}

    def factory(**kwargs):
        base = kwargs["base_url"]
        client = AsyncMock()
        client.chat.completions.create.return_value = _completion(base)
        made[base] = client
        return client

    return factory, made


async def _catalog(endpoint, _key, timeout=2.0, **_kw):
    return {A: VLLMProbe("ok", {"qwen"}), B: VLLMProbe("ok", {"llama"})}[endpoint]


async def test_each_model_reaches_its_own_server():
    from core.services.llm.providers.vllm_provider import VLLMProvider

    factory, made = _clients()
    with patch(_SDK) as sdk, patch(_PROBE, _catalog):
        sdk.AsyncOpenAI.side_effect = factory
        provider = VLLMProvider(api_base=None, endpoints=[A, B])
        llama, _ = await provider.generate("hi", model="llama")
        qwen, _ = await provider.generate("hi", model="qwen")

    assert (llama, qwen) == (B, A)
    assert set(made) == {A, B}  # one client per server, reused


async def test_an_unserved_model_names_what_is_served():
    from core.services.llm.providers.vllm_provider import VLLMProvider

    with patch(_PROBE, _catalog):
        provider = VLLMProvider(api_base=None, endpoints=[A, B])
        with pytest.raises(LLMProviderError, match=r"'mistral'.*llama.*qwen"):
            await provider.generate("hi", model="mistral")


async def test_all_servers_down_still_tries_the_first():
    """No catalog to consult: the call itself reports the connection error."""
    from core.services.llm.providers.vllm_provider import VLLMProvider

    factory, made = _clients()

    async def down(endpoint, _key, timeout=2.0, **_kw):
        return VLLMProbe("unreachable")

    with patch(_SDK) as sdk, patch(_PROBE, down):
        sdk.AsyncOpenAI.side_effect = factory
        provider = VLLMProvider(api_base=None, endpoints=[A, B])
        text, _ = await provider.generate("hi", model="qwen")
    assert text == A


async def test_streaming_is_routed_too():
    from core.services.llm.providers.vllm_provider import VLLMProvider

    async def chunks():
        yield SimpleNamespace(
            choices=[
                SimpleNamespace(
                    delta=SimpleNamespace(content="ok", reasoning_content=None)
                )
            ],
            usage=None,
        )

    seen: list[str] = []

    def factory(**kwargs):
        seen.append(kwargs["base_url"])
        client = AsyncMock()
        client.chat.completions.create.return_value = chunks()
        return client

    with patch(_SDK) as sdk, patch(_PROBE, _catalog):
        sdk.AsyncOpenAI.side_effect = factory
        provider = VLLMProvider(api_base=None, endpoints=[A, B])
        out = [t async for t, _ in provider.generate_stream("hi", model="llama")]
    assert "".join(out) == "ok"
    assert seen == [B]


def test_the_factory_hands_over_every_endpoint():
    from core.services.llm.provider_factory import create_provider

    config = SimpleNamespace(
        provider="vllm",
        model="qwen",
        api_key=None,
        api_base=None,
        vllm_api_base=None,
        vllm_endpoints=f"{A},{B}",
        vllm_api_key=None,
        vllm_native_tools=True,
        request_timeout=30.0,
        connect_timeout=2.0,
    )
    provider = create_provider(config)
    assert provider.endpoints == [A, B]
