"""Per-provider credentials must answer to their prefixed name.

An explicit ``alias=`` REPLACES a class's ``env_prefix`` instead of adding to it.
``VisionConfig``, ``VoiceConfig``, ``FineTuningConfig`` and ``VectorStoreConfig``
each had fields aliased to a bare vendor name, so the prefixed names published in
``.env.example`` and the docs bound nothing: setting
``VISION_ANTHROPIC_API_KEY`` left the field ``None``, and the fine-tuning keys
silently shared the chat provider's ``OPENAI_API_KEY``.

Each field now takes ``AliasChoices(prefixed, bare)`` — prefixed first, so a
deployment can namespace per provider, bare still accepted so existing
environments keep working.
"""

import pytest

from core.config.multimodal import FineTuningConfig, VisionConfig, VoiceConfig
from core.config.vectorstore import VectorStoreConfig

PREFIXED = [
    ("VISION_ANTHROPIC_API_KEY", VisionConfig, "anthropic_api_key"),
    ("VISION_GOOGLE_API_KEY", VisionConfig, "google_api_key"),
    ("VOICE_ELEVENLABS_API_KEY", VoiceConfig, "elevenlabs_api_key"),
    ("VOICE_GOOGLE_API_KEY", VoiceConfig, "google_api_key"),
    ("FINETUNE_OPENAI_API_KEY", FineTuningConfig, "openai_api_key"),
    ("FINETUNE_TOGETHER_API_KEY", FineTuningConfig, "together_api_key"),
]

#: ``(bare name, prefixed name that outranks it, class, field)``. The prefixed
#: name is cleared per case: a checkout whose own ``.env`` sets it would
#: otherwise mask the fallback under test.
BARE_FALLBACKS = [
    (
        "ANTHROPIC_API_KEY",
        "VISION_ANTHROPIC_API_KEY",
        VisionConfig,
        "anthropic_api_key",
    ),
    (
        "ELEVENLABS_API_KEY",
        "VOICE_ELEVENLABS_API_KEY",
        VoiceConfig,
        "elevenlabs_api_key",
    ),
    ("OPENAI_API_KEY", "FINETUNE_OPENAI_API_KEY", FineTuningConfig, "openai_api_key"),
]


def _secret(config, field: str) -> str | None:
    value = getattr(config, field)
    return None if value is None else value.get_secret_value()


class TestPrefixedNames:
    """The documented, namespaced name must reach the field."""

    @pytest.mark.parametrize(("env", "config_class", "field"), PREFIXED)
    def test_prefixed_name_binds(self, monkeypatch, env, config_class, field):
        monkeypatch.setenv(env, "prefixed-value")

        assert _secret(config_class(), field) == "prefixed-value"

    def test_vision_ollama_host_accepts_the_prefixed_name(self, monkeypatch):
        monkeypatch.setenv("VISION_OLLAMA_HOST", "http://vision-host:11434")

        assert VisionConfig().ollama_url == "http://vision-host:11434"

    def test_voice_google_credentials_accept_the_prefixed_name(self, monkeypatch):
        monkeypatch.setenv("VOICE_GOOGLE_APPLICATION_CREDENTIALS", "/creds.json")

        assert VoiceConfig().google_credentials_path == "/creds.json"

    def test_qdrant_mode_accepts_the_prefixed_name(self, monkeypatch):
        monkeypatch.setenv("VECTORSTORE_QDRANT_MODE", "local")

        assert VectorStoreConfig().qdrant_mode == "local"


class TestBareFallbacks:
    """Environments set up before the fix must keep working."""

    @pytest.mark.parametrize(
        ("env", "outranking", "config_class", "field"), BARE_FALLBACKS
    )
    def test_bare_vendor_name_still_binds(
        self, monkeypatch, env, outranking, config_class, field
    ):
        monkeypatch.delenv(outranking, raising=False)
        monkeypatch.setenv(env, "bare-value")

        assert _secret(config_class(), field) == "bare-value"

    def test_bare_qdrant_mode_still_binds(self, monkeypatch):
        monkeypatch.delenv("VECTORSTORE_QDRANT_MODE", raising=False)
        monkeypatch.setenv("QDRANT_MODE", "memory")

        assert VectorStoreConfig().qdrant_mode == "memory"


class TestPrecedence:
    """The namespaced name wins, so a per-provider override is possible."""

    def test_prefixed_name_beats_the_bare_fallback(self, monkeypatch):
        monkeypatch.setenv("OPENAI_API_KEY", "chat-key")
        monkeypatch.setenv("FINETUNE_OPENAI_API_KEY", "finetune-key")

        assert _secret(FineTuningConfig(), "openai_api_key") == "finetune-key"
