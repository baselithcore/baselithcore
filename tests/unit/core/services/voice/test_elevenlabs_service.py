"""VoiceService with the ElevenLabs provider: routing, overrides, usage, probe."""

from __future__ import annotations

import json
from typing import Any

import httpx
import pytest
from elevenlabs_rig import KEY, VOICE, Handler, error, make_service

from baselith.core.services.voice import (
    VoiceCapabilities,
    VoiceKeyInvalidError,
    VoiceLimitError,
    VoiceNotConfiguredError,
    VoiceOverrides,
)
from baselith.core.services.voice.models import VoiceProvider


class TestService:
    @pytest.mark.parametrize("blank", ["", "   "])
    async def test_a_blank_key_is_no_key(
        self, blank: str, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # ``VOICE_ELEVENLABS_API_KEY=`` is what a copied .env template leaves behind.
        svc = make_service(
            error(500), monkeypatch, key=None, VOICE_ELEVENLABS_API_KEY=blank
        )
        assert svc.capabilities() == VoiceCapabilities(False, False, None, None)
        with pytest.raises(VoiceNotConfiguredError):
            await svc.text_to_speech("ciao")

    async def test_tts_and_usage_in_characters(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from baselith.core.services.llm.usage_sinks import (
            register_usage_sink,
            unregister_usage_sink,
        )

        reports: list[Any] = []
        register_usage_sink(reports.append)
        try:
            svc = make_service(
                lambda _: httpx.Response(200, content=b"mp3"), monkeypatch
            )
            response = await svc.text_to_speech("Buongiorno a tutti")
        finally:
            unregister_usage_sink(reports.append)
        assert response.audio_bytes == b"mp3"
        assert response.provider == "elevenlabs"
        assert response.model == "eleven_flash_v2_5"
        assert response.metadata["voice"] == VOICE
        tts = [r for r in reports if r.model.startswith("tts:elevenlabs/")]
        assert tts and tts[0].usage.input_tokens == len("Buongiorno a tutti")

    async def test_stt_and_usage_in_seconds(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from baselith.core.services.llm.usage_sinks import (
            register_usage_sink,
            unregister_usage_sink,
        )

        reports: list[Any] = []
        register_usage_sink(reports.append)
        seen: list[httpx.Request] = []

        def handle(request: httpx.Request) -> httpx.Response:
            seen.append(request)
            return httpx.Response(
                200, json={"text": "ciao", "audio_duration_secs": 7.2}
            )

        try:
            svc = make_service(handle, monkeypatch)
            response = await svc.speech_to_text(
                audio_data=b"abcd", language="it", content_type="audio/webm;codecs=opus"
            )
        finally:
            unregister_usage_sink(reports.append)
        assert response.text == "ciao" and response.language == "it"
        assert (
            b"language_code" in seen[0].content
            and b'filename="audio.webm"' in seen[0].content
        )
        stt = [r for r in reports if r.model.startswith("stt:elevenlabs/")]
        assert stt and stt[0].usage.input_tokens == 8  # ceil(7.2 s)

    async def test_no_usage_is_reported_for_a_failed_call(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from baselith.core.services.llm.usage_sinks import (
            register_usage_sink,
            unregister_usage_sink,
        )

        reports: list[Any] = []
        register_usage_sink(reports.append)
        try:
            svc = make_service(error(401), monkeypatch)
            with pytest.raises(VoiceKeyInvalidError):
                await svc.text_to_speech("ciao")
        finally:
            unregister_usage_sink(reports.append)
        assert reports == []

    async def test_missing_key_is_not_configured(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        svc = make_service(error(500), monkeypatch, key=None)
        with pytest.raises(VoiceNotConfiguredError):
            await svc.text_to_speech("ciao")
        with pytest.raises(VoiceNotConfiguredError):
            await svc.speech_to_text(audio_data=b"abc")
        assert svc.capabilities() == VoiceCapabilities(False, False, None, None)

    async def test_capabilities_cover_both_directions(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        svc = make_service(error(500), monkeypatch)
        assert svc.capabilities() == VoiceCapabilities(
            True, True, "elevenlabs", "elevenlabs"
        )

    async def test_text_cap_applies(self, monkeypatch: pytest.MonkeyPatch) -> None:
        svc = make_service(error(500), monkeypatch, VOICE_MAX_TTS_CHARS="10")
        with pytest.raises(VoiceLimitError):
            await svc.text_to_speech("x" * 11)

    async def test_env_key_beats_the_override_key(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        seen: list[str] = []

        def handle(request: httpx.Request) -> httpx.Response:
            seen.append(request.headers["xi-api-key"])
            return httpx.Response(200, content=b"a")

        both = VoiceOverrides(elevenlabs_api_key="from-the-vault")
        svc = make_service(handle, monkeypatch, overrides=lambda: both)
        await svc.text_to_speech("ciao")
        assert seen == [KEY]
        only_vault = make_service(handle, monkeypatch, key=None, overrides=lambda: both)
        await only_vault.text_to_speech("ciao")
        assert seen[-1] == "from-the-vault"

    async def test_override_chooses_provider_voice_and_model(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        seen: list[httpx.Request] = []

        def handle(request: httpx.Request) -> httpx.Response:
            seen.append(request)
            return httpx.Response(200, content=b"a")

        state = {
            "o": VoiceOverrides(
                provider="elevenlabs",
                elevenlabs_voice_id="VoiceB12345",
                elevenlabs_tts_model="eleven_multilingual_v2",
            )
        }
        svc = make_service(
            handle,
            monkeypatch,
            VOICE_PROVIDER="openai_compatible",
            overrides=lambda: state["o"],
        )
        assert svc.default_provider == VoiceProvider.ELEVENLABS
        await svc.text_to_speech("ciao")
        assert seen[0].url.path.endswith("/VoiceB12345")
        assert json.loads(seen[0].content)["model_id"] == "eleven_multilingual_v2"
        state["o"] = VoiceOverrides(provider="sidecar")  # live: next call, no restart
        assert svc.default_provider == VoiceProvider.OPENAI_COMPATIBLE

    async def test_a_failing_override_source_means_no_override(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        def boom() -> VoiceOverrides:
            raise RuntimeError("db down")

        svc = make_service(
            lambda _: httpx.Response(200, content=b"a"), monkeypatch, overrides=boom
        )
        assert (await svc.text_to_speech("ciao")).audio_bytes == b"a"

    async def test_voices_and_probe(self, monkeypatch: pytest.MonkeyPatch) -> None:
        def handle(request: httpx.Request) -> httpx.Response:
            if request.url.path == "/v1/user/subscription":
                return httpx.Response(
                    200, json={"character_count": 10, "character_limit": 100}
                )
            return httpx.Response(
                200, json={"voices": [{"voice_id": "a1", "name": "A"}]}
            )

        svc = make_service(handle, monkeypatch)
        assert [v.id for v in await svc.list_voices()] == ["a1"]
        probe = await svc.probe_elevenlabs()
        assert (
            probe.ok,
            probe.code,
            probe.characters_used,
            probe.characters_limit,
        ) == (True, "ok", 10, 100)

    async def test_probe_without_the_user_permission_is_still_ok(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        def handle(request: httpx.Request) -> httpx.Response:
            if request.url.path == "/v1/user/subscription":
                return httpx.Response(
                    401, json={"detail": {"status": "missing_permissions"}}
                )
            return httpx.Response(200, json={"voices": []})

        probe = await make_service(handle, monkeypatch).probe_elevenlabs()
        assert probe.ok and probe.characters_used is None

    @pytest.mark.parametrize(
        ("handler", "code"),
        [
            (error(401, "invalid_api_key"), "key_invalid"),
            (error(401, "missing_permissions"), "permission_missing"),
            (error(401, "quota_exceeded"), "quota_exceeded"),
            (error(500), "provider_unavailable"),
        ],
    )
    async def test_probe_codes(
        self, handler: Handler, code: str, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        probe = await make_service(handler, monkeypatch).probe_elevenlabs()
        assert (probe.ok, probe.code) == (False, code)
        assert KEY not in repr(probe)

    async def test_probe_reports_an_exhausted_allowance(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        def handle(request: httpx.Request) -> httpx.Response:
            if request.url.path == "/v1/user/subscription":
                return httpx.Response(
                    200, json={"character_count": 100, "character_limit": 100}
                )
            return httpx.Response(200, json={"voices": []})

        probe = await make_service(handle, monkeypatch).probe_elevenlabs()
        assert (probe.ok, probe.code) == (False, "quota_exceeded")

    async def test_probe_without_a_key(self, monkeypatch: pytest.MonkeyPatch) -> None:
        probe = await make_service(error(500), monkeypatch, key=None).probe_elevenlabs()
        assert (probe.ok, probe.code) == (False, "not_configured")
