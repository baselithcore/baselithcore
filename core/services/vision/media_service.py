"""Native document/audio analysis surface for the vision service.

``MediaAnalysisMixin`` adds ``analyze_document`` / ``analyze_audio`` to
:class:`~core.services.vision.service.VisionService` — a sibling module so
``service.py`` stays under the 500-line cap. Provider selection mirrors the
image path exactly: explicit argument, else the service default.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, cast

from core.observability.logging import get_logger
from core.services.vision import media_backends
from core.services.vision.media_models import (
    AudioContent,
    DocumentContent,
    UnsupportedContentError,
)
from core.services.vision.models import VisionProvider

if TYPE_CHECKING:  # pragma: no cover
    from core.services.vision.service import VisionService

logger = get_logger(__name__)


class MediaAnalysisMixin:
    """Document and audio analysis methods mixed into ``VisionService``.

    Relies on the host class for provider defaults, resolved API keys and
    the shared HTTP/SDK clients.
    """

    default_provider: VisionProvider

    async def analyze_document(
        self,
        document: DocumentContent,
        prompt: str,
        *,
        provider: VisionProvider | None = None,
    ) -> str:
        """Analyze a document passed natively to a capable provider.

        Note:
            There is no in-core text-extraction fallback. Document text
            extraction lives in the ``document_sources`` plugin, and the
            Sacred Core boundary forbids ``core -> plugins`` imports — so
            when the selected provider has no native document path, the
            :class:`UnsupportedContentError` is re-raised with a message
            pointing operators at that extraction pipeline.

        Args:
            document: The document to analyze (PDF, inline bytes or URI).
            prompt: Instructions for the analysis.
            provider: Provider override; defaults to the service default.

        Returns:
            str: The provider's textual answer.

        Raises:
            UnsupportedContentError: If the provider has no native document
                path (OpenAI chat-completions, Ollama).
        """
        service = cast("VisionService", self)
        resolved = provider or service.default_provider
        logger.info(
            "vision_analyze_document_start",
            provider=resolved.value,
            source_type=document.source_type,
        )
        try:
            response = await media_backends.analyze_document(
                service, document, prompt, resolved
            )
        except UnsupportedContentError as exc:
            raise UnsupportedContentError(
                exc.provider,
                exc.content_type,
                f"{exc} No text-extraction fallback exists inside core: "
                "extraction lives in the document_sources plugin "
                "(plugins/document_sources). Extract the text there and send "
                "it as a plain prompt, or select a document-capable provider "
                "(anthropic, google).",
            ) from exc
        return response.content

    async def analyze_audio(
        self,
        audio: AudioContent,
        prompt: str,
        *,
        provider: VisionProvider | None = None,
    ) -> str:
        """Analyze an audio clip passed natively to a capable provider.

        Audio has no extraction fallback:
        :class:`UnsupportedContentError` propagates untouched.

        Args:
            audio: The audio clip to analyze.
            prompt: Instructions for the analysis.
            provider: Provider override; defaults to the service default.

        Returns:
            str: The provider's textual answer.

        Raises:
            UnsupportedContentError: If the provider has no native audio path
                (Anthropic, Ollama) or cannot take this format (OpenAI with
                OGG/FLAC).
        """
        service = cast("VisionService", self)
        resolved = provider or service.default_provider
        logger.info(
            "vision_analyze_audio_start",
            provider=resolved.value,
            media_type=audio.media_type,
        )
        response = await media_backends.analyze_audio(service, audio, prompt, resolved)
        return response.content


__all__ = ["MediaAnalysisMixin"]
