"""Modality router — magic-byte, MIME and extension detection.

Pins ``detect_modality``'s precedence (magic bytes → mime → extension →
``"text"``), the container disambiguation inside RIFF/ISO-BMFF framing, and
the ``annotate_context`` helper plus its context-assembly wiring.
"""

from __future__ import annotations

import pytest
from core.orchestration.modality_router import annotate_context, detect_modality

# Minimal-but-real magic-byte heads per format.
_PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 8
_JPEG = b"\xff\xd8\xff\xe0\x00\x10JFIF"
_GIF = b"GIF89a\x01\x00"
_WEBP = b"RIFF\x24\x00\x00\x00WEBPVP8 "
_PDF = b"%PDF-1.7\n%stuff"
_MP3_ID3 = b"ID3\x04\x00\x00\x00\x00\x00\x00"
_WAV = b"RIFF\x24\x00\x00\x00WAVEfmt "
_OGG = b"OggS\x00\x02\x00\x00"
_FLAC = b"fLaC\x00\x00\x00\x22"
_MP4 = b"\x00\x00\x00\x18ftypmp42\x00\x00\x00\x00"
_M4A = b"\x00\x00\x00\x20ftypM4A \x00\x00\x00\x00"
_AVI = b"RIFF\x24\x00\x00\x00AVI LIST"
_MKV = b"\x1a\x45\xdf\xa3\x93\x42\x82\x88"


class TestMagicBytes:
    @pytest.mark.parametrize("data", [_PNG, _JPEG, _GIF, _WEBP])
    def test_images(self, data):
        assert detect_modality(data) == "image"

    def test_pdf(self):
        assert detect_modality(_PDF) == "pdf"

    @pytest.mark.parametrize("data", [_MP3_ID3, _WAV, _OGG, _FLAC, _M4A])
    def test_audio(self, data):
        assert detect_modality(data) == "audio"

    @pytest.mark.parametrize("data", [_MP4, _AVI, _MKV])
    def test_video(self, data):
        assert detect_modality(data) == "video"

    def test_unknown_bytes_default_to_text(self):
        assert detect_modality(b"hello plain world") == "text"

    def test_bytes_win_over_contradicting_mime(self):
        assert detect_modality(_PNG, mime="audio/mpeg") == "image"

    def test_bytes_win_over_contradicting_extension(self):
        assert detect_modality(_PDF, filename="song.mp3") == "pdf"


class TestMimeFallback:
    @pytest.mark.parametrize(
        ("mime", "expected"),
        [
            ("image/png", "image"),
            ("application/pdf", "pdf"),
            ("audio/mpeg", "audio"),
            ("video/mp4", "video"),
            ("text/plain", "text"),
            ("text/markdown; charset=utf-8", "text"),
        ],
    )
    def test_mime_families(self, mime, expected):
        assert detect_modality(mime=mime) == expected

    def test_unmapped_mime_falls_through_to_extension(self):
        assert detect_modality(mime="application/octet-stream", filename="a.pdf") == (
            "pdf"
        )

    def test_unsniffable_bytes_fall_through_to_mime(self):
        assert detect_modality(b"\x00\x01\x02\x03", mime="audio/flac") == "audio"


class TestExtensionFallback:
    @pytest.mark.parametrize(
        ("filename", "expected"),
        [
            ("photo.JPEG", "image"),
            ("report.pdf", "pdf"),
            ("song.mp3", "audio"),
            ("voice.flac", "audio"),
            ("clip.mkv", "video"),
            ("movie.mp4", "video"),
            ("notes.txt", "text"),
            ("README", "text"),
            ("archive.tar.gz", "text"),
        ],
    )
    def test_extensions(self, filename, expected):
        assert detect_modality(filename=filename) == expected

    def test_nothing_at_all_is_text(self):
        assert detect_modality() == "text"


class TestAnnotateContext:
    def test_writes_and_returns_modality(self):
        context: dict = {}
        assert annotate_context(context, _PNG) == "image"
        assert context["modality"] == "image"

    def test_annotates_text_when_undetectable(self):
        context: dict = {}
        assert annotate_context(context) == "text"
        assert context["modality"] == "text"


class TestContextAssemblyWiring:
    """annotate_modality — the orchestrator-side seam over attachment keys."""

    def test_image_paths_annotate_image(self):
        from core.orchestration.mixins._context_assembly import annotate_modality

        context = {"image_paths": ["/tmp/diagram.png"]}
        annotate_modality(context)
        assert context["modality"] == "image"

    def test_image_data_annotates_image(self):
        from core.orchestration.mixins._context_assembly import annotate_modality

        context = {"image_data": ["aGVsbG8="]}
        annotate_modality(context)
        assert context["modality"] == "image"

    def test_plain_query_context_stays_unannotated(self):
        from core.orchestration.mixins._context_assembly import annotate_modality

        context: dict = {"intent": "qa_docs"}
        annotate_modality(context)
        assert "modality" not in context

    def test_existing_modality_not_overwritten(self):
        from core.orchestration.mixins._context_assembly import annotate_modality

        context = {"modality": "audio", "image_paths": ["/tmp/x.png"]}
        annotate_modality(context)
        assert context["modality"] == "audio"

    def test_attachment_bytes_annotate(self):
        from core.orchestration.mixins._context_assembly import annotate_modality

        context = {"attachment_data": _PDF}
        annotate_modality(context)
        assert context["modality"] == "pdf"
