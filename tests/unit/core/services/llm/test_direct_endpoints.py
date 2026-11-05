"""Endpoint resolution for clients that speak a provider's protocol directly.

The resolver exists so a component holding no SDK can still reach every
provider without naming a provider host itself, and so it dials the same
server the funnel would for the same provider.
"""

from unittest.mock import patch

import pytest

from baselith.core.config.services import LLMConfig
from baselith.core.services.llm import direct_endpoints
from baselith.core.services.llm.direct_endpoints import (
    DEFAULT_OLLAMA_BASE,
    HOSTED_API_ROOTS,
    DirectEndpoint,
    direct_endpoint,
    ollama_root,
)
from baselith.core.services.llm.policy import SUPPORTED_PROVIDERS


def _config(**kwargs) -> LLMConfig:
    """A config built from explicit values only (no ambient env or ``.env``)."""
    env = kwargs.pop("env", {})
    with patch.dict("os.environ", env, clear=True):
        return LLMConfig(**kwargs)


class TestHostedProviders:
    def test_every_hosted_provider_has_a_root(self):
        hosted = set(SUPPORTED_PROVIDERS) - {"ollama", "vllm"}
        assert hosted == set(HOSTED_API_ROOTS)

    @pytest.mark.parametrize(
        ("provider", "protocol"),
        [
            ("openai", "chat_completions"),
            ("anthropic", "anthropic_messages"),
            ("gemini", "chat_completions"),
            ("huggingface", "chat_completions"),
        ],
    )
    def test_unconfigured_provider_gets_the_public_root(self, provider, protocol):
        endpoint = direct_endpoint(_config(provider="ollama", model="m"), provider)
        assert endpoint == DirectEndpoint(
            provider, protocol, HOSTED_API_ROOTS[provider][1]
        )

    def test_default_provider_gateway_replaces_the_openai_root(self):
        config = _config(
            provider="openai", model="gpt-4o", api_base="https://gw.example/v1/"
        )
        endpoint = direct_endpoint(config, "openai")
        assert endpoint is not None
        assert endpoint.base_url == "https://gw.example/v1"

    def test_gemini_never_takes_the_native_sdk_base(self):
        """Gemini's configured base addresses the native API, not the
        OpenAI-compatible surface a direct client speaks."""
        config = _config(
            provider="gemini", model="gemini-2.5-pro", api_base="https://native.example"
        )
        endpoint = direct_endpoint(config, "gemini")
        assert endpoint is not None
        assert endpoint.base_url == HOSTED_API_ROOTS["gemini"][1]

    def test_anthropic_on_a_cloud_backend_is_unreachable(self):
        config = _config(provider="openai", model="m", anthropic_backend="bedrock")
        assert direct_endpoint(config, "anthropic") is None

    def test_unknown_provider_resolves_to_none(self):
        assert direct_endpoint(_config(provider="openai", model="m"), "mistral") is None


class TestOllama:
    def test_default_address_when_nothing_is_configured(self):
        config = _config(provider="openai", model="m")
        assert ollama_root(config) == DEFAULT_OLLAMA_BASE
        endpoint = direct_endpoint(config, "ollama")
        assert endpoint is not None
        assert endpoint.base_url == f"{DEFAULT_OLLAMA_BASE}/v1"

    def test_bare_ollama_host_gets_a_scheme(self, monkeypatch):
        config = _config(provider="openai", model="m")
        # Read at call time by ``api_base_for``, not stored on the config.
        monkeypatch.setenv("OLLAMA_HOST", "box:11434")
        assert ollama_root(config) == "http://box:11434"

    def test_dedicated_base_with_v1_is_not_doubled(self):
        config = _config(
            provider="openai", model="m", ollama_api_base="http://gpu:11434/v1/"
        )
        endpoint = direct_endpoint(config, "ollama")
        assert endpoint is not None
        assert endpoint.base_url == "http://gpu:11434/v1"


class TestVllm:
    def test_no_server_configured_is_none(self):
        assert direct_endpoint(_config(provider="openai", model="m"), "vllm") is None

    def test_single_server_is_normalised_to_its_v1_root(self):
        config = _config(provider="openai", model="m", vllm_api_base="http://gpu:8002")
        endpoint = direct_endpoint(config, "vllm", "qwen")
        assert endpoint == DirectEndpoint(
            "vllm", "chat_completions", "http://gpu:8002/v1"
        )

    def test_model_picks_its_server_among_several(self, monkeypatch):
        config = _config(
            provider="openai",
            model="m",
            vllm_endpoints="http://a:8002/v1, http://b:8004/v1",
        )
        seen: list[tuple[str | None, object]] = []

        def fake_base(cfg, model, key):
            seen.append((model, key))
            return "http://b:8004/v1"

        monkeypatch.setattr(
            "baselith.core.services.llm.governed._vllm_base_for", fake_base
        )
        endpoint = direct_endpoint(config, "vllm", "rerank")
        assert endpoint is not None
        assert endpoint.base_url == "http://b:8004/v1"
        assert seen == [("rerank", None)]

    def test_resolver_module_names_no_key(self):
        """The answer carries no credential: keys are read at call time."""
        assert "api_key" not in DirectEndpoint.__dataclass_fields__
        assert direct_endpoints.__all__  # explicit exports
