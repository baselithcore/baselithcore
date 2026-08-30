"""Modality routing — what kind of payload an attachment actually is.

Labels can lie (a browser's ``Content-Type`` is whatever the client sent, a
filename is whatever the uploader chose), so detection is layered by
trustworthiness: magic bytes first (reusing
:func:`core.utils.images.sniff_image_type` for raster images), then the
declared MIME type, then the filename extension, and finally ``"text"`` as
the fallback for anything undetectable.

The router is pure and dependency-free so any surface that receives
attachments — API upload paths, MCP tool inputs, the orchestrator's context
assembly (:func:`core.orchestration.mixins._context_assembly.annotate_modality`)
— can stamp ``context["modality"]`` before intent classification or handler
dispatch.
"""

from __future__ import annotations

from typing import Any, Literal

from core.utils.images import sniff_image_type
from core.utils.media import sniff_audio_type, sniff_document_type

Modality = Literal["image", "pdf", "audio", "video", "text"]

#: Filename extension → modality. Lowercase, no leading dot.
_EXTENSION_MODALITIES: dict[str, Modality] = {
    # Images
    "png": "image",
    "jpg": "image",
    "jpeg": "image",
    "gif": "image",
    "webp": "image",
    "bmp": "image",
    "tif": "image",
    "tiff": "image",
    # Documents
    "pdf": "pdf",
    # Audio
    "mp3": "audio",
    "wav": "audio",
    "ogg": "audio",
    "oga": "audio",
    "flac": "audio",
    "m4a": "audio",
    "aac": "audio",
    # Video
    "mp4": "video",
    "m4v": "video",
    "mov": "video",
    "avi": "video",
    "mkv": "video",
    "webm": "video",
}


def _sniff(data: bytes) -> Modality | None:
    """Identify a payload from its magic bytes, or None when unrecognized."""
    # Raster images first: sniff_image_type already disambiguates RIFF-WEBP.
    if sniff_image_type(data) is not None:
        return "image"
    if sniff_document_type(data) is not None:
        return "pdf"
    # Audio next (ID3 / OggS / fLaC / RIFF-WAVE / bare MPEG frame sync); the
    # signature knowledge lives in core.utils.media, shared with the vision
    # service's native audio path.
    if sniff_audio_type(data) is not None:
        return "audio"
    if data[:4] == b"RIFF":
        # RIFF is a container: WAVE was already claimed by the audio sniffer,
        # so only the AVI form type remains meaningful here.
        return "video" if data[8:12] == b"AVI " else None
    if data[4:8] == b"ftyp":
        # ISO-BMFF (MP4/MOV family). The M4A brand is audio-only.
        return "audio" if data[8:11] == b"M4A" else "video"
    if data.startswith(b"\x1a\x45\xdf\xa3"):  # EBML: Matroska / WebM
        return "video"
    return None


def _from_mime(mime: str) -> Modality | None:
    """Map a declared MIME type to a modality, or None when unmapped."""
    normalized = mime.strip().lower().split(";", 1)[0]
    if normalized == "application/pdf":
        return "pdf"
    family = normalized.split("/", 1)[0]
    if family == "image":
        return "image"
    if family == "audio":
        return "audio"
    if family == "video":
        return "video"
    if family == "text":
        return "text"
    return None


def _from_extension(filename: str) -> Modality | None:
    """Map a filename extension to a modality, or None when unmapped."""
    _, dot, extension = filename.rpartition(".")
    if not dot:
        return None
    return _EXTENSION_MODALITIES.get(extension.strip().lower())


def detect_modality(
    data: bytes | None = None,
    *,
    filename: str | None = None,
    mime: str | None = None,
) -> Modality:
    """Detect the modality of an attachment payload.

    Detection order is trust order: magic bytes (cannot be misreported) →
    declared MIME type → filename extension → ``"text"``.

    Args:
        data: Raw payload bytes; only the first bytes are read.
        filename: Original filename, used for extension fallback.
        mime: Declared MIME type (a ``Content-Type`` value is accepted).

    Returns:
        One of ``"image"``, ``"pdf"``, ``"audio"``, ``"video"``, ``"text"``.
    """
    if data:
        sniffed = _sniff(data)
        if sniffed is not None:
            return sniffed
    if mime:
        from_mime = _from_mime(mime)
        if from_mime is not None:
            return from_mime
    if filename:
        from_extension = _from_extension(filename)
        if from_extension is not None:
            return from_extension
    return "text"


def annotate_context(
    context: dict[str, Any],
    data: bytes | None = None,
    *,
    filename: str | None = None,
    mime: str | None = None,
) -> Modality:
    """Detect the modality and stamp it onto *context* as ``"modality"``.

    Args:
        context: Orchestration context dict to annotate (mutated in place).
        data: Raw payload bytes, if available.
        filename: Original filename, if available.
        mime: Declared MIME type, if available.

    Returns:
        The detected modality (also written to ``context["modality"]``).
    """
    modality = detect_modality(data, filename=filename, mime=mime)
    context["modality"] = modality
    return modality


__all__ = ["Modality", "annotate_context", "detect_modality"]
