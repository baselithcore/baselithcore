"""Several vLLM servers, one per model: route by model, never by port.

A vLLM server serves one model, so a second model means a second server (and
port). The operator lists the servers once in ``LLM_VLLM_ENDPOINTS``; the
framework learns which models each serves from ``GET /v1/models`` and sends a
call for a model to the server that serves it. A pin names a model — moving a
model to another port does not break it.
"""

from __future__ import annotations

import os
from unittest.mock import AsyncMock, patch

import pytest

from core.config.services import LLMConfig
from core.services.llm.preflight import VLLMProbe
from core.services.llm.vllm_endpoints import (
    VLLMEndpointRegistry,
    get_vllm_registry,
    vllm_endpoints,
)

A = "http://gpu:8002/v1"
B = "http://gpu:8003/v1"
_PROBE = "core.services.llm.vllm_endpoints.probe_vllm"
_PROBE_SYNC = "core.services.llm.vllm_endpoints.probe_vllm_sync"
_PREFLIGHT_PROBE = "core.services.llm._vllm_preflight.probe_vllm"


def _config(**kwargs) -> LLMConfig:
    env = kwargs.pop("env", {})
    with patch.dict(os.environ, env, clear=True):
        return LLMConfig(_env_file=None, **kwargs)


def _catalog(mapping: dict[str, VLLMProbe]):
    async def probe(endpoint, _key, timeout=2.0, **_kw):
        return mapping[endpoint]

    return probe


@pytest.fixture(autouse=True)
def _fresh_registry():
    get_vllm_registry().reset()
    yield
    get_vllm_registry().reset()


class TestEndpointList:
    def test_the_list_is_normalised_and_deduplicated(self):
        config = _config(
            provider="openai",
            model="m",
            env={
                "LLM_VLLM_ENDPOINTS": "http://gpu:8002, http://gpu:8003/v1/ ,http://gpu:8002/v1"
            },
        )
        assert vllm_endpoints(config) == [A, B]

    def test_the_single_endpoint_setting_still_works(self):
        config = _config(
            provider="openai", model="m", env={"LLM_VLLM_API_BASE": "http://gpu:8002"}
        )
        assert vllm_endpoints(config) == [A]

    def test_both_settings_merge_list_first(self):
        config = _config(
            provider="openai",
            model="m",
            env={"LLM_VLLM_ENDPOINTS": B, "LLM_VLLM_API_BASE": "http://gpu:8002"},
        )
        assert vllm_endpoints(config) == [B, A]

    def test_nothing_configured_is_empty(self):
        assert vllm_endpoints(_config(provider="openai", model="m")) == []

    def test_configured_means_at_least_one_endpoint(self):
        from core.services.llm.runtime import api_base_for, provider_configured

        config = _config(provider="openai", model="m", env={"LLM_VLLM_ENDPOINTS": B})
        assert provider_configured(config, "vllm") is True
        assert api_base_for(config, "vllm") == B


