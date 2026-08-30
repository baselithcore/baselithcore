"""Native document/audio analysis through VisionService.

Provider SDK calls are mocked exactly as in ``test_vision_service.py``:
``httpx.AsyncClient`` for the HTTP-based providers (Anthropic, Google,
Ollama) and ``openai.AsyncOpenAI`` for OpenAI. No network, no real SDKs.
"""

from __future__ import annotations

import base64
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from pydantic import SecretStr

from core.services.vision.media_models import (
    AudioContent,
    DocumentContent,
    UnsupportedContentError,
)
from core.services.vision.models import VisionProvider
from core.services.vision.service import VisionService

_PDF = b"%PDF-1.7\n%fake little pdf"
_WAV = b"RIFF\x24\x00\x00\x00WAVEfmt "
_MP3 = b"ID3\x04\x00\x00\x00\x00\x00\x00"
_OGG = b"OggS\x00\x02\x00\x00"

_B64_PDF = base64.b64encode(_PDF).decode("ascii")
_B64_WAV = base64.b64encode(_WAV).decode("ascii")


@pytest.fixture
def vision_service():
    with patch("core.services.vision.service.get_vision_config") as mock_config:
        mock_config.return_value.provider = "openai"
        mock_config.return_value.openai_api_key = SecretStr("fake-key")
        mock_config.return_value.anthropic_api_key = SecretStr("fake-key")
        mock_config.return_value.google_api_key = SecretStr("fake-google-key")
        mock_config.return_value.ollama_url = "http://localhost:11434"
        mock_config.return_value.openai_model = None
        mock_config.return_value.anthropic_model = None
        mock_config.return_value.google_model = None
        mock_config.return_value.ollama_model = None
        mock_config.return_value.openai_audio_model = None

        service = VisionService(openai_api_key="fake-key", anthropic_api_key="fake-key")
        yield service


def _mock_httpx_response(payload: dict) -> AsyncMock:
    client = AsyncMock()
    response = MagicMock()
    response.json.return_value = payload
    response.raise_for_status = MagicMock()
    client.post.return_value = response
    return client


class TestAnthropicDocument:
    @pytest.mark.asyncio
    async def test_base64_document_block(self, vision_service):
        with patch("httpx.AsyncClient") as mock_client_cls:
            mock_client = _mock_httpx_response(
                {
                    "content": [{"text": "A summary"}],
                    "usage": {"input_tokens": 10, "output_tokens": 5},
                }
            )
            mock_client_cls.return_value = mock_client

            result = await vision_service.analyze_document(
                DocumentContent(data=_PDF),
                "Summarize this.",
                provider=VisionProvider.ANTHROPIC,
            )

            assert result == "A summary"
            mock_client.post.assert_awaited_once()
            call = mock_client.post.call_args
            assert call[0][0] == "https://api.anthropic.com/v1/messages"
            content = call.kwargs["json"]["messages"][0]["content"]
            assert content[0] == {
                "type": "document",
                "source": {
                    "type": "base64",
                    "media_type": "application/pdf",
                    "data": _B64_PDF,
                },
            }
            assert content[1] == {"type": "text", "text": "Summarize this."}

    @pytest.mark.asyncio
    async def test_url_document_block(self, vision_service):
        with patch("httpx.AsyncClient") as mock_client_cls:
            mock_client = _mock_httpx_response({"content": [{"text": "ok"}]})
            mock_client_cls.return_value = mock_client

            await vision_service.analyze_document(
                DocumentContent(uri="https://example.com/a.pdf"),
                "Summarize",
                provider=VisionProvider.ANTHROPIC,
            )

            content = mock_client.post.call_args.kwargs["json"]["messages"][0][
                "content"
            ]
            assert content[0] == {
                "type": "document",
                "source": {"type": "url", "url": "https://example.com/a.pdf"},
            }

    @pytest.mark.asyncio
    async def test_missing_key(self):
        with patch("core.services.vision.service.get_vision_config") as mock_config:
            mock_config.return_value.provider = "anthropic"
            mock_config.return_value.openai_api_key = None
            mock_config.return_value.anthropic_api_key = None
            mock_config.return_value.google_api_key = None
            mock_config.return_value.ollama_url = "http://localhost:11434"
            service = VisionService(
                openai_api_key=None, anthropic_api_key=None, google_api_key=None
            )
        with pytest.raises(ValueError, match="Anthropic API key"):
            await service.analyze_document(
                DocumentContent(data=_PDF), "x", provider=VisionProvider.ANTHROPIC
            )


