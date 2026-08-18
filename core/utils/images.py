"""What an image is, decided from its own bytes.

Two callers need this and neither can trust a label: the provider layer, whose
image endpoints document one format and return another for some models, and
any upload path, where the browser's ``Content-Type`` is whatever the client
chose to send. Magic bytes are the only thing that cannot be misreported.

SVG is deliberately absent. It is a script container rather than a raster
image, and the callers here serve or commit what they accept.
"""

from __future__ import annotations

__all__ = ["sniff_image_type"]

_SIGNATURES: tuple[tuple[bytes, str], ...] = (
    (b"\x89PNG\r\n\x1a\n", "image/png"),
    (b"\xff\xd8\xff", "image/jpeg"),
    (b"GIF87a", "image/gif"),
    (b"GIF89a", "image/gif"),
)


def sniff_image_type(data: bytes) -> str | None:
    """Identify an image from its own bytes.

    Args:
        data: The candidate image; only the first bytes are read.

    Returns:
        The IANA media type, or None when the bytes are not an image this
        framework recognises (PNG, JPEG, GIF, WebP).
    """
    for signature, media_type in _SIGNATURES:
        if data.startswith(signature):
            return media_type
    # WebP is RIFF-framed: "RIFF" + 4 size bytes + "WEBP".
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    return None