class TestRouting:
    async def test_a_model_goes_to_the_server_that_serves_it(self):
        reg = VLLMEndpointRegistry()
        probe = _catalog(
            {A: VLLMProbe("ok", {"qwen"}), B: VLLMProbe("ok", {"llama-3.1-8b"})}
        )
        with patch(_PROBE, probe):
            assert await reg.endpoint_for("llama-3.1-8b", [A, B], None) == B
            assert await reg.endpoint_for("qwen", [A, B], None) == A

    async def test_a_single_endpoint_needs_no_discovery(self):
        reg = VLLMEndpointRegistry()
        probe = AsyncMock()
        with patch(_PROBE, probe):
            assert await reg.endpoint_for("anything", [A], None) == A
        probe.assert_not_called()

    async def test_the_catalog_is_cached(self):
        reg = VLLMEndpointRegistry(ttl=60)
        probe = AsyncMock(
            side_effect=_catalog(
                {A: VLLMProbe("ok", {"qwen"}), B: VLLMProbe("ok", {"x"})}
            )
        )
        with patch(_PROBE, probe):
            await reg.endpoint_for("qwen", [A, B], None)
            await reg.endpoint_for("x", [A, B], None)
        assert probe.await_count == 2  # one per endpoint, once

    async def test_an_unknown_model_triggers_one_refresh(self):
        """A model added to a server shows up without waiting for the TTL."""
        reg = VLLMEndpointRegistry(ttl=3600, miss_refresh=0)
        state = {B: VLLMProbe("ok", set())}

        async def probe(endpoint, _key, timeout=2.0, **_kw):
            return state.get(endpoint, VLLMProbe("ok", {"qwen"}))

        with patch(_PROBE, probe):
            assert await reg.endpoint_for("new-model", [A, B], None) is None
            state[B] = VLLMProbe("ok", {"new-model"})
            assert await reg.endpoint_for("new-model", [A, B], None) == B

    async def test_a_down_server_does_not_hide_the_others(self):
        reg = VLLMEndpointRegistry()
        probe = _catalog({A: VLLMProbe("unreachable"), B: VLLMProbe("ok", {"llama"})})
        with patch(_PROBE, probe):
            assert await reg.endpoint_for("llama", [A, B], None) == B

    async def test_the_first_server_wins_a_duplicate(self):
        reg = VLLMEndpointRegistry()
        probe = _catalog({A: VLLMProbe("ok", {"qwen"}), B: VLLMProbe("ok", {"qwen"})})
        with patch(_PROBE, probe):
            assert await reg.endpoint_for("qwen", [A, B], None) == A

    async def test_served_lists_every_reachable_model(self):
        reg = VLLMEndpointRegistry()
        probe = _catalog({A: VLLMProbe("ok", {"qwen"}), B: VLLMProbe("unreachable")})
        with patch(_PROBE, probe):
            served = await reg.served([A, B], None)
        assert served == {A: {"qwen"}}

    def test_sync_resolution_for_plugins_holding_their_own_sdk(self):
        reg = VLLMEndpointRegistry()

        def probe(endpoint, _key, timeout=2.0):
            return {A: VLLMProbe("ok", {"qwen"}), B: VLLMProbe("ok", {"llama"})}[
                endpoint
            ]

        with patch(_PROBE_SYNC, probe):
            assert reg.endpoint_for_sync("llama", [A, B], None) == B


class TestGovernedAndPreflight:
    def test_a_pin_hands_plugins_the_server_of_its_model(self):
        from core.services.llm.governed import resolve_governed_client_config
        from core.services.llm.policy import (
            PluginLLMPolicy,
            set_plugin_llm_policy_resolver,
        )

        config = _config(
            provider="openai",
            model="gpt-4o-mini",
            env={"LLM_VLLM_ENDPOINTS": f"{A},{B}"},
        )

        def probe(endpoint, _key, timeout=2.0):
            return {A: VLLMProbe("ok", {"qwen"}), B: VLLMProbe("ok", {"llama"})}[
                endpoint
            ]

        set_plugin_llm_policy_resolver(
            lambda _n, _s=None: PluginLLMPolicy(provider="vllm", model="llama")
        )
        try:
            with (
                patch("core.services.llm.governed.get_llm_config", return_value=config),
                patch(
                    "core.services.llm.runtime.resolve_llm_credential",
                    return_value=None,
                ),
                patch(_PROBE_SYNC, probe),
            ):
                gov = resolve_governed_client_config("wikigen")
        finally:
            set_plugin_llm_policy_resolver(None)
        assert gov is not None and gov.api_base == B

    async def test_preflight_checks_every_server_and_the_union(self):
        from core.services.llm.preflight import check_local_endpoints

        config = _config(
            provider="vllm",
            model="llama",
            fallback_chain="vllm:mistral",
            env={"LLM_VLLM_ENDPOINTS": f"{A},{B}"},
        )
        probe = _catalog({A: VLLMProbe("ok", {"qwen"}), B: VLLMProbe("unreachable")})
        with (
            patch(_PREFLIGHT_PROBE, probe),
            patch(
                "core.services.llm.preflight.probe_ollama",
                AsyncMock(return_value={"llama3.2:latest"}),
            ),
        ):
            findings = await check_local_endpoints(config)
        codes = sorted(f.code for f in findings if f.code.startswith("vllm"))
        # B is down, so llama/mistral may live there: unverified, not missing.
        assert codes == ["vllm_models_unverified", "vllm_unreachable"]

    async def test_a_model_no_answering_server_serves_is_missing(self):
        from core.services.llm.preflight import check_local_endpoints

        config = _config(
            provider="vllm", model="llama", env={"LLM_VLLM_ENDPOINTS": f"{A},{B}"}
        )
        probe = _catalog({A: VLLMProbe("ok", {"qwen"}), B: VLLMProbe("ok", {"x"})})
        with (
            patch(_PREFLIGHT_PROBE, probe),
            patch(
                "core.services.llm.preflight.probe_ollama",
                AsyncMock(return_value={"llama3.2:latest"}),
            ),
        ):
            findings = await check_local_endpoints(config)
        [finding] = [f for f in findings if f.code.startswith("vllm")]
        assert finding.code == "vllm_model_missing"
        assert "qwen, x" in finding.remedy
