"""Per-provider endpoint resolution and non-empty provider errors.

Both guard the same operator experience: pinning a plugin to a second provider
must talk to *that* provider's server, and when it cannot, the failure has to
say what happened.
"""

from unittest.mock import patch

import httpx
import pytest

from core.config.services import LLMConfig
from core.services.llm.exceptions import describe_exception
from core.services.llm.governed import resolve_governed_client_config
from core.services.llm.policy import PluginLLMPolicy, set_plugin_llm_policy_resolver
from core.services.llm.runtime import api_base_for


def _config(**kwargs) -> LLMConfig:
    """A config built from explicit values only.

    ``LLMConfig`` is a ``BaseSettings``: without a cleared environment an
    ambient ``OLLAMA_HOST`` (or the repo's ``.env``) leaks into the very fields
    these tests assert on.
    """
    env = kwargs.pop("env", {})
    with patch.dict("os.environ", env, clear=True):
        return LLMConfig(**kwargs)


class TestApiBaseFor:
    """``api_base`` belongs to one provider — never to whoever asks."""

    def test_default_provider_gets_the_configured_base(self):
        config = _config(
            provider="ollama", model="llama3.2", api_base="http://box:11434"
        )
        assert api_base_for(config, "ollama") == "http://box:11434"

    def test_other_providers_get_nothing(self):
        """The regression this module exists for.

        A deployment defaulting to an OpenAI-compatible gateway used to hand
        that URL to a policy-routed Ollama call. The gateway serves nothing on
        ``/api/chat``, so the call stalled until the read timeout — reported as
        a timeout, i.e. a misconfiguration disguised as a slow model.
        """
        config = _config(
            provider="openai",
            model="gpt-4o-mini",
            api_base="https://openai-gateway.internal/v1",
        )
        assert api_base_for(config, "ollama") is None
        assert api_base_for(config, "anthropic") is None
        assert api_base_for(config, "openai") == "https://openai-gateway.internal/v1"

    def test_dedicated_ollama_endpoint_wins(self):
        config = _config(
            provider="openai",
            model="gpt-4o-mini",
            api_base="https://openai-gateway.internal/v1",
            env={"LLM_OLLAMA_API_BASE": "http://ollama-box:11434"},
        )
        assert api_base_for(config, "ollama") == "http://ollama-box:11434"
        # …and it never bleeds into another provider.
        assert api_base_for(config, "anthropic") is None

    def test_ollama_host_is_the_last_resort(self):
        """``OLLAMA_HOST`` is what the SDK reads, so it is honoured — last.

        It is read at call time, not baked into the config, which is why the
        environment stays patched around the lookup.
        """
        config = _config(provider="openai", model="gpt-4o-mini")
        with patch.dict("os.environ", {"OLLAMA_HOST": "http://host-var:11434"}):
            assert api_base_for(config, "ollama") == "http://host-var:11434"

    def test_explicit_configuration_outranks_ollama_host(self):
        """``OLLAMA_HOST`` is often exported machine-wide.

        Letting it shadow an explicit ``LLM_API_BASE`` would silently redirect
        an Ollama-default deployment to localhost — a behaviour change for
        every box that happens to export the variable.
        """
        config = _config(
            provider="ollama", model="llama3.2", api_base="http://box:11434"
        )
        with patch.dict("os.environ", {"OLLAMA_HOST": "http://ignored:11434"}):
            assert api_base_for(config, "ollama") == "http://box:11434"

    def test_dedicated_field_outranks_everything(self):
        config = _config(
            provider="ollama",
            model="llama3.2",
            api_base="http://box:11434",
            env={"LLM_OLLAMA_API_BASE": "http://dedicated:11434"},
        )
        with patch.dict("os.environ", {"OLLAMA_HOST": "http://ignored:11434"}):
            assert api_base_for(config, "ollama") == "http://dedicated:11434"

    def test_no_endpoint_configured_is_none(self):
        config = _config(provider="openai", model="gpt-4o-mini")
        with patch.dict("os.environ", {}, clear=True):
            assert api_base_for(config, "ollama") is None


class TestGovernedEndpoint:
    """The governed seam obeys the same rule as the funnel."""

    @pytest.fixture(autouse=True)
    def _clear_resolver(self):
        yield
        set_plugin_llm_policy_resolver(None)

    def test_cross_provider_pin_does_not_inherit_the_default_endpoint(self):
        config = _config(
            provider="openai",
            model="gpt-4o-mini",
            api_base="https://openai-gateway.internal/v1",
        )
        set_plugin_llm_policy_resolver(
            lambda name, scope=None: PluginLLMPolicy(
                provider="ollama", model="llama3.2"
            )
        )
        with patch("core.services.llm.governed.get_llm_config", return_value=config):
            resolved = resolve_governed_client_config("some-plugin")
        assert resolved is not None
        assert resolved.provider == "ollama"
        assert resolved.api_base is None

    def test_same_provider_pin_keeps_the_endpoint(self):
        config = _config(
            provider="ollama", model="llama3.2", api_base="http://box:11434"
        )
        set_plugin_llm_policy_resolver(
            lambda name, scope=None: PluginLLMPolicy(model="mistral")
        )
        with patch("core.services.llm.governed.get_llm_config", return_value=config):
            resolved = resolve_governed_client_config("some-plugin")
        assert resolved is not None
        assert resolved.api_base == "http://box:11434"


class TestDescribeException:
    """A wrapped failure must never render as an empty message."""

    @pytest.mark.parametrize(
        "exc",
        [httpx.ReadTimeout(""), httpx.ConnectTimeout(""), httpx.PoolTimeout("")],
    )
    def test_blank_upstream_message_falls_back_to_the_type(self, exc):
        """``str(httpx.ReadTimeout())`` is ``""``.

        A hung local model server produces exactly this, and the provider used
        to surface it as ``Ollama error:`` — a failure with no cause and
        nothing to act on.
        """
        assert str(exc) == ""
        assert describe_exception(exc) == type(exc).__name__

    def test_a_real_message_is_preserved(self):
        assert describe_exception(ValueError("no credits remaining")) == (
            "no credits remaining"
        )

    def test_whitespace_only_message_counts_as_blank(self):
        assert describe_exception(RuntimeError("   ")) == "RuntimeError"


class TestFallbackStageEndpoint:
    """A fallback stage is a provider switch too — same endpoint rule."""

    def test_stage_clone_does_not_inherit_the_primary_endpoint(self):
        """The shape this deployment actually runs.

        ``LLM_FALLBACK_CHAIN=ollama:…`` behind a hosted default is the standard
        way to keep working when the hosted provider answers 429. The stage
        clone used to carry the primary's ``api_base``, so the local stage
        dialled the hosted gateway and stalled until the read timeout — turning
        a graceful degradation into a hang.
        """
        from unittest.mock import AsyncMock

        from core.services.llm.fallback_runtime import (
            _clone_service,
            reset_fallback_services,
        )
        from core.services.llm.service import LLMService

        reset_fallback_services()
        base_config = _config(
            provider="openai",
            model="gpt-4o-mini",
            api_base="https://openai-gateway.internal/v1",
            fallback_chain="ollama:llama3.2",
        )
        with patch.object(LLMService, "_create_provider", return_value=AsyncMock()):
            primary = LLMService(config=base_config, enable_cache=False)
            with patch.dict("os.environ", {}, clear=True):
                clone = _clone_service(primary, "ollama", "llama3.2")

        assert clone.config.provider == "ollama"
        assert clone.config.api_base is None
        # A stage must never recurse into its own chain.
        assert clone.config.fallback_chain == ""
        reset_fallback_services()