class TestGoogleDocumentAndAudio:
    @pytest.mark.asyncio
    async def test_inline_data_document_part(self, vision_service):
        with patch("httpx.AsyncClient") as mock_client_cls:
            mock_client = _mock_httpx_response(
                {
                    "candidates": [{"content": {"parts": [{"text": "Gemini says"}]}}],
                    "usageMetadata": {"totalTokenCount": 20},
                }
            )
            mock_client_cls.return_value = mock_client

            result = await vision_service.analyze_document(
                DocumentContent(data=_PDF),
                "Summarize",
                provider=VisionProvider.GOOGLE,
            )

            assert result == "Gemini says"
            url = mock_client.post.call_args[0][0]
            assert url.startswith(
                "https://generativelanguage.googleapis.com/v1/models/"
            )
            parts = mock_client.post.call_args.kwargs["json"]["contents"][0]["parts"]
            assert parts[0] == {
                "inline_data": {"mime_type": "application/pdf", "data": _B64_PDF}
            }
            assert parts[1] == {"text": "Summarize"}

    @pytest.mark.asyncio
    async def test_file_uri_document_part(self, vision_service):
        with patch("httpx.AsyncClient") as mock_client_cls:
            mock_client = _mock_httpx_response(
                {"candidates": [{"content": {"parts": [{"text": "ok"}]}}]}
            )
            mock_client_cls.return_value = mock_client

            await vision_service.analyze_document(
                DocumentContent(uri="https://example.com/a.pdf"),
                "Summarize",
                provider=VisionProvider.GOOGLE,
            )

            parts = mock_client.post.call_args.kwargs["json"]["contents"][0]["parts"]
            assert parts[0] == {
                "file_data": {
                    "mime_type": "application/pdf",
                    "file_uri": "https://example.com/a.pdf",
                }
            }

    @pytest.mark.asyncio
    async def test_inline_data_audio_part(self, vision_service):
        with patch("httpx.AsyncClient") as mock_client_cls:
            mock_client = _mock_httpx_response(
                {"candidates": [{"content": {"parts": [{"text": "Transcript"}]}}]}
            )
            mock_client_cls.return_value = mock_client

            result = await vision_service.analyze_audio(
                AudioContent(data=_WAV, media_type="audio/wav"),
                "Transcribe",
                provider=VisionProvider.GOOGLE,
            )

            assert result == "Transcript"
            parts = mock_client.post.call_args.kwargs["json"]["contents"][0]["parts"]
            assert parts[0] == {
                "inline_data": {"mime_type": "audio/wav", "data": _B64_WAV}
            }


