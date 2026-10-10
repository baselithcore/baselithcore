"""ElevenLabs continuity: request stitching, language, normalisation and per-call tuning."""

from __future__ import annotations

import json
from typing import Any

import httpx
import pytest
from elevenlabs_rig import TARGET, VOICE, client, make_service

from baselith.core.services.voice import SpeechContext, SpeechTuning


def _ok(seen: list[httpx.Request], request_id: str | None = "req_1") -> Any:
    def handle(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        headers = {"request-id": request_id} if request_id else {}
        return httpx.Response(200, content=b"mp3", headers=headers)

    return handle


async def _speak(c: Any, model: str, **kw: Any) -> Any:
    return await c.speech_result(
        TARGET,
        kw.pop("text", "Ciao a tutti."),
        voice_id=VOICE,
        model=model,
        output_format="mp3_44100_128",
        stability=0.4,
        similarity_boost=0.8,
        style=0.2,
        **kw,
    )


def _body(req: httpx.Request) -> dict[str, Any]:
    return json.loads(req.content)


class TestStitchingPayload:
    async def test_request_id_is_read_from_the_response(self) -> None:
        seen: list[httpx.Request] = []
        result = await _speak(client(_ok(seen)), "eleven_multilingual_v2")
        assert result.audio == b"mp3"
        assert result.request_id == "req_1"

    async def test_no_header_means_no_request_id(self) -> None:
        seen: list[httpx.Request] = []
        result = await _speak(client(_ok(seen, None)), "eleven_multilingual_v2")
        assert result.request_id is None

    async def test_context_text_goes_on_the_wire(self) -> None:
        seen: list[httpx.Request] = []
        ctx = SpeechContext(previous_text="Prima frase.", next_text="Poi questa.")
        await _speak(client(_ok(seen)), "eleven_multilingual_v2", context=ctx)
        body = _body(seen[0])
        assert body["previous_text"] == "Prima frase."
        assert body["next_text"] == "Poi questa."
        assert "previous_request_ids" not in body

    async def test_request_ids_take_over_previous_text(self) -> None:
        seen: list[httpx.Request] = []
        ctx = SpeechContext(
            previous_text="Prima.", previous_request_ids=("a", "b", "c", "d")
        )
        await _speak(client(_ok(seen)), "eleven_multilingual_v2", context=ctx)
        body = _body(seen[0])
        # Ids win (the provider ignores previous_text when ids are sent) and at most three.
        assert body["previous_request_ids"] == ["b", "c", "d"]
        assert "previous_text" not in body

    async def test_invalid_request_ids_are_dropped(self) -> None:
        seen: list[httpx.Request] = []
        ctx = SpeechContext(
            previous_text="Prima.", previous_request_ids=("bad id!", "../x")
        )
        await _speak(client(_ok(seen)), "eleven_multilingual_v2", context=ctx)
        body = _body(seen[0])
        assert "previous_request_ids" not in body
        assert body["previous_text"] == "Prima."

    async def test_eleven_v3_gets_no_stitching(self) -> None:
        seen: list[httpx.Request] = []
        ctx = SpeechContext(
            previous_text="Prima.", next_text="Poi.", previous_request_ids=("a",)
        )
        await _speak(client(_ok(seen)), "eleven_v3", context=ctx)
        body = _body(seen[0])
        for key in ("previous_text", "next_text", "previous_request_ids"):
            assert key not in body

    async def test_context_text_is_bounded(self) -> None:
        seen: list[httpx.Request] = []
        ctx = SpeechContext(previous_text="x" * 5000, next_text="y" * 5000)
        await _speak(client(_ok(seen)), "eleven_flash_v2_5", context=ctx)
        body = _body(seen[0])
        assert len(body["previous_text"]) <= 600
        assert len(body["next_text"]) <= 600

    async def test_normalisation_is_sent_only_when_asked(self) -> None:
        seen: list[httpx.Request] = []
        c = client(_ok(seen))
        await _speak(c, "eleven_multilingual_v2")
        await _speak(
            c, "eleven_multilingual_v2", context=SpeechContext(normalization="on")
        )
        assert "apply_text_normalization" not in _body(seen[0])
        assert _body(seen[1])["apply_text_normalization"] == "on"

    async def test_unknown_normalisation_is_not_sent(self) -> None:
        seen: list[httpx.Request] = []
        ctx = SpeechContext(normalization="weird")  # type: ignore[arg-type]
        await _speak(client(_ok(seen)), "eleven_multilingual_v2", context=ctx)
        assert "apply_text_normalization" not in _body(seen[0])

    async def test_language_only_for_models_that_take_it(self) -> None:
        seen: list[httpx.Request] = []
        c = client(_ok(seen))
        await _speak(c, "eleven_multilingual_v2", language="it")
        await _speak(c, "eleven_flash_v2_5", language="it")
        assert "language_code" not in _body(seen[0])
        assert _body(seen[1])["language_code"] == "it"

    async def test_speed_is_clamped_into_the_providers_range(self) -> None:
        seen: list[httpx.Request] = []
        c = client(_ok(seen))
        await _speak(c, "eleven_multilingual_v2", speed=2.0)
        await _speak(c, "eleven_multilingual_v2", speed=1.0)
        assert _body(seen[0])["voice_settings"]["speed"] == 1.2
        assert "speed" not in _body(seen[1])["voice_settings"]


class TestServiceChain:
    async def test_language_context_and_request_id_reach_the_wire(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        seen: list[httpx.Request] = []
        svc = make_service(
            _ok(seen, "req_9"),
            monkeypatch,
            VOICE_ELEVENLABS_TTS_MODEL="eleven_flash_v2_5",
        )
        response = await svc.text_to_speech(
            "Ciao a tutti.",
            language="it",
            context=SpeechContext(previous_text="Prima.", normalization="auto"),
        )
        body = _body(seen[0])
        assert body["language_code"] == "it"
        assert body["previous_text"] == "Prima."
        assert body["apply_text_normalization"] == "auto"
        assert response.metadata["request_id"] == "req_9"
        assert response.metadata["stitched"] is True

    async def test_stitched_flag_is_false_without_context_or_for_v3(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        seen: list[httpx.Request] = []
        svc = make_service(
            _ok(seen), monkeypatch, VOICE_ELEVENLABS_TTS_MODEL="eleven_v3"
        )
        response = await svc.text_to_speech(
            "Ciao.", context=SpeechContext(previous_text="Prima.")
        )
        assert response.metadata["stitched"] is False


class TestTuningPrecedence:
    async def test_tuning_fills_what_the_operator_left_default(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        seen: list[httpx.Request] = []
        svc = make_service(_ok(seen), monkeypatch)
        await svc.text_to_speech(
            "Ciao.",
            tuning=SpeechTuning(
                model="eleven_multilingual_v2",
                stability=0.4,
                similarity_boost=0.8,
                style=0.2,
            ),
        )
        body = _body(seen[0])
        assert body["model_id"] == "eleven_multilingual_v2"
        assert body["voice_settings"]["stability"] == 0.4
        assert body["voice_settings"]["style"] == 0.2

    async def test_explicit_env_beats_the_tuning(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        seen: list[httpx.Request] = []
        svc = make_service(
            _ok(seen),
            monkeypatch,
            VOICE_ELEVENLABS_TTS_MODEL="eleven_multilingual_v2",
            VOICE_ELEVENLABS_STABILITY="0.9",
        )
        await svc.text_to_speech(
            "Ciao.",
            tuning=SpeechTuning(model="eleven_flash_v2_5", stability=0.1, style=0.7),
        )
        body = _body(seen[0])
        assert body["model_id"] == "eleven_multilingual_v2"
        assert body["voice_settings"]["stability"] == 0.9
        assert (
            body["voice_settings"]["style"] == 0.7
        )  # not set by the operator: tuning applies
        assert svc.elevenlabs_explicit() == frozenset({"model", "stability"})

    async def test_admin_override_model_beats_the_tuning(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from baselith.core.services.voice import VoiceOverrides

        seen: list[httpx.Request] = []
        svc = make_service(
            _ok(seen),
            monkeypatch,
            overrides=lambda: VoiceOverrides(elevenlabs_tts_model="eleven_v3"),
        )
        await svc.text_to_speech(
            "Ciao.", tuning=SpeechTuning(model="eleven_flash_v2_5")
        )
        assert _body(seen[0])["model_id"] == "eleven_v3"
        assert "model" in svc.elevenlabs_explicit()
