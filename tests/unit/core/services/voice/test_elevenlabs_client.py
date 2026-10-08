"""The native ElevenLabs client: wire shape, error mapping, retry and egress rules.

The "server" is an ``httpx.MockTransport`` injected as the inner transport of the hardened
client, so the host allowlist and the redirect rule are exercised as in production.
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

import httpx
import pytest
from elevenlabs_rig import KEY, LEAK, TARGET, VOICE, client, error, speak

from core.security.ssrf import SsrfError
from core.services.voice import (
    VoiceInputError,
    VoiceKeyInvalidError,
    VoiceLimitError,
    VoiceNotFoundError,
    VoicePermissionMissingError,
    VoiceQuotaError,
    VoiceRateLimitedError,
    VoiceTimeoutError,
    VoiceUnavailableError,
)
from core.services.voice._elevenlabs import parse_voices


@pytest.fixture(autouse=True)
def no_sleep(monkeypatch: pytest.MonkeyPatch) -> list[float]:
    pauses: list[float] = []

    async def fake(seconds: float) -> None:
        pauses.append(seconds)

    monkeypatch.setattr("core.services.voice._elevenlabs.asyncio.sleep", fake)
    return pauses


class TestSpeech:
    async def test_wire_shape(self) -> None:
        seen: list[httpx.Request] = []

        def handle(request: httpx.Request) -> httpx.Response:
            seen.append(request)
            return httpx.Response(
                200, content=b"ID3-mp3", headers={"content-type": "audio/mpeg"}
            )

        audio = await speak(client(handle), language="it", speed=1.1)
        assert audio == b"ID3-mp3"
        (req,) = seen
        assert req.url.path == f"/v1/text-to-speech/{VOICE}"
        assert req.url.params["output_format"] == "mp3_44100_128"
        assert req.headers["xi-api-key"] == KEY
        body = json.loads(req.content)
        assert body["text"] == "ciao"
        assert body["model_id"] == "eleven_flash_v2_5"
        assert body["language_code"] == "it"
        assert body["voice_settings"] == {
            "stability": 0.5,
            "similarity_boost": 0.75,
            "style": 0.0,
            "speed": 1.1,
        }

    async def test_language_code_only_for_models_that_take_it(self) -> None:
        seen: list[httpx.Request] = []

        def handle(request: httpx.Request) -> httpx.Response:
            seen.append(request)
            return httpx.Response(200, content=b"a")

        await client(handle).speech(
            TARGET,
            "ciao",
            voice_id=VOICE,
            model="eleven_multilingual_v2",
            output_format="mp3_44100_128",
            stability=0.5,
            similarity_boost=0.75,
            style=0.0,
            language="it",
        )
        assert "language_code" not in json.loads(seen[0].content)
        assert "speed" not in json.loads(seen[0].content)["voice_settings"]

    @pytest.mark.parametrize("voice", ["../v1/user", "a/b", "x?y=1", "", "a" * 65, "é"])
    async def test_voice_id_cannot_steer_the_path(self, voice: str) -> None:
        calls: list[httpx.Request] = []

        def handle(request: httpx.Request) -> httpx.Response:
            calls.append(request)
            return httpx.Response(200, content=b"a")

        with pytest.raises(VoiceNotFoundError):
            await speak(client(handle), voice_id=voice)
        assert calls == []

    async def test_empty_text_is_refused_before_the_wire(self) -> None:
        with pytest.raises(VoiceInputError):
            await speak(client(error(500)), text="   ")

    async def test_empty_audio_is_an_upstream_failure(self) -> None:
        def handle(_: httpx.Request) -> httpx.Response:
            return httpx.Response(200, content=b"")

        with pytest.raises(Exception, match="empty"):
            await speak(client(handle))

    async def test_oversize_response_is_refused(self) -> None:
        def handle(_: httpx.Request) -> httpx.Response:
            return httpx.Response(200, content=b"x" * 2000)

        with pytest.raises(VoiceLimitError):
            await speak(client(handle))

    async def test_oversize_response_without_content_length_is_cut(self) -> None:
        def handle(_: httpx.Request) -> httpx.Response:
            async def body() -> Any:
                for _ in range(10):
                    yield b"x" * 400

            return httpx.Response(200, content=body())

        with pytest.raises(VoiceLimitError):
            await speak(client(handle))


class TestTranscribe:
    async def test_wire_shape_and_text(self) -> None:
        seen: list[httpx.Request] = []

        def handle(request: httpx.Request) -> httpx.Response:
            seen.append(request)
            return httpx.Response(
                200,
                json={"text": "  ciao mondo ", "audio_duration_secs": 2.4, "words": []},
            )

        text, seconds = await client(handle).transcribe(
            TARGET,
            b"RIFFxxxx",
            model="scribe_v1",
            language="it",
            filename="a.webm",
            content_type="audio/webm",
        )
        assert (text, seconds) == ("ciao mondo", 2.4)
        (req,) = seen
        assert req.url.path == "/v1/speech-to-text"
        assert req.headers["xi-api-key"] == KEY
        body = req.content
        assert b'name="model_id"' in body and b"scribe_v1" in body
        assert b'name="language_code"' in body and b"\r\n\r\nit\r\n" in body
        assert b'name="tag_audio_events"' in body and b"\r\n\r\nfalse\r\n" in body
        assert b'filename="a.webm"' in body

    async def test_language_is_omitted_when_unknown(self) -> None:
        seen: list[httpx.Request] = []

        def handle(request: httpx.Request) -> httpx.Response:
            seen.append(request)
            return httpx.Response(200, json={"text": "hi"})

        await client(handle).transcribe(TARGET, b"abc", model="scribe_v1")
        assert b"language_code" not in seen[0].content

    async def test_oversize_audio_is_refused_before_the_wire(self) -> None:
        calls: list[Any] = []

        def handle(request: httpx.Request) -> httpx.Response:
            calls.append(request)
            return httpx.Response(200, json={"text": "x"})

        with pytest.raises(VoiceLimitError):
            await client(handle).transcribe(TARGET, b"x" * 2000, model="scribe_v1")
        assert calls == []

    async def test_empty_audio_is_refused(self) -> None:
        with pytest.raises(VoiceInputError):
            await client(error(500)).transcribe(TARGET, b"", model="scribe_v1")

    async def test_unparseable_answer(self) -> None:
        def handle(_: httpx.Request) -> httpx.Response:
            return httpx.Response(200, content=b"<html>")

        with pytest.raises(Exception, match="unusable"):
            await client(handle).transcribe(TARGET, b"abc", model="scribe_v1")

    async def test_422_is_an_input_problem_not_a_voice_one(self) -> None:
        with pytest.raises(VoiceInputError):
            await client(error(422)).transcribe(TARGET, b"abc", model="scribe_v1")


class TestErrorMapping:
    @pytest.mark.parametrize(
        ("status", "detail", "expected"),
        [
            (401, "invalid_api_key", VoiceKeyInvalidError),
            (401, None, VoiceKeyInvalidError),
            (403, None, VoiceKeyInvalidError),
            (401, "missing_permissions", VoicePermissionMissingError),
            (401, "quota_exceeded", VoiceQuotaError),
            (402, None, VoiceQuotaError),
            (422, None, VoiceNotFoundError),
            (404, "voice_not_found", VoiceNotFoundError),
            (400, "voice_does_not_exist", VoiceNotFoundError),
            (500, None, VoiceUnavailableError),
            (503, None, VoiceUnavailableError),
        ],
    )
    async def test_tts(
        self, status: int, detail: str | None, expected: type[Exception]
    ) -> None:
        with pytest.raises(expected) as caught:
            await speak(client(error(status, detail)))
        message = str(caught.value)
        assert LEAK not in message and KEY not in message
        assert "nope" not in message  # the provider's own wording never travels

    async def test_codes_are_the_stable_ones(self) -> None:
        pairs = [
            (VoiceKeyInvalidError, "key_invalid"),
            (VoiceQuotaError, "quota_exceeded"),
            (VoiceNotFoundError, "voice_not_found"),
            (VoiceRateLimitedError, "rate_limited"),
            (VoiceUnavailableError, "provider_unavailable"),
            (VoicePermissionMissingError, "permission_missing"),
        ]
        for cls, code in pairs:
            assert cls.code == code

    async def test_unknown_detail_status_is_ignored(self) -> None:
        with pytest.raises(VoiceKeyInvalidError):
            await speak(client(error(401, "totally_new_status")))

    async def test_a_huge_or_non_json_error_body_is_survivable(self) -> None:
        def huge(_: httpx.Request) -> httpx.Response:
            return httpx.Response(401, content=b"{" + b"x" * 50_000)

        with pytest.raises(VoiceKeyInvalidError):
            await speak(client(huge))

        def html(_: httpx.Request) -> httpx.Response:
            return httpx.Response(502, content=b"<html>bad gateway</html>")

        with pytest.raises(VoiceUnavailableError):
            await speak(client(html))

    async def test_key_never_reaches_the_logs(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        caplog.set_level(logging.DEBUG)
        for status in (401, 429, 500):
            with pytest.raises(Exception):
                await speak(client(error(status), retries=0))
        assert KEY not in caplog.text and LEAK not in caplog.text


class TestRetry:
    async def test_429_is_retried_then_succeeds(self, no_sleep: list[float]) -> None:
        answers = iter([429, 429, 200])
        calls = 0

        def handle(_: httpx.Request) -> httpx.Response:
            nonlocal calls
            calls += 1
            status = next(answers)
            if status == 200:
                return httpx.Response(200, content=b"mp3")
            return httpx.Response(
                429, json={"detail": {"status": "too_many_concurrent_requests"}}
            )

        assert await speak(client(handle)) == b"mp3"
        assert calls == 3
        assert len(no_sleep) == 2

    async def test_429_exhausted_is_rate_limited(self) -> None:
        calls = 0

        def handle(_: httpx.Request) -> httpx.Response:
            nonlocal calls
            calls += 1
            return httpx.Response(429, headers={"retry-after": "30"})

        with pytest.raises(VoiceRateLimitedError):
            await speak(client(handle, retries=2))
        assert calls == 3  # the first try and two retries, no more

    async def test_retry_after_is_capped(self, no_sleep: list[float]) -> None:
        with pytest.raises(VoiceRateLimitedError):
            await speak(client(error(429, None, **{"retry-after": "3600"}), retries=1))
        assert no_sleep == [2.0]

    async def test_quota_429_is_not_retried(self) -> None:
        calls = 0

        def handle(_: httpx.Request) -> httpx.Response:
            nonlocal calls
            calls += 1
            return httpx.Response(429, json={"detail": {"status": "quota_exceeded"}})

        with pytest.raises(VoiceQuotaError):
            await speak(client(handle))
        assert calls == 1

    async def test_deadline_covers_the_retries(self) -> None:
        async def slow(_: httpx.Request) -> httpx.Response:
            await asyncio.Event().wait()
            return httpx.Response(200, content=b"a")

        with pytest.raises(VoiceTimeoutError):
            await speak(client(slow, timeout_seconds=0.05))


class TestEgress:
    async def test_redirect_is_refused_and_not_followed(self) -> None:
        hosts: list[str] = []

        def handle(request: httpx.Request) -> httpx.Response:
            hosts.append(request.url.host)
            return httpx.Response(
                302, headers={"location": "https://evil.example/steal"}
            )

        with pytest.raises(VoiceUnavailableError):
            await speak(client(handle))
        assert hosts == ["api.elevenlabs.io"]  # the key never went to the other host

    async def test_only_the_configured_host_is_reachable(self) -> None:
        c = client(lambda _: httpx.Response(200, content=b"a"))
        with pytest.raises(SsrfError):
            await c._client.get("https://evil.example/", headers={"xi-api-key": KEY})
        strict = client(
            lambda _: httpx.Response(200, content=b"a"), allow_internal=False
        )
        with pytest.raises(SsrfError):
            await strict._client.get(
                "http://api.elevenlabs.io/"
            )  # https only unless local dev

    async def test_http_is_allowed_only_for_local_development(self) -> None:
        c = client(
            lambda _: httpx.Response(200, content=b"a"), base_url="http://127.0.0.1:9"
        )
        assert (await c._client.get("http://127.0.0.1:9/ok")).status_code == 200

    async def test_loopback_refused_by_default(self) -> None:
        c = client(
            lambda _: httpx.Response(200, content=b"a"),
            base_url="https://localhost",
            allow_internal=False,
        )
        with pytest.raises(Exception, match="not allowed|blocked|internal|resolve"):
            await speak(c)

    async def test_transport_failure_is_unavailable(self) -> None:
        def boom(_: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError(f"connect to {KEY} failed")

        with pytest.raises(VoiceUnavailableError) as caught:
            await speak(client(boom))
        assert KEY not in str(caught.value)


class TestVoices:
    def test_parse_voices(self) -> None:
        voices = parse_voices(
            {
                "voices": [
                    {
                        "voice_id": VOICE,
                        "name": "Rachel",
                        "category": "premade",
                        "verified_languages": [
                            {"language": "it", "model_id": "m"},
                            {"language": "en-US"},
                            {"language": "Italian"},
                        ],
                    },
                    {"voice_id": "bad id!", "name": "X"},
                    {"voice_id": "ok1", "name": 7},
                    "junk",
                ]
            }
        )
        assert [v.id for v in voices] == [VOICE]
        assert voices[0].languages == ("it", "en")
        assert voices[0].category == "premade"

    async def test_pages_are_followed_up_to_a_limit(self) -> None:
        pages = {
            None: {
                "voices": [{"voice_id": "a1", "name": "A"}],
                "has_more": True,
                "next_page_token": "t1",
            },
            "t1": {"voices": [{"voice_id": "b1", "name": "B"}], "has_more": False},
        }

        def handle(request: httpx.Request) -> httpx.Response:
            assert request.url.path == "/v2/voices"
            return httpx.Response(
                200, json=pages[request.url.params.get("next_page_token")]
            )

        voices = await client(handle, max_response_bytes=10_000).voices(TARGET)
        assert [v.id for v in voices] == ["a1", "b1"]

    async def test_endless_pagination_stops(self) -> None:
        calls = 0

        def handle(_: httpx.Request) -> httpx.Response:
            nonlocal calls
            calls += 1
            return httpx.Response(
                200,
                json={"voices": [], "has_more": True, "next_page_token": "again"},
            )

        await client(handle).voices(TARGET)
        assert calls == 3
