"""Native document and audio content models for the vision service.

``DocumentContent`` and ``AudioContent`` carry PDFs and audio to providers
that accept them natively, instead of flattening to extracted text. Both
fail closed at construction: the payload's magic bytes
(:mod:`core.utils.media`) must agree with the declared media type — a
mislabeled or unrecognisable payload is rejected, mirroring how the
browser-vision path treats media types.

``UnsupportedContentError`` is the shared signal for a provider that has no
native path for a content type.
"""

from __future__ import annotations

import base64
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from core.utils.media import sniff_audio_type, sniff_document_type

_DOCUMENT_MEDIA_TYPES = frozenset({"application/pdf"})
_AUDIO_MEDIA_TYPES = frozenset({"audio/wav", "audio/mpeg", "audio/ogg", "audio/flac"})
#: OpenAI ``input_audio`` accepts only these container formats.
_OPENAI_AUDIO_FORMATS: dict[str, str] = {"audio/wav": "wav", "audio/mpeg": "mp3"}


class UnsupportedContentError(Exception):
    """A provider has no native path for the given content type.

    Attributes:
        provider: Provider name (``"openai"``, ``"anthropic"``, ``"google"``,
            ``"ollama"``).
        content_type: The media type the provider cannot accept.
    """

    def __init__(
        self, provider: str, content_type: str, message: str | None = None
    ) -> None:
        self.provider = provider
        self.content_type = content_type
        super().__init__(
            message
            or f"Provider {provider!r} does not support {content_type!r} "
            "content natively."
        )


@dataclass(frozen=True)
class DocumentContent:
    """A document passed natively to a capable provider.

    Exactly one of ``data`` (raw bytes) and ``uri`` (publicly reachable URL)
    must be set. Inline bytes are sniffed on construction and rejected when
    their magic bytes disagree with ``media_type`` — labels can lie, bytes
    cannot (fail closed).
    """

    data: bytes | None = None
    uri: str | None = None
    media_type: str = "application/pdf"
    source_type: Literal["bytes", "uri", "file"] = "bytes"

    def __post_init__(self) -> None:
        """Validate the one-of source rule and the payload's magic bytes."""
        if (self.data is None) == (self.uri is None):
            raise ValueError(
                "Exactly one of 'data' and 'uri' must be provided for a document."
            )
        if self.uri is not None and self.source_type != "uri":
            # Normalize: a URI-sourced document is always source_type "uri".
            object.__setattr__(self, "source_type", "uri")
        if self.media_type not in _DOCUMENT_MEDIA_TYPES:
            raise ValueError(
                f"Unsupported document media type {self.media_type!r}; "
                f"supported: {sorted(_DOCUMENT_MEDIA_TYPES)}."
            )
        if self.data is not None:
            sniffed = sniff_document_type(self.data)
            if sniffed != self.media_type:
                raise ValueError(
                    f"Document payload does not match declared media type "
                    f"{self.media_type!r}: magic bytes identify it as {sniffed!r}."
                )

    @classmethod
    def from_bytes(
        cls, data: bytes, media_type: str = "application/pdf"
    ) -> DocumentContent:
        """Create a DocumentContent instance from raw bytes.

        Args:
            data: Raw document bytes (validated against ``media_type``).
            media_type: The IANA media type (default: application/pdf).

        Returns:
            DocumentContent: A byte-sourced document content instance.
        """
        return cls(data=data, media_type=media_type, source_type="bytes")

    @classmethod
    def from_file(cls, path: str | Path) -> DocumentContent:
        """Load a document from a local file.

        Args:
            path: Absolute or relative path to the document file.

        Returns:
            DocumentContent: A file-sourced document content instance.

        Raises:
            FileNotFoundError: If the file does not exist.
            ValueError: If the file's bytes are not a supported document.
        """
        file_path = Path(path)
        if not file_path.exists():
            raise FileNotFoundError(f"Document file not found: {path}")
        return cls(data=file_path.read_bytes(), source_type="file")

    @classmethod
    def from_uri(cls, uri: str) -> DocumentContent:
        """Create a DocumentContent instance referencing a remote URL.

        Args:
            uri: Publicly accessible URL of the document.

        Returns:
            DocumentContent: A URI-referenced document content instance.
        """
        return cls(uri=uri, source_type="uri")

    @property
    def data_base64(self) -> str:
        """Inline bytes encoded as base64 ASCII.

        Raises:
            ValueError: If this document is URI-sourced (no inline bytes).
        """
        if self.data is None:
            raise ValueError("URI-sourced document has no inline data.")
        return base64.b64encode(self.data).decode("ascii")

    def to_anthropic_format(self) -> dict[str, Any]:
        """Format the document as an Anthropic ``document`` content block.

        Returns:
            dict[str, Any]: Anthropic-compatible message content block
            (``url`` source for URI documents, ``base64`` otherwise).
        """
        if self.uri is not None:
            return {"type": "document", "source": {"type": "url", "url": self.uri}}
        return {
            "type": "document",
            "source": {
                "type": "base64",
                "media_type": self.media_type,
                "data": self.data_base64,
            },
        }

    def to_google_part(self) -> dict[str, Any]:
        """Format the document as a Gemini ``generateContent`` part.

        Returns:
            dict[str, Any]: ``file_data`` part for URI documents,
            ``inline_data`` part otherwise.
        """
        if self.uri is not None:
            return {"file_data": {"mime_type": self.media_type, "file_uri": self.uri}}
        return {"inline_data": {"mime_type": self.media_type, "data": self.data_base64}}


