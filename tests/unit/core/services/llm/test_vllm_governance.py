"""vLLM through the seams that are not the shared funnel.

Plugins holding their own SDK read the pin from
:func:`core.services.llm.governed.resolve_governed_client_config`, and an
operator may store a vLLM key from the admin console through the credential
seam. Both have to land on the same server with the same key the funnel would
use — and neither may ever pick up the primary ``LLM_API_KEY``, which also
answers to ``LLM_OPENAI_API_KEY``.
"""

from __future__ import annotations

import os
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from pydantic import SecretStr

from core.config.services import LLMConfig
from core.services.llm.policy import PluginLLMPolicy, set_plugin_llm_policy_resolver


def _config(**kwargs) -> LLMConfig:
    env = kwargs.pop("env", {})
    with patch.dict(os.environ, env, clear=True):
        return LLMConfig(_env_file=None, **kwargs)


@pytest.fixture
def stored_key():
    """Register a credential resolver answering for vllm only."""
    from core.services.llm import credentials

    credentials.set_llm_credential_resolver(
        lambda provider: "stored-tok" if provider == "vllm" else None
    )
    yield
    credentials.set_llm_credential_resolver(None)


@pytest.fixture
def pin_vllm():
    set_plugin_llm_policy_resolver(
        lambda _plugin, _scope=None: PluginLLMPolicy(provider="vllm", model="qwen")
    )
    yield
    set_plugin_llm_policy_resolver(None)


def _factory_config(**overrides):
    base = {
        "provider": "vllm",
        "model": "qwen",
        "api_key": SecretStr("sk-openai"),
        "api_base": None,
        "vllm_api_base": "http://gpu:8002",
        "vllm_api_key": None,
        "vllm_native_tools": True,
        "request_timeout": 30.0,
        "connect_timeout": 2.0,
    }
    base.update(overrides)
    return SimpleNamespace(**base)


class TestStoredCredential:
    def test_factory_uses_a_console_stored_key(self, stored_key):
        from core.services.llm.provider_factory import create_provider

        provider = create_provider(_factory_config())
        assert provider._api_key.get_secret_value() == "stored-tok"

    def test_the_environment_key_still_wins(self, stored_key):
        from core.services.llm.provider_factory import create_provider

        provider = create_provider(_factory_config(vllm_api_key=SecretStr("env-tok")))
        assert provider._api_key.get_secret_value() == "env-tok"

    async def test_preflight_probes_with_the_stored_key(self, stored_key):
        from core.services.llm._vllm_preflight import VLLMProbe, check_vllm_endpoints

        config = _config(provider="vllm", model="qwen", api_base="http://gpu:8002")
        probe = AsyncMock(return_value=VLLMProbe("ok", {"qwen"}))
        with patch("core.services.llm._vllm_preflight.probe_vllm", probe):
            assert await check_vllm_endpoints(config) == []
        assert probe.call_args.args[1] == "stored-tok"


class TestGoverned:
    def test_a_vllm_pin_carries_the_openai_root(self, pin_vllm):
        from core.services.llm.governed import resolve_governed_client_config

        config = _config(
            provider="openai",
            model="gpt-4o-mini",
            env={"LLM_VLLM_API_BASE": "http://gpu:8002", "LLM_API_KEY": "sk-openai"},
        )
        with (
            patch("core.services.llm.governed.get_llm_config", return_value=config),
            patch(
                "core.services.llm.runtime.resolve_llm_credential", return_value=None
            ),
        ):
            gov = resolve_governed_client_config("docheck")

        assert gov is not None
        assert (gov.provider, gov.model) == ("vllm", "qwen")
        assert gov.api_base == "http://gpu:8002/v1"
        assert gov.api_key is None

    def test_a_vllm_default_never_hands_out_the_primary_key(self, pin_vllm):
        from core.services.llm.governed import resolve_governed_client_config

        config = _config(
            provider="vllm",
            model="qwen",
            api_base="http://gpu:8002/v1",
            env={"LLM_OPENAI_API_KEY": "sk-openai"},
        )
        with (
            patch("core.services.llm.governed.get_llm_config", return_value=config),
            patch(
                "core.services.llm.runtime.resolve_llm_credential", return_value=None
            ),
        ):
            gov = resolve_governed_client_config("docheck")

        assert gov is not None and gov.api_key is None


class TestOpenAIWire:
    @pytest.mark.parametrize(
        ("provider", "expected"),
        [("openai", True), ("vllm", True), ("ollama", False), ("anthropic", False)],
    )
    def test_speaks_openai(self, provider, expected):
        from core.services.llm.governed import GovernedClientConfig

        gov = GovernedClientConfig(provider, "m", None, None)
        assert gov.speaks_openai is expected

    def test_vllm_key_falls_back_to_the_placeholder(self):
        from core.services.llm.governed import GovernedClientConfig

        assert GovernedClientConfig("vllm", "m", None, None).openai_key() == "EMPTY"
        assert (
            GovernedClientConfig("vllm", "m", SecretStr("t"), None).openai_key() == "t"
        )
        assert GovernedClientConfig("openai", "m", None, None).openai_key() is None
