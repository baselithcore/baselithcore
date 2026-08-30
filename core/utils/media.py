"""What a document or audio payload is, decided from its own bytes.

Companion to :mod:`core.utils.images`, for the same reason: a declared
``Content-Type`` or filename is whatever the sender chose, so the native
document/audio content models fail closed on these sniffers before any
payload reaches a provider. :mod:`core.orchestration.modality_router`
delegates its PDF/audio prefixes here so the signature knowledge lives in
exactly one place.
"""

from __future__ import annotations

__all__ = ["sniff_audio_type", "sniff_document_type"]

_AUDIO_SIGNATURES: tuple[tuple[bytes, str], ...] = (
    (b"ID3", "audio/mpeg"),
    (b"OggS", "audio/ogg"),
    (b"fLaC", "audio/flac"),
)


def sniff_document_type(data: bytes) -> str | None:
    """Identify a document from its own bytes.

    Args:
        data: The candidate document; only the first bytes are read.

    Returns:
        The IANA media type (currently only ``application/pdf``), or None
        when the bytes are not a document this framework recognises.
    """
    if data.startswith(b"%PDF"):
        return "application/pdf"
    return None


def sniff_audio_type(data: bytes) -> str | None:
    """Identify an audio payload from its own bytes.

    Recognises the formats the vision service accepts natively: WAV
    (RIFF-framed), MP3 (ID3-tagged or a bare MPEG frame sync), OGG and FLAC.
    The frame-sync check runs last — it is the loosest signature (0xFF plus
    three sync bits), so every exact prefix gets first claim.

    Args:
        data: The candidate audio; only the first bytes are read.

    Returns:
        The IANA media type (``audio/wav``, ``audio/mpeg``, ``audio/ogg``,
        ``audio/flac``), or None when the bytes are not audio this framework
        recognises.
    """
    for signature, media_type in _AUDIO_SIGNATURES:
        if data.startswith(signature):
            return media_type
    # WAV is RIFF-framed: "RIFF" + 4 size bytes + "WAVE".
    if data[:4] == b"RIFF" and data[8:12] == b"WAVE":
        return "audio/wav"
    # Bare MPEG frame sync (MP3 without an ID3 tag): 11 set bits.
    if len(data) >= 2 and data[0] == 0xFF and (data[1] & 0xE0) == 0xE0:
        return "audio/mpeg"
    return None
