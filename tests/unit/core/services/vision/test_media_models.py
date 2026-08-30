"""Native document/audio content models — validation and payload shapes.

DocumentContent and AudioContent fail closed: a payload whose magic bytes
disagree with the declared media type (or are unrecognizable) is rejected at
construction, mirroring how the browser-vision path treats media types.
"""

from __future__ import annotations

import base64

import pytest
from core.services.vision.media_models import (
    AudioContent,
    DocumentContent,
    UnsupportedContentError,
)

_PDF = b"%PDF-1.7\n%fake little pdf"
_PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 8
_WAV = b"RIFF\x24\x00\x00\x00WAVEfmt "
_MP3 = b"ID3\x04\x00\x00\x00\x00\x00\x00"
_OGG = b"OggS\x00\x02\x00\x00"
_FLAC = b"fLaC\x00\x00\x00\x22"


class TestDocumentContentValidation:
    def test_valid_pdf_bytes(self):
        doc = DocumentContent(data=_PDF)
        assert doc.media_type == "application/pdf"
        assert doc.source_type == "bytes"

    def test_valid_uri(self):
        doc = DocumentContent(uri="https://example.com/paper.pdf")
        assert doc.uri == "https://example.com/paper.pdf"
        assert doc.source_type == "uri"

    def test_both_data_and_uri_rejected(self):
        with pytest.raises(ValueError, match="[Ee]xactly one"):
            DocumentContent(data=_PDF, uri="https://example.com/a.pdf")

    def test_neither_data_nor_uri_rejected(self):
        with pytest.raises(ValueError, match="[Ee]xactly one"):
            DocumentContent()

    def test_mislabeled_payload_rejected(self):
        # PNG bytes declared as application/pdf: fail closed.
        with pytest.raises(ValueError, match="magic bytes"):
            DocumentContent(data=_PNG)

    def test_unknown_media_type_rejected(self):
        with pytest.raises(ValueError, match="media type"):
            DocumentContent(data=_PDF, media_type="application/msword")

    def test_from_bytes(self):
        doc = DocumentContent.from_bytes(_PDF)
        assert doc.data == _PDF

    def test_from_uri(self):
        doc = DocumentContent.from_uri("https://example.com/a.pdf")
        assert doc.uri == "https://example.com/a.pdf"

    def test_from_file(self, tmp_path):
        path = tmp_path / "doc.pdf"
        path.write_bytes(_PDF)
        doc = DocumentContent.from_file(path)
        assert doc.data == _PDF
        assert doc.source_type == "file"

    def test_from_file_missing(self, tmp_path):
        with pytest.raises(FileNotFoundError):
            DocumentContent.from_file(tmp_path / "nope.pdf")


class TestDocumentContentFormats:
    def test_anthropic_base64_block(self):
        block = DocumentContent(data=_PDF).to_anthropic_format()
        assert block == {
            "type": "document",
            "source": {
                "type": "base64",
                "media_type": "application/pdf",
                "data": base64.b64encode(_PDF).decode("ascii"),
            },
        }

    def test_anthropic_url_block(self):
        block = DocumentContent(uri="https://example.com/a.pdf").to_anthropic_format()
        assert block == {
            "type": "document",
            "source": {"type": "url", "url": "https://example.com/a.pdf"},
        }

    def test_google_inline_data_part(self):
        part = DocumentContent(data=_PDF).to_google_part()
        assert part == {
            "inline_data": {
                "mime_type": "application/pdf",
                "data": base64.b64encode(_PDF).decode("ascii"),
            }
        }

    def test_google_file_uri_part(self):
        part = DocumentContent(uri="https://example.com/a.pdf").to_google_part()
        assert part == {
            "file_data": {
                "mime_type": "application/pdf",
                "file_uri": "https://example.com/a.pdf",
            }
        }


class TestAudioContentValidation:
    @pytest.mark.parametrize(
        ("data", "media_type"),
        [
            (_WAV, "audio/wav"),
            (_MP3, "audio/mpeg"),
            (_OGG, "audio/ogg"),
            (_FLAC, "audio/flac"),
        ],
    )
    def test_valid_audio(self, data, media_type):
        audio = AudioContent(data=data, media_type=media_type)
        assert audio.media_type == media_type

    def test_mislabeled_payload_rejected(self):
        # WAV bytes declared as MP3: fail closed.
        with pytest.raises(ValueError, match="magic bytes"):
            AudioContent(data=_WAV, media_type="audio/mpeg")

    def test_unrecognized_bytes_rejected(self):
        with pytest.raises(ValueError, match="magic bytes"):
            AudioContent(data=b"not audio at all", media_type="audio/wav")

    def test_unknown_media_type_rejected(self):
        with pytest.raises(ValueError, match="media type"):
            AudioContent(data=_WAV, media_type="audio/aac")

    def test_empty_payload_rejected(self):
        with pytest.raises(ValueError, match="empty"):
            AudioContent(data=b"", media_type="audio/wav")

    def test_from_bytes_sniffs_media_type(self):
        audio = AudioContent.from_bytes(_FLAC)
        assert audio.media_type == "audio/flac"

    def test_from_file(self, tmp_path):
        path = tmp_path / "clip.wav"
        path.write_bytes(_WAV)
        audio = AudioContent.from_file(path)
        assert audio.media_type == "audio/wav"
        assert audio.source_type == "file"


class TestAudioContentFormats:
    def test_openai_input_audio_wav(self):
        part = AudioContent(data=_WAV, media_type="audio/wav").to_openai_format()
        assert part == {
            "type": "input_audio",
            "input_audio": {
                "data": base64.b64encode(_WAV).decode("ascii"),
                "format": "wav",
            },
        }

    def test_openai_input_audio_mp3(self):
        part = AudioContent(data=_MP3, media_type="audio/mpeg").to_openai_format()
        assert part["input_audio"]["format"] == "mp3"

    @pytest.mark.parametrize(
        ("data", "media_type"),
        [(_OGG, "audio/ogg"), (_FLAC, "audio/flac")],
    )
    def test_openai_rejects_formats_it_cannot_take(self, data, media_type):
        audio = AudioContent(data=data, media_type=media_type)
        with pytest.raises(UnsupportedContentError) as excinfo:
            audio.to_openai_format()
        assert excinfo.value.provider == "openai"
        assert excinfo.value.content_type == media_type

    def test_google_inline_data_part(self):
        part = AudioContent(data=_OGG, media_type="audio/ogg").to_google_part()
        assert part == {
            "inline_data": {
                "mime_type": "audio/ogg",
                "data": base64.b64encode(_OGG).decode("ascii"),
            }
        }


class TestUnsupportedContentError:
    def test_carries_provider_and_content_type(self):
        err = UnsupportedContentError("ollama", "application/pdf")
        assert err.provider == "ollama"
        assert err.content_type == "application/pdf"
        assert "ollama" in str(err)
        assert "application/pdf" in str(err)

    def test_custom_message(self):
        err = UnsupportedContentError("openai", "audio/ogg", "custom words")
        assert str(err) == "custom words"
