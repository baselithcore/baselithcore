"""Startup validation of the LLM posture.

Each case here is a deployment that used to boot successfully and then behave
in a way nobody chose: a container whose configuration never named a provider,
a fallback chain whose stages have no credentials, a local endpoint with
nothing listening, and a local model that was never pulled. All four are
invisible at runtime — they surface as a slow success, a misattributed error,
or a bill — and all four are decidable before the first request.
"""

import os
from unittest.mock import AsyncMock, patch

import pytest

from core.config.services import LLMConfig
from core.services.llm.preflight import (
    LLMPreflightError,
    check_configuration,
    check_local_endpoints,
    run_llm_preflight,
)


def _config(**kwargs) -> LLMConfig:
    """A config built from explicit values, with the environment cleared."""
    env = kwargs.pop("env", {})
    with patch.dict(os.environ, env, clear=True):
        return LLMConfig(_env_file=None, **kwargs)


def _codes(findings) -> set[str]:
    return {f.code for f in findings}


@pytest.fixture(autouse=True)
def _neutral_vision_config():
    """Pin the vision provider away from Ollama for every test here.

    Vision is configured separately from the LLM provider and its config is a
    process-wide singleton, so these tests otherwise assert against whichever
    ``VISION_PROVIDER`` the checkout's ``.env`` happens to carry — passing in
    one repository and failing in its sibling. The one test that cares about a
    vision target sets it up explicitly.
    """
    from core.config import multimodal

    original = multimodal._vision_config
    multimodal._vision_config = multimodal.VisionConfig(
        _env_file=None, provider="openai"
    )
    try:
        yield
    finally:
        multimodal._vision_config = original


class TestConfigurationFindings:
    def test_an_unset_provider_is_reported(self):
        """The quiet one: nothing is wrong, and inference runs on this host.

        A ConfigMap that forgot ``LLM_PROVIDER`` inherits the package default
        and serves every request from a local model, successfully. There is no
        later signal — which is exactly why it has to be an early one.
        """
        with patch.dict(os.environ, {}, clear=True):
            findings = check_configuration(LLMConfig(_env_file=None))
        assert "llm_provider_unset" in _codes(findings)

    def test_an_explicit_provider_is_not_reported(self):
        with patch.dict(os.environ, {"LLM_PROVIDER": "ollama"}, clear=True):
            findings = check_configuration(LLMConfig(_env_file=None))
        assert "llm_provider_unset" not in _codes(findings)

    def test_a_hosted_primary_without_a_key_is_reported(self):
        config = _config(provider="openai", model="gpt-4o-mini")
        with patch.dict(os.environ, {"LLM_PROVIDER": "openai"}, clear=True):
            findings = check_configuration(config)
        assert "primary_provider_unconfigured" in _codes(findings)

    def test_a_chain_stage_without_credentials_is_reported(self):
        """A decorative chain is worse than none: it is a promise kept nowhere."""
        config = _config(
            provider="ollama", model="llama3.2", fallback_chain="openai:gpt-4o-mini"
        )
        with patch.dict(os.environ, {"LLM_PROVIDER": "ollama"}, clear=True):
            findings = check_configuration(config)
        assert "chain_stage_unconfigured" in _codes(findings)

    def test_a_malformed_chain_is_reported_not_raised(self):
        """The chain is parsed per request, so a typo used to fail under load."""
        config = _config(provider="ollama", model="llama3.2", fallback_chain="openai")
        with patch.dict(os.environ, {"LLM_PROVIDER": "ollama"}, clear=True):
            findings = check_configuration(config)
        assert "chain_malformed" in _codes(findings)

    def test_a_local_chain_stage_needs_no_credentials(self):
        config = _config(
            provider="ollama", model="llama3.2", fallback_chain="ollama:small"
        )
        with patch.dict(os.environ, {"LLM_PROVIDER": "ollama"}, clear=True):
            findings = check_configuration(config)
        assert "chain_stage_unconfigured" not in _codes(findings)