class TestOpenAI:
    @pytest.mark.asyncio
    async def test_input_audio_part(self, vision_service):
        with patch("openai.AsyncOpenAI") as mock_cls:
            mock_client = AsyncMock()
            mock_cls.return_value = mock_client
            mock_response = MagicMock()
            mock_response.choices = [MagicMock(message=MagicMock(content="Heard it"))]
            mock_response.usage.total_tokens = 42
            mock_client.chat.completions.create.return_value = mock_response

            result = await vision_service.analyze_audio(
                AudioContent(data=_WAV, media_type="audio/wav"),
                "What is said?",
                provider=VisionProvider.OPENAI,
            )

            assert result == "Heard it"
            mock_client.chat.completions.create.assert_awaited_once()
            kwargs = mock_client.chat.completions.create.call_args.kwargs
            content = kwargs["messages"][0]["content"]
            assert {"type": "text", "text": "What is said?"} in content
            audio_parts = [p for p in content if p.get("type") == "input_audio"]
            assert audio_parts == [
                {
                    "type": "input_audio",
                    "input_audio": {"data": _B64_WAV, "format": "wav"},
                }
            ]

    @pytest.mark.asyncio
    async def test_audio_uses_audio_capable_model(self, vision_service):
        # gpt-4o (the vision model) cannot take input_audio; the audio path
        # must use the configured audio-capable chat model instead.
        with patch("openai.AsyncOpenAI") as mock_cls:
            mock_client = AsyncMock()
            mock_cls.return_value = mock_client
            mock_response = MagicMock()
            mock_response.choices = [MagicMock(message=MagicMock(content="ok"))]
            mock_response.usage.total_tokens = 1
            mock_client.chat.completions.create.return_value = mock_response

            await vision_service.analyze_audio(
                AudioContent(data=_WAV, media_type="audio/wav"),
                "x",
                provider=VisionProvider.OPENAI,
            )
            kwargs = mock_client.chat.completions.create.call_args.kwargs
            assert kwargs["model"] == vision_service.openai_audio_model
            assert kwargs["model"] != vision_service.models[VisionProvider.OPENAI]

    @pytest.mark.asyncio
    async def test_pdf_unsupported(self, vision_service):
        with pytest.raises(UnsupportedContentError) as excinfo:
            await vision_service.analyze_document(
                DocumentContent(data=_PDF), "x", provider=VisionProvider.OPENAI
            )
        assert excinfo.value.provider == "openai"
        assert excinfo.value.content_type == "application/pdf"

    @pytest.mark.asyncio
    async def test_ogg_audio_unsupported_propagates(self, vision_service):
        # No fallback for audio: the error propagates untouched.
        with pytest.raises(UnsupportedContentError) as excinfo:
            await vision_service.analyze_audio(
                AudioContent(data=_OGG, media_type="audio/ogg"),
                "x",
                provider=VisionProvider.OPENAI,
            )
        assert excinfo.value.provider == "openai"
        assert excinfo.value.content_type == "audio/ogg"


class TestOllama:
    @pytest.mark.asyncio
    async def test_document_unsupported(self, vision_service):
        with pytest.raises(UnsupportedContentError) as excinfo:
            await vision_service.analyze_document(
                DocumentContent(data=_PDF), "x", provider=VisionProvider.OLLAMA
            )
        assert excinfo.value.provider == "ollama"

    @pytest.mark.asyncio
    async def test_audio_unsupported(self, vision_service):
        with pytest.raises(UnsupportedContentError) as excinfo:
            await vision_service.analyze_audio(
                AudioContent(data=_MP3, media_type="audio/mpeg"),
                "x",
                provider=VisionProvider.OLLAMA,
            )
        assert excinfo.value.provider == "ollama"


class TestServiceDispatchAndBoundary:
    @pytest.mark.asyncio
    async def test_default_provider_used_when_none_given(self, vision_service):
        # Fixture default provider is openai → document path is unsupported,
        # proving provider selection mirrors the image path (request → default).
        with pytest.raises(UnsupportedContentError) as excinfo:
            await vision_service.analyze_document(DocumentContent(data=_PDF), "x")
        assert excinfo.value.provider == "openai"

    @pytest.mark.asyncio
    async def test_document_boundary_message_points_at_extraction(self, vision_service):
        # Text extraction lives only in plugins/document_sources; core must not
        # import it (Sacred Core). The re-raise names the extraction pipeline.
        with pytest.raises(UnsupportedContentError) as excinfo:
            await vision_service.analyze_document(
                DocumentContent(data=_PDF), "x", provider=VisionProvider.OLLAMA
            )
        assert "document_sources" in str(excinfo.value)
        assert isinstance(excinfo.value.__cause__, UnsupportedContentError)

    @pytest.mark.asyncio
    async def test_audio_error_not_rewritten(self, vision_service):
        # Audio has no fallback and no extraction pipeline: no rewrite.
        with pytest.raises(UnsupportedContentError) as excinfo:
            await vision_service.analyze_audio(
                AudioContent(data=_MP3, media_type="audio/mpeg"),
                "x",
                provider=VisionProvider.ANTHROPIC,
            )
        assert excinfo.value.provider == "anthropic"
        assert "document_sources" not in str(excinfo.value)
