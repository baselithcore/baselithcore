"""OpenAI-compatible voice paths: sidecar mode, SSRF, caps, governance, usage.

The "server" is an in-process ASGI app injected as the inner transport of the
hardened client, so the SSRF guard is exercised exactly as in production.
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

import httpx
import pytest
from pydantic import SecretStr

from baselith.core.config.multimodal import VoiceConfig
from baselith.core.services.llm.governed import GovernedClientConfig
from baselith.core.services.voice import (
    VoiceCapabilities,
    VoiceEgressError,
    VoiceError,
    VoiceInputError,
    VoiceLimitError,
    VoiceNotConfiguredError,
    VoiceService,
    VoiceTimeoutError,
    VoiceUpstreamError,
)
from baselith.core.services.voice._openai_wire import OpenAIWireClient, WireTarget

BASE = "http://voice.test/v1"
LEAK = "sk-leaky-body-secret-123456"


class FakeServer:
    """A scriptable OpenAI-compatible audio server."""

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []
        self.status = 200
        self.text = "ciao mondo"
        self.audio = b"ID3-fake-mp3"
        self.body_override: bytes | None = None
        self.delay = 0.0

    async def __call__(self, scope: dict, receive: Any, send: Any) -> None:
        body = b""
        while True:
            msg = await receive()
            body += msg.get("body", b"")
            if not msg.get("more_body"):
                break
        headers = {k.decode(): v.decode() for k, v in scope["headers"]}
        self.calls.append({"path": scope["path"], "headers": headers, "body": body})
        if self.delay:
            await asyncio.sleep(self.delay)
        if self.status != 200:
            payload = f'{{"error":"boom {LEAK}"}}'.encode()
            ctype = b"application/json"
        elif scope["path"].endswith("/transcriptions"):
            payload = json.dumps({"text": self.text}).encode()
            ctype = b"application/json"
        else:
            payload = self.audio
            ctype = b"audio/mpeg"
        if self.body_override is not None:
            payload = self.body_override
        await send(
            {
                "type": "http.response.start",
                "status": self.status,
                "headers": [(b"content-type", ctype)],
            }
        )
        await send({"type": "http.response.body", "body": payload})


def wire(server: FakeServer, **kw: Any) -> OpenAIWireClient:
    params: dict[str, Any] = {
        "allow_internal": True,
        "timeout_seconds": 5.0,
        "max_request_bytes": 1024,
        "max_response_bytes": 1024,
        "transport": httpx.ASGITransport(app=server),  # type: ignore[arg-type]
    }
    params.update(kw)
    return OpenAIWireClient(**params)


@pytest.fixture
def server() -> FakeServer:
    return FakeServer()


@pytest.fixture
def cfg(monkeypatch: pytest.MonkeyPatch) -> VoiceConfig:
    for var in ("OPENAI_API_KEY", "VOICE_OPENAI_API_KEY", "VOICE_PROVIDER"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("VOICE_PROVIDER", "openai_compatible")
    monkeypatch.setenv("VOICE_STT_BASE_URL", BASE)
    monkeypatch.setenv("VOICE_TTS_BASE_URL", BASE)
    monkeypatch.setenv("VOICE_ALLOW_INTERNAL_ENDPOINTS", "true")
    config = VoiceConfig()
    monkeypatch.setattr(
        "baselith.core.services.voice.service.get_voice_config", lambda: config
    )
    return config


def service(server: FakeServer, **kw: Any) -> VoiceService:
    svc = VoiceService(**kw)
    svc._wire = wire(server, max_request_bytes=10_000, max_response_bytes=10_000)
    return svc


class TestClient:
    async def test_stt_happy_path(self, server: FakeServer) -> None:
        text = await wire(server).transcribe(
            WireTarget(BASE, "k1234567890"),
            b"RIFFxxxx",
            model="m",
            language="it",
            filename="a.webm",
            content_type="audio/webm",
        )
        assert text == "ciao mondo"
        call = server.calls[0]
        assert call["path"] == "/v1/audio/transcriptions"
        assert call["headers"]["authorization"] == "Bearer k1234567890"
        assert (
            b'name="language"' in call["body"] and b'filename="a.webm"' in call["body"]
        )

    async def test_tts_happy_path(self, server: FakeServer) -> None:
        audio = await wire(server).speech(
            WireTarget(BASE), "hi", model="tts-1", voice="v", fmt="wav"
        )
        assert audio == b"ID3-fake-mp3"
        sent = json.loads(server.calls[0]["body"])
        assert (
            sent["input"] == "hi"
            and sent["response_format"] == "wav"
            and sent["voice"] == "v"
        )
        assert "authorization" not in server.calls[0]["headers"]

    async def test_empty_inputs(self, server: FakeServer) -> None:
        client = wire(server)
        with pytest.raises(VoiceInputError):
            await client.transcribe(WireTarget(BASE), b"", model="m")
        with pytest.raises(VoiceInputError):
            await client.speech(WireTarget(BASE), "  ", model="m", voice="v")
        assert not server.calls

    @pytest.mark.parametrize(
        ("status", "error"),
        [
            (401, VoiceUpstreamError),
            (429, VoiceUpstreamError),
            (500, VoiceUpstreamError),
            (413, VoiceLimitError),
        ],
    )
    async def test_status_errors_never_carry_the_body(
        self,
        server: FakeServer,
        status: int,
        error: type[VoiceError],
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        server.status = status
        caplog.set_level(logging.DEBUG)
        with pytest.raises(error) as info:
            await wire(server).speech(
                WireTarget(BASE, LEAK), "hi", model="m", voice="v"
            )
        assert LEAK not in str(info.value) and "boom" not in str(info.value)
        assert LEAK not in caplog.text
        if error is VoiceUpstreamError:
            assert info.value.status == status  # type: ignore[attr-defined]

    async def test_request_cap(self, server: FakeServer) -> None:
        with pytest.raises(VoiceLimitError):
            await wire(server).transcribe(WireTarget(BASE), b"x" * 2000, model="m")
        assert not server.calls

    async def test_response_cap(self, server: FakeServer) -> None:
        server.audio = b"a" * 5000
        with pytest.raises(VoiceLimitError):
            await wire(server).speech(WireTarget(BASE), "hi", model="m", voice="v")

    async def test_timeout(self, server: FakeServer) -> None:
        server.delay = 1.0
        with pytest.raises(VoiceTimeoutError):
            await wire(server, timeout_seconds=0.05).speech(
                WireTarget(BASE), "hi", model="m", voice="v"
            )

    async def test_unusable_transcript_body(self, server: FakeServer) -> None:
        server.body_override = b"not json"
        with pytest.raises(VoiceUpstreamError):
            await wire(server).transcribe(WireTarget(BASE), b"abc", model="m")

    @pytest.mark.parametrize(
        "url",
        ["http://127.0.0.1:8000/v1", "http://10.1.2.3/v1", "http://169.254.169.254/v1"],
    )
    async def test_internal_endpoints_are_refused_by_default(
        self, server: FakeServer, url: str
    ) -> None:
        with pytest.raises(VoiceEgressError):
            await wire(server, allow_internal=False).speech(
                WireTarget(url), "hi", model="m", voice="v"
            )
        assert not server.calls

    async def test_internal_endpoint_allowed_when_opted_in(
        self, server: FakeServer
    ) -> None:
        out = await wire(server, allow_internal=True).speech(
            WireTarget("http://127.0.0.1:8000/v1"), "hi", model="m", voice="v"
        )
        assert out


class TestService:
    async def test_sidecar_mode_needs_no_provider_key(
        self, server: FakeServer, cfg: VoiceConfig
    ) -> None:
        svc = service(server)
        stt = await svc.speech_to_text(
            audio_data=b"abc", language="it", content_type="audio/webm;codecs=opus"
        )
        assert stt.text == "ciao mondo" and stt.provider == "openai_compatible"
        assert b'filename="audio.webm"' in server.calls[0]["body"]
        tts = await svc.text_to_speech("ciao")
        assert tts.audio_bytes == b"ID3-fake-mp3"
        assert json.loads(server.calls[1]["body"])["model"] == "tts-1"

    async def test_tts_text_cap_and_empty(
        self, server: FakeServer, cfg: VoiceConfig
    ) -> None:
        svc = service(server)
        with pytest.raises(VoiceLimitError):
            await svc.text_to_speech("x" * (cfg.max_tts_chars + 1))
        with pytest.raises(VoiceInputError):
            await svc.text_to_speech("   ")

    async def test_missing_url_is_not_configured(
        self, server: FakeServer, cfg: VoiceConfig
    ) -> None:
        cfg.stt_base_url = None
        with pytest.raises(VoiceNotConfiguredError):
            await service(server).speech_to_text(audio_data=b"abc")

    def test_capabilities(self, server: FakeServer, cfg: VoiceConfig) -> None:
        assert service(server).capabilities() == VoiceCapabilities(
            True, True, "openai_compatible", "openai_compatible"
        )
        cfg.tts_base_url = None
        assert service(server).capabilities().tts is False

    def test_capabilities_unconfigured(self, monkeypatch: pytest.MonkeyPatch) -> None:
        for var in (
            "OPENAI_API_KEY",
            "VOICE_OPENAI_API_KEY",
            "VOICE_STT_BASE_URL",
            "VOICE_TTS_BASE_URL",
        ):
            monkeypatch.delenv(var, raising=False)
        config = VoiceConfig(_env_file=None)
        monkeypatch.setattr(
            "baselith.core.services.voice.service.get_voice_config", lambda: config
        )
        assert VoiceService().capabilities() == VoiceCapabilities(
            False, False, None, None
        )

    def test_capabilities_with_openai_key(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        config = VoiceConfig(
            _env_file=None, openai_api_key=SecretStr("sk-test-key-0000")
        )
        # Patch the namespace VoiceService actually reads, not a dotted path
        # re-resolved through sys.modules: in the full CI run that patch did not
        # reach the service, which saw the real, keyless config.
        monkeypatch.setitem(
            VoiceService.__init__.__globals__, "get_voice_config", lambda: config
        )
        caps = VoiceService().capabilities()
        assert (caps.stt_provider, caps.tts_provider) == ("openai", "openai")

    def test_base_url_validation(self) -> None:
        with pytest.raises(ValueError):
            VoiceConfig(_env_file=None, stt_base_url="ftp://x/v1")
        with pytest.raises(ValueError):
            VoiceConfig(_env_file=None, tts_base_url="http://u:p@x/v1")
        assert (
            VoiceConfig(_env_file=None, tts_base_url="http://x/v1/").tts_base_url
            == "http://x/v1"
        )


class TestGovernance:
    @pytest.fixture(autouse=True)
    def _cfg(self, cfg: VoiceConfig) -> None:
        cfg.provider = "openai"  # type: ignore[assignment]

    def pin(
        self, monkeypatch: pytest.MonkeyPatch, config: GovernedClientConfig | None
    ) -> list[tuple]:
        seen: list[tuple] = []

        def fake(plugin: str, scope: str | None = None) -> GovernedClientConfig | None:
            seen.append((plugin, scope))
            return config

        monkeypatch.setattr(
            "baselith.core.services.llm.governed.resolve_governed_client_config", fake
        )
        return seen

    async def test_pin_credential_and_endpoint_win(
        self, server: FakeServer, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        seen = self.pin(
            monkeypatch,
            GovernedClientConfig(
                "openai",
                "gpt-5",
                SecretStr("sk-central-key-999"),
                "http://central.test/v1",
            ),
        )
        svc = service(server, plugin="myplugin")
        await svc.speech_to_text(audio_data=b"abc")
        assert seen == [("myplugin", "voice")]
        assert (
            server.calls[0]["headers"]["authorization"] == "Bearer sk-central-key-999"
        )
        assert server.calls[0]["headers"]["host"] == "central.test"
        assert (
            b"gpt-5" not in server.calls[0]["body"]
        )  # the chat model is not an audio model

    async def test_vllm_pin_uses_keyless_placeholder(
        self, server: FakeServer, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        self.pin(
            monkeypatch,
            GovernedClientConfig("vllm", "m", None, "http://gpu.test:8002/v1"),
        )
        await service(server, plugin="myplugin").text_to_speech("hi")
        assert server.calls[0]["headers"]["host"] == "gpu.test:8002"

    async def test_non_openai_pin_is_ignored(
        self, server: FakeServer, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        self.pin(
            monkeypatch,
            GovernedClientConfig(
                "anthropic", "claude", SecretStr("sk-ant-0000000"), None
            ),
        )
        svc = service(server, plugin="myplugin")
        assert (
            svc.capabilities().stt_provider == "openai_compatible"
        )  # falls back to VOICE_*
        await svc.speech_to_text(audio_data=b"abc")
        assert server.calls[0]["headers"]["host"] == "voice.test"

    async def test_no_pin_falls_back(
        self, server: FakeServer, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        self.pin(monkeypatch, None)
        await service(server, plugin="myplugin").speech_to_text(audio_data=b"abc")
        assert server.calls[0]["headers"]["host"] == "voice.test"

    async def test_resolved_per_call(
        self, server: FakeServer, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        seen = self.pin(monkeypatch, None)
        svc = service(server, plugin="myplugin")
        await svc.speech_to_text(audio_data=b"abc")
        await svc.speech_to_text(audio_data=b"abc")
        assert len(seen) == 2

    async def test_without_a_plugin_nothing_is_resolved(
        self, server: FakeServer, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        seen = self.pin(monkeypatch, None)
        await service(server).speech_to_text(audio_data=b"abc")
        assert seen == []


class TestUsage:
    @pytest.fixture
    def reports(self, monkeypatch: pytest.MonkeyPatch) -> list[tuple[str, int, int]]:
        from baselith.core.services.llm.usage_sinks import (
            register_usage_sink,
            unregister_usage_sink,
        )

        out: list[tuple[str, int, int]] = []

        def sink(report: Any) -> None:
            out.append(
                (report.model, report.usage.input_tokens, report.usage.output_tokens)
            )

        register_usage_sink(sink)
        yield out
        unregister_usage_sink(sink)

    async def test_stt_and_tts_are_reported_as_estimates(
        self, server: FakeServer, cfg: VoiceConfig, reports: list
    ) -> None:
        svc = service(server)
        await svc.speech_to_text(audio_data=b"abc", duration_seconds=2.0)
        await svc.text_to_speech("x" * 40)
        assert ("stt:whisper-1", 100, 3) in reports  # 2 s * 50; ceil(10 chars / 4)
        assert ("tts:tts-1", 10, 0) in reports

    async def test_a_failing_sink_never_breaks_the_call(
        self, server: FakeServer, cfg: VoiceConfig, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        def boom(*a: Any, **k: Any) -> None:
            raise RuntimeError("accounting down")

        monkeypatch.setattr(
            "baselith.core.services.llm._telemetry.report_external_usage", boom
        )
        assert (await service(server).text_to_speech("hi")).audio_bytes
