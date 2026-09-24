"""vLLM wired through every place that names a provider.

A provider the factory can build is not yet a provider the framework serves:
the per-plugin policy, the fallback chain, the endpoint and credential
resolution, cost accounting, telemetry and the startup preflight each keep
their own notion of "which providers exist". These tests walk that list.
"""

from __future__ import annotations

import os
from unittest.mock import AsyncMock, patch

import pytest
from pydantic import SecretStr

from core.config.services import LLMConfig


def _config(**kwargs) -> LLMConfig:
    """A config built from explicit values, with the environment cleared."""
    env = kwargs.pop("env", {})
    with patch.dict(os.environ, env, clear=True):
        return LLMConfig(_env_file=None, **kwargs)


def _codes(findings) -> set[str]:
    return {f.code for f in findings}


class TestConfig:
    def test_vllm_is_an_accepted_provider(self):
        assert _config(provider="vllm", model="m").provider == "vllm"

    def test_dedicated_settings_read_their_env_vars(self):
        config = _config(
            provider="openai",
            model="gpt-4o-mini",
            env={
                "LLM_VLLM_API_BASE": "http://gpu:8000/v1",
                "VLLM_API_KEY": "tok",
                "LLM_VLLM_NATIVE_TOOLS": "false",
            },
        )
        assert config.vllm_api_base == "http://gpu:8000/v1"
        assert isinstance(config.vllm_api_key, SecretStr)
        assert config.vllm_api_key.get_secret_value() == "tok"
        assert config.vllm_native_tools is False

    def test_blank_values_read_as_unset(self):
        config = _config(
            provider="vllm",
            model="m",
            env={"LLM_VLLM_API_BASE": "  ", "LLM_VLLM_API_KEY": " "},
        )
        assert config.vllm_api_base is None
        assert config.vllm_api_key is None


class TestRuntime:
    def test_dedicated_endpoint_wins_and_stays_its_own(self):
        from core.services.llm.runtime import api_base_for

        config = _config(
            provider="openai",
            model="gpt-4o-mini",
            api_base="https://openai-gw/v1",
            env={"LLM_VLLM_API_BASE": "http://gpu:8000/v1"},
        )
        assert api_base_for(config, "vllm") == "http://gpu:8000/v1"
        assert api_base_for(config, "openai") == "https://openai-gw/v1"

    def test_primary_base_serves_a_vllm_default(self):
        from core.services.llm.runtime import api_base_for

        config = _config(provider="vllm", model="m", api_base="http://gpu:8000/v1")
        assert api_base_for(config, "vllm") == "http://gpu:8000/v1"

    def test_another_providers_base_is_never_borrowed(self):
        from core.services.llm.runtime import api_base_for

        config = _config(provider="openai", model="m", api_base="https://gw/v1")
        assert api_base_for(config, "vllm") is None

    def test_configured_means_an_endpoint_not_a_key(self):
        from core.services.llm.runtime import provider_configured

        keyless = _config(
            provider="openai",
            model="m",
            env={"LLM_VLLM_API_BASE": "http://gpu:8000"},
        )
        assert provider_configured(keyless, "vllm") is True
        assert provider_configured(_config(provider="openai", model="m"), "vllm") is (
            False
        )

    def test_dedicated_key_is_resolved(self):
        from core.services.llm.runtime import api_key_from_config

        config = _config(
            provider="openai",
            model="m",
            env={"LLM_VLLM_API_KEY": "tok", "LLM_API_KEY": "sk-openai"},
        )
        key = api_key_from_config(config, "vllm")
        assert key is not None and key.get_secret_value() == "tok"

    def test_the_primary_key_is_never_vllms(self):
        """``LLM_API_KEY`` doubles as ``LLM_OPENAI_API_KEY``; vLLM reads only
        its own key, even when it is the default provider."""
        from core.services.llm.runtime import api_key_for, api_key_from_config

        config = _config(
            provider="vllm",
            model="m",
            api_base="http://gpu:8000",
            env={"LLM_OPENAI_API_KEY": "sk-openai"},
        )
        assert config.api_key is not None
        assert api_key_from_config(config, "vllm") is None
        with patch(
            "core.services.llm.runtime.resolve_llm_credential", return_value=None
        ):
            assert api_key_for(config, "vllm") is None

    def test_setup_hint_names_the_endpoint(self):
        from core.services.llm.runtime import provider_setup_hint

        assert "LLM_VLLM_API_BASE" in provider_setup_hint("vllm")
        assert "API key" in provider_setup_hint("anthropic")


