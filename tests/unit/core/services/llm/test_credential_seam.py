"""The core credential seam: registration, degradation, env precedence."""

import pytest
from pydantic import SecretStr

from core.config.services import LLMConfig
from core.services.llm.credentials import (
    resolve_llm_credential,
    set_llm_credential_resolver,
)
from core.services.llm.runtime import api_key_for, provider_configured

_ENV_VARS = (
    "LLM_API_KEY",
    "LLM_ANTHROPIC_API_KEY",
    "ANTHROPIC_API_KEY",
    "OPENAI_API_KEY",
    "LLM_OPENAI_API_KEY",
    "LLM_HUGGINGFACE_API_KEY",
    "HF_TOKEN",
    "LLM_GEMINI_API_KEY",
    "GEMINI_API_KEY",
    "GOOGLE_API_KEY",
)


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    """Scrub host credentials and leave no resolver installed after a test."""
    for var in _ENV_VARS:
        monkeypatch.delenv(var, raising=False)
    set_llm_credential_resolver(None)
    yield
    set_llm_credential_resolver(None)


def _config(**overrides) -> LLMConfig:
    base = dict(provider="ollama", model="base-model", enable_cache=False)
    base.update(overrides)
    return LLMConfig(**base)


class TestCredentialSeam:
    def test_no_resolver_installed_yields_none(self):
        assert resolve_llm_credential("anthropic") is None

    def test_resolver_value_is_wrapped_in_secretstr(self):
        set_llm_credential_resolver(lambda _p: "k-stored")
        secret = resolve_llm_credential("anthropic")
        assert isinstance(secret, SecretStr)
        assert secret.get_secret_value() == "k-stored"

    def test_resolver_raising_degrades_to_none(self):
        def boom(_provider: str) -> str | None:
            raise RuntimeError("store down")

        set_llm_credential_resolver(boom)
        assert resolve_llm_credential("anthropic") is None

    def test_blank_stored_value_is_not_a_credential(self):
        set_llm_credential_resolver(lambda _p: "   ")
        assert resolve_llm_credential("anthropic") is None

    def test_non_string_resolver_value_degrades_to_none(self):
        set_llm_credential_resolver(lambda _p: 42)  # type: ignore[arg-type]
        assert resolve_llm_credential("anthropic") is None


class TestEnvironmentPrecedence:
    def test_env_key_wins_over_stored(self, monkeypatch):
        monkeypatch.setenv("ANTHROPIC_API_KEY", "k-env")
        set_llm_credential_resolver(lambda _p: "k-stored")
        assert api_key_for(_config(), "anthropic").get_secret_value() == "k-env"

    def test_stored_key_fills_the_gap(self):
        set_llm_credential_resolver(lambda _p: "k-stored")
        assert api_key_for(_config(), "anthropic").get_secret_value() == "k-stored"
        assert provider_configured(_config(), "anthropic") is True

    def test_default_provider_with_blank_primary_reaches_the_resolver(self):
        """LLM_PROVIDER=anthropic with a blank LLM_API_KEY must still find a
        stored key — the primary branch must not short-circuit on None."""
        set_llm_credential_resolver(lambda _p: "k-stored")
        cfg = _config(provider="anthropic")
        assert api_key_for(cfg, "anthropic").get_secret_value() == "k-stored"

    def test_config_only_lookup_ignores_the_seam(self):
        """api_key_from_config must never see a stored credential."""
        from core.services.llm.runtime import api_key_from_config

        set_llm_credential_resolver(lambda _p: "k-stored")
        assert api_key_from_config(_config(), "anthropic") is None

    def test_config_only_lookup_still_reads_the_environment(self, monkeypatch):
        from core.services.llm.runtime import api_key_from_config

        monkeypatch.setenv("ANTHROPIC_API_KEY", "k-env")
        secret = api_key_from_config(_config(), "anthropic")
        assert secret is not None and secret.get_secret_value() == "k-env"

    def test_resolver_is_asked_for_the_right_provider(self):
        seen: list[str] = []

        def record(provider: str) -> str | None:
            seen.append(provider)
            return None

        set_llm_credential_resolver(record)
        api_key_for(_config(), "openai")
        assert seen == ["openai"]
