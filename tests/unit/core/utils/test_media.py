"""Magic-byte sniffing for document and audio payloads.

Pins ``sniff_document_type`` / ``sniff_audio_type`` — the byte-level truth
the native document/audio content models fail closed on.
"""

from __future__ import annotations

import pytest

from core.utils.media import sniff_audio_type, sniff_document_type

# Minimal-but-real magic-byte heads per format.
_PDF = b"%PDF-1.7\n%stuff"
_WAV = b"RIFF\x24\x00\x00\x00WAVEfmt "
_MP3_ID3 = b"ID3\x04\x00\x00\x00\x00\x00\x00"
_MP3_FRAME = b"\xff\xfb\x90\x44\x00\x00"  # bare MPEG frame sync, no ID3 tag
_OGG = b"OggS\x00\x02\x00\x00"
_FLAC = b"fLaC\x00\x00\x00\x22"
_PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 8
_JPEG = b"\xff\xd8\xff\xe0\x00\x10JFIF"
_AVI = b"RIFF\x24\x00\x00\x00AVI LIST"


class TestSniffDocumentType:
    def test_pdf(self):
        assert sniff_document_type(_PDF) == "application/pdf"

    @pytest.mark.parametrize("data", [_PNG, _WAV, _MP3_ID3, b"", b"hello"])
    def test_non_documents_are_none(self, data):
        assert sniff_document_type(data) is None


class TestSniffAudioType:
    @pytest.mark.parametrize(
        ("data", "expected"),
        [
            (_WAV, "audio/wav"),
            (_MP3_ID3, "audio/mpeg"),
            (_MP3_FRAME, "audio/mpeg"),
            (_OGG, "audio/ogg"),
            (_FLAC, "audio/flac"),
        ],
    )
    def test_audio_formats(self, data, expected):
        assert sniff_audio_type(data) == expected

    @pytest.mark.parametrize("data", [_PNG, _JPEG, _PDF, _AVI, b"", b"hello"])
    def test_non_audio_is_none(self, data):
        # JPEG starts 0xFF 0xD8 — must not be mistaken for an MPEG frame sync.
        assert sniff_audio_type(data) is None