class TestProviderLists:
    def test_policy_can_pin_vllm(self):
        from core.services.llm.policy import SUPPORTED_PROVIDERS

        assert "vllm" in SUPPORTED_PROVIDERS

    def test_fallback_chain_accepts_a_vllm_stage(self):
        from core.services.llm._fallback_support import parse_fallback_chain

        assert parse_fallback_chain("openai:gpt-4o-mini, vllm:Qwen/Qwen3-8B") == [
            ("openai", "gpt-4o-mini"),
            ("vllm", "Qwen/Qwen3-8B"),
        ]

    def test_telemetry_reports_vllm(self):
        from core.services.llm._telemetry import gen_ai_system

        assert gen_ai_system("vllm") == "vllm"

    def test_self_hosted_tokens_cost_nothing(self):
        """Billing them at UNKNOWN_PRICE would abort tenant budgets."""
        from core.models.pricing import get_price, qualified_model_id

        model_id = qualified_model_id("vllm", "Qwen/Qwen3-8B")
        assert model_id == "vllm/Qwen/Qwen3-8B"
        price = get_price(model_id)
        assert price.input_usd_per_million == 0.0
        assert price.output_usd_per_million == 0.0


class TestPreflightConfiguration:
    def test_a_vllm_primary_without_endpoint_names_the_setting(self):
        from core.services.llm.preflight import check_configuration

        config = _config(provider="vllm", model="m")
        with patch.dict(os.environ, {"LLM_PROVIDER": "vllm"}, clear=True):
            findings = check_configuration(config)
        [finding] = [f for f in findings if f.code == "primary_provider_unconfigured"]
        assert "LLM_VLLM_API_BASE" in finding.remedy

    def test_a_keyless_vllm_primary_is_fine(self):
        from core.services.llm.preflight import check_configuration

        config = _config(provider="vllm", model="m", api_base="http://gpu:8000")
        with patch.dict(os.environ, {"LLM_PROVIDER": "vllm"}, clear=True):
            assert check_configuration(config) == []


_PROBE = "core.services.llm._vllm_preflight.probe_vllm"