@pytest.mark.asyncio
class TestLocalEndpointFindings:
    async def test_an_unreachable_endpoint_is_reported(self):
        config = _config(provider="ollama", model="llama3.2")
        with patch(
            "core.services.llm.preflight.probe_ollama", AsyncMock(return_value=None)
        ):
            findings = await check_local_endpoints(config)
        assert "ollama_unreachable" in _codes(findings)

    async def test_a_missing_model_is_reported_never_pulled(self):
        """Several gigabytes is an operator's decision, not a boot step."""
        config = _config(provider="ollama", model="llama3.2")
        with patch(
            "core.services.llm.preflight.probe_ollama",
            AsyncMock(return_value={"mistral:latest"}),
        ):
            findings = await check_local_endpoints(config)
        assert "ollama_model_missing" in _codes(findings)
        assert "ollama pull llama3.2" in findings[0].remedy

    async def test_a_bare_tag_matches_its_latest(self):
        """Ollama reports ``llama3.2:latest``; configuration says ``llama3.2``."""
        config = _config(provider="ollama", model="llama3.2")
        with patch(
            "core.services.llm.preflight.probe_ollama",
            AsyncMock(return_value={"llama3.2:latest"}),
        ):
            findings = await check_local_endpoints(config)
        assert findings == []

    async def test_a_local_chain_stage_is_probed_too(self):
        """The stage that only runs during an outage still has to exist."""
        config = _config(
            provider="openai",
            model="gpt-4o-mini",
            fallback_chain="ollama:qwen2.5:7b-instruct",
        )
        with patch(
            "core.services.llm.preflight.probe_ollama",
            AsyncMock(return_value=set()),
        ):
            findings = await check_local_endpoints(config)
        assert any("qwen2.5:7b-instruct" in f.message for f in findings)

    async def test_a_vision_provider_on_ollama_is_probed_too(self):
        """Vision is configured separately and lands on a host of its own.

        A deployment can be hosted for chat and local for vision without
        anything saying so, which is how ``llava`` comes to be missing on a
        machine that otherwise looks correctly configured.
        """
        from core.config import multimodal

        config = _config(provider="openai", model="gpt-4o-mini")
        multimodal._vision_config = multimodal.VisionConfig(
            _env_file=None,
            provider="ollama",
            ollama_model="llava:7b",
            ollama_url="http://localhost:11434",
        )
        with patch(
            "core.services.llm.preflight.probe_ollama",
            AsyncMock(return_value={"llama3.2:latest"}),
        ):
            findings = await check_local_endpoints(config)
        assert any("llava:7b" in f.message for f in findings)

    async def test_a_hosted_only_deployment_probes_nothing(self):
        config = _config(provider="openai", model="gpt-4o-mini")
        with patch("core.services.llm.preflight.probe_ollama", AsyncMock()) as probe:
            await check_local_endpoints(config)
        probe.assert_not_awaited()


@pytest.mark.asyncio
class TestModes:
    async def test_strict_raises_on_an_error_finding(self):
        config = _config(provider="ollama", model="llama3.2", preflight="strict")
        with (
            patch.dict(os.environ, {"LLM_PROVIDER": "ollama"}, clear=True),
            patch(
                "core.services.llm.preflight.probe_ollama",
                AsyncMock(return_value=None),
            ),
        ):
            with pytest.raises(LLMPreflightError, match="ollama_unreachable"):
                await run_llm_preflight(config)

    async def test_warn_reports_and_continues(self):
        config = _config(provider="ollama", model="llama3.2", preflight="warn")
        with (
            patch.dict(os.environ, {"LLM_PROVIDER": "ollama"}, clear=True),
            patch(
                "core.services.llm.preflight.probe_ollama",
                AsyncMock(return_value=None),
            ),
        ):
            findings = await run_llm_preflight(config)
        assert "ollama_unreachable" in _codes(findings)

    async def test_off_skips_everything(self):
        config = _config(provider="ollama", model="llama3.2", preflight="off")
        with patch("core.services.llm.preflight.probe_ollama", AsyncMock()) as probe:
            assert await run_llm_preflight(config) == []
        probe.assert_not_awaited()

    async def test_auto_is_strict_in_production(self):
        config = _config(provider="ollama", model="llama3.2", preflight="auto")
        with (
            patch.dict(os.environ, {"LLM_PROVIDER": "ollama"}, clear=True),
            patch("core.config.environment.is_production_env", return_value=True),
            patch(
                "core.services.llm.preflight.probe_ollama",
                AsyncMock(return_value=None),
            ),
        ):
            with pytest.raises(LLMPreflightError):
                await run_llm_preflight(config)

    async def test_auto_only_warns_outside_production(self):
        config = _config(provider="ollama", model="llama3.2", preflight="auto")
        with (
            patch.dict(os.environ, {"LLM_PROVIDER": "ollama"}, clear=True),
            patch("core.config.environment.is_production_env", return_value=False),
            patch(
                "core.services.llm.preflight.probe_ollama",
                AsyncMock(return_value=None),
            ),
        ):
            findings = await run_llm_preflight(config)
        assert findings  # reported, not raised


@pytest.mark.asyncio
class TestProbe:
    async def test_an_unreachable_host_returns_none_not_an_error(self):
        """Startup must survive a probe that cannot connect."""
        result = await run_probe("http://127.0.0.1:9")
        assert result is None


async def run_probe(url: str):
    from core.services.llm.preflight import probe_ollama

    return await probe_ollama(url, timeout=0.2)