@dataclass(frozen=True)
class AudioContent:
    """An audio clip passed natively to a capable provider.

    The payload's magic bytes must agree with ``media_type``; unrecognised
    or mislabeled bytes are rejected at construction (fail closed).
    """

    data: bytes
    media_type: str = "audio/wav"
    source_type: Literal["bytes", "file"] = "bytes"

    def __post_init__(self) -> None:
        """Validate the declared media type against the payload's magic bytes."""
        if not self.data:
            raise ValueError("Audio payload must not be empty.")
        if self.media_type not in _AUDIO_MEDIA_TYPES:
            raise ValueError(
                f"Unsupported audio media type {self.media_type!r}; "
                f"supported: {sorted(_AUDIO_MEDIA_TYPES)}."
            )
        sniffed = sniff_audio_type(self.data)
        if sniffed != self.media_type:
            raise ValueError(
                f"Audio payload does not match declared media type "
                f"{self.media_type!r}: magic bytes identify it as {sniffed!r}."
            )

    @classmethod
    def from_bytes(cls, data: bytes, media_type: str | None = None) -> AudioContent:
        """Create an AudioContent instance from raw bytes.

        Args:
            data: Raw audio bytes.
            media_type: The IANA media type; sniffed from the bytes when
                omitted.

        Returns:
            AudioContent: A byte-sourced audio content instance.

        Raises:
            ValueError: If the bytes are not a supported audio format.
        """
        resolved = media_type or sniff_audio_type(data)
        if resolved is None:
            raise ValueError("Audio payload bytes are not a recognised format.")
        return cls(data=data, media_type=resolved, source_type="bytes")

    @classmethod
    def from_file(cls, path: str | Path) -> AudioContent:
        """Load an audio clip from a local file, sniffing its media type.

        Args:
            path: Absolute or relative path to the audio file.

        Returns:
            AudioContent: A file-sourced audio content instance.

        Raises:
            FileNotFoundError: If the file does not exist.
            ValueError: If the file's bytes are not a supported audio format.
        """
        file_path = Path(path)
        if not file_path.exists():
            raise FileNotFoundError(f"Audio file not found: {path}")
        data = file_path.read_bytes()
        media_type = sniff_audio_type(data)
        if media_type is None:
            raise ValueError(f"File is not a recognised audio format: {path}")
        return cls(data=data, media_type=media_type, source_type="file")

    @property
    def data_base64(self) -> str:
        """The audio bytes encoded as base64 ASCII."""
        return base64.b64encode(self.data).decode("ascii")

    def to_openai_format(self) -> dict[str, Any]:
        """Format the audio as an OpenAI ``input_audio`` content part.

        Returns:
            dict[str, Any]: OpenAI-compatible message content part.

        Raises:
            UnsupportedContentError: If the format is not one OpenAI's
                ``input_audio`` accepts (WAV and MP3 only).
        """
        audio_format = _OPENAI_AUDIO_FORMATS.get(self.media_type)
        if audio_format is None:
            raise UnsupportedContentError(
                "openai",
                self.media_type,
                f"OpenAI 'input_audio' accepts only WAV and MP3; got "
                f"{self.media_type!r}.",
            )
        return {
            "type": "input_audio",
            "input_audio": {"data": self.data_base64, "format": audio_format},
        }

    def to_google_part(self) -> dict[str, Any]:
        """Format the audio as a Gemini ``inline_data`` part.

        Returns:
            dict[str, Any]: Gemini-compatible ``generateContent`` part.
        """
        return {"inline_data": {"mime_type": self.media_type, "data": self.data_base64}}


__all__ = ["AudioContent", "DocumentContent", "UnsupportedContentError"]
