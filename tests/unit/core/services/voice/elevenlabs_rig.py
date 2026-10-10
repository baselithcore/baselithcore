"""Shared rig of the ElevenLabs adapter tests: a MockTransport "server" and its helpers."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import httpx
import pytest

from baselith.core.config.multimodal import VoiceConfig
from baselith.core.services.voice import VoiceOverrides, VoiceService
from baselith.core.services.voice._elevenlabs import ElevenLabsClient, ElevenLabsTarget

KEY = "xi-secret-key-0123456789"  # gitleaks:allow (fake fixture)
LEAK = "sk-body-secret-987654"
BASE = "https://api.elevenlabs.io"
VOICE = "21m00Tcm4TlvDq8ikWAM"

Handler = Callable[[httpx.Request], httpx.Response]


def client(handler: Handler, **kw: Any) -> ElevenLabsClient:
    params: dict[str, Any] = {
        "base_url": BASE,
        "allow_internal": True,
        "timeout_seconds": 5.0,
        "max_request_bytes": 1024,
        "max_response_bytes": 1024,
        "retries": 2,
        "transport": httpx.MockTransport(handler),
    }
    params.update(kw)
    return ElevenLabsClient(**params)


def error(status: int, detail: str | None = None, **headers: str) -> Handler:
    def handle(_: httpx.Request) -> httpx.Response:
        body = {"detail": {"status": detail, "message": f"nope {LEAK}"}}
        return httpx.Response(status, json=body, headers=headers)

    return handle


TARGET = ElevenLabsTarget(KEY)


async def speak(c: ElevenLabsClient, **kw: Any) -> bytes:
    return await c.speech(
        TARGET,
        kw.pop("text", "ciao"),
        voice_id=kw.pop("voice_id", VOICE),
        model="eleven_flash_v2_5",
        output_format="mp3_44100_128",
        stability=0.5,
        similarity_boost=0.75,
        style=0.0,
        **kw,
    )


def make_service(
    handler: Handler,
    monkeypatch: pytest.MonkeyPatch,
    *,
    key: str | None = KEY,
    overrides: Callable[[], VoiceOverrides | None] | None = None,
    **env: str,
) -> VoiceService:
    for var in ("VOICE_PROVIDER", "VOICE_ELEVENLABS_API_KEY", "ELEVENLABS_API_KEY"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("VOICE_PROVIDER", "elevenlabs")
    monkeypatch.setenv("VOICE_ALLOW_INTERNAL_ENDPOINTS", "true")
    for name, value in env.items():
        monkeypatch.setenv(name, value)
    config = VoiceConfig()
    monkeypatch.setattr(
        "baselith.core.services.voice.service.get_voice_config", lambda: config
    )
    service = VoiceService(elevenlabs_api_key=key, overrides=overrides)
    service._eleven = client(
        handler, max_request_bytes=10_000, max_response_bytes=10_000
    )
    return service
