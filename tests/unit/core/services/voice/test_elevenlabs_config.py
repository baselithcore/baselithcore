"""VoiceConfig: the ElevenLabs settings and their validation."""

from __future__ import annotations

import pytest
from elevenlabs_rig import KEY, VOICE
from pydantic import SecretStr, ValidationError

from baselith.core.config.multimodal import VoiceConfig


class TestConfig:
    def _env(self, monkeypatch: pytest.MonkeyPatch, **env: str) -> VoiceConfig:
        for var in list(env) + ["VOICE_PROVIDER", "VOICE_ALLOW_INTERNAL_ENDPOINTS"]:
            monkeypatch.delenv(var, raising=False)
        for name, value in env.items():
            monkeypatch.setenv(name, value)
        return VoiceConfig()

    def test_defaults(self, monkeypatch: pytest.MonkeyPatch) -> None:
        cfg = self._env(monkeypatch)
        assert cfg.elevenlabs_base_url == "https://api.elevenlabs.io"
        assert cfg.elevenlabs_tts_model == "eleven_flash_v2_5"
        assert cfg.elevenlabs_stt_model == "scribe_v2"
        assert cfg.elevenlabs_output_format == "mp3_44100_128"
        assert cfg.elevenlabs_voice_id == VOICE
        assert cfg.provider == "openai"  # the sidecar default is untouched

    def test_unknown_provider_is_refused(self, monkeypatch: pytest.MonkeyPatch) -> None:
        with pytest.raises(ValidationError):
            self._env(monkeypatch, VOICE_PROVIDER="elevenlab")

    @pytest.mark.parametrize(
        "url",
        [
            "http://api.elevenlabs.io",
            "ftp://api.elevenlabs.io",
            "https://user:pw@api.elevenlabs.io",
            "https://api.elevenlabs.io/?x=1",
            "https://api.elevenlabs.io/#f",
            "https:///nohost",
            "api.elevenlabs.io",
        ],
    )
    def test_base_url_must_be_plain_https(
        self, url: str, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        with pytest.raises(ValidationError):
            self._env(monkeypatch, VOICE_ELEVENLABS_BASE_URL=url)

    def test_http_only_with_the_local_development_switch(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        cfg = self._env(
            monkeypatch,
            VOICE_ELEVENLABS_BASE_URL="http://127.0.0.1:8830/",
            VOICE_ALLOW_INTERNAL_ENDPOINTS="true",
        )
        assert cfg.elevenlabs_base_url == "http://127.0.0.1:8830"

    def test_residency_url_and_trailing_slash(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        cfg = self._env(
            monkeypatch,
            VOICE_ELEVENLABS_BASE_URL="https://api.eu.residency.elevenlabs.io/",
        )
        assert cfg.elevenlabs_base_url == "https://api.eu.residency.elevenlabs.io"

    def test_key_is_a_secret_and_both_names_work(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        cfg = self._env(monkeypatch, VOICE_ELEVENLABS_API_KEY=KEY)
        assert isinstance(cfg.elevenlabs_api_key, SecretStr)
        assert KEY not in repr(cfg) and KEY not in str(cfg.model_dump())

    def test_legacy_model_env_name_still_works(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("VOICE_ELEVENLABS_TTS_MODEL", raising=False)
        cfg = self._env(monkeypatch, VOICE_ELEVENLABS_MODEL_ID="eleven_v3")
        assert cfg.elevenlabs_tts_model == "eleven_v3"

    def test_output_format_is_mp3_only(self, monkeypatch: pytest.MonkeyPatch) -> None:
        with pytest.raises(ValidationError):
            self._env(monkeypatch, VOICE_ELEVENLABS_OUTPUT_FORMAT="pcm_16000")