@pytest.mark.asyncio
class TestPreflightEndpoints:
    @pytest.fixture(autouse=True)
    def _no_ollama(self):
        with patch(
            "core.services.llm.preflight.probe_ollama",
            AsyncMock(return_value={"llama3.2:latest"}),
        ):
            yield

    async def test_a_served_model_passes(self):
        from core.services.llm._vllm_preflight import VLLMProbe
        from core.services.llm.preflight import check_local_endpoints

        config = _config(provider="vllm", model="Qwen/Qwen3-8B", api_base="http://g")
        with patch(_PROBE, AsyncMock(return_value=VLLMProbe("ok", {"Qwen/Qwen3-8B"}))):
            assert await check_local_endpoints(config) == []

    async def test_a_name_mismatch_is_reported_with_what_is_served(self):
        """The common one: ``--served-model-name`` differs from LLM_MODEL."""
        from core.services.llm._vllm_preflight import VLLMProbe
        from core.services.llm.preflight import check_local_endpoints

        config = _config(provider="vllm", model="qwen3", api_base="http://g")
        with patch(_PROBE, AsyncMock(return_value=VLLMProbe("ok", {"Qwen/Qwen3-8B"}))):
            [finding] = await check_local_endpoints(config)
        assert finding.code == "vllm_model_missing"
        assert finding.severity == "error"
        assert "Qwen/Qwen3-8B" in finding.remedy

    async def test_userinfo_never_reaches_a_finding(self):
        """Findings are logged and raised; a basic-auth URL must not leak."""
        from core.services.llm._vllm_preflight import VLLMProbe
        from core.services.llm.preflight import check_local_endpoints

        config = _config(
            provider="vllm", model="m", api_base="http://ops:s3cret@gpu:8000"
        )
        probe = AsyncMock(return_value=VLLMProbe("unreachable"))
        with patch(_PROBE, probe):
            [finding] = await check_local_endpoints(config)
        assert "s3cret" not in str(finding)
        assert "gpu:8000" in finding.message
        # The probe itself still dials the configured URL, credentials included.
        assert probe.call_args.args[0] == "http://ops:s3cret@gpu:8000/v1"

    async def test_an_unreachable_server_is_reported(self):
        from core.services.llm._vllm_preflight import VLLMProbe
        from core.services.llm.preflight import check_local_endpoints

        config = _config(provider="vllm", model="m", api_base="http://g")
        with patch(_PROBE, AsyncMock(return_value=VLLMProbe("unreachable"))):
            assert _codes(await check_local_endpoints(config)) == {"vllm_unreachable"}

    async def test_a_rejected_key_is_not_called_unreachable(self):
        from core.services.llm._vllm_preflight import VLLMProbe
        from core.services.llm.preflight import check_local_endpoints

        config = _config(provider="vllm", model="m", api_base="http://g")
        with patch(_PROBE, AsyncMock(return_value=VLLMProbe("unauthorized"))):
            [finding] = await check_local_endpoints(config)
        assert finding.code == "vllm_unauthorized"
        assert "LLM_VLLM_API_KEY" in finding.remedy

    async def test_a_fallback_stage_is_probed_on_its_own_endpoint(self):
        from core.services.llm._vllm_preflight import VLLMProbe
        from core.services.llm.preflight import check_local_endpoints

        config = _config(
            provider="ollama",
            model="llama3.2",
            fallback_chain="vllm:Qwen/Qwen3-8B",
            env={"LLM_VLLM_API_BASE": "http://gpu:8000", "VLLM_API_KEY": "tok"},
        )
        probe = AsyncMock(return_value=VLLMProbe("ok", set()))
        with patch(_PROBE, probe):
            findings = await check_local_endpoints(config)
        assert _codes(findings) == {"vllm_model_missing"}
        endpoint, key = probe.call_args.args[:2]
        assert endpoint == "http://gpu:8000/v1"
        assert key == "tok"

    async def test_nothing_is_probed_without_a_vllm_target(self):
        from core.services.llm.preflight import check_local_endpoints

        probe = AsyncMock()
        with patch(_PROBE, probe):
            await check_local_endpoints(_config(provider="ollama", model="llama3.2"))
        probe.assert_not_called()


@pytest.mark.asyncio
class TestProbe:
    async def test_reads_the_model_catalog(self):
        import httpx

        from core.services.llm._vllm_preflight import probe_vllm

        def handler(request: httpx.Request) -> httpx.Response:
            assert request.url.path == "/v1/models"
            assert request.headers["authorization"] == "Bearer tok"
            return httpx.Response(200, json={"data": [{"id": "Qwen/Qwen3-8B"}]})

        probe = await probe_vllm(
            "http://gpu:8000/v1", "tok", transport=httpx.MockTransport(handler)
        )
        assert probe.status == "ok"
        assert probe.models == {"Qwen/Qwen3-8B"}

    async def test_401_is_unauthorized(self):
        import httpx

        from core.services.llm._vllm_preflight import probe_vllm

        def handler(request: httpx.Request) -> httpx.Response:
            # A keyless deployment sends no header at all, never "Bearer None".
            assert "authorization" not in request.headers
            return httpx.Response(401)

        probe = await probe_vllm(
            "http://gpu:8000/v1", None, transport=httpx.MockTransport(handler)
        )
        assert probe.status == "unauthorized"

    async def test_connection_failure_is_unreachable(self):
        import httpx

        from core.services.llm._vllm_preflight import probe_vllm

        def handler(request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("refused")

        probe = await probe_vllm(
            "http://gpu:8000/v1", None, transport=httpx.MockTransport(handler)
        )
        assert probe.status == "unreachable"
