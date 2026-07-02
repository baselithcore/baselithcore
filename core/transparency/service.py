"""
Transparency service — unifies AI disclosure and content provenance (Art 50).

Wraps :class:`~core.transparency.disclosure.DisclosureService` (Art 50(1)) and
:class:`~core.transparency.provenance.ProvenanceTagger` (Art 50(2)/(4)) behind one
entry point and emits an ``AUDIT | TRANSPARENCY | …`` log line whenever content is
marked, so the marking of synthetic output is itself traceable for conformity
(mirrors the audit pattern used by the privacy/DSR subsystem).

The global instance is built from :func:`~core.config.transparency.get_transparency_config`
and is opt-in: when ``TRANSPARENCY_ENABLED`` is false the disclosure side reports
"not required" and callers simply skip attaching notices. The primitive itself
stays usable for tests regardless of the flag.
"""

from __future__ import annotations

from pydantic import SecretStr

from core.observability.logging import get_logger
from core.transparency.disclosure import DisclosureService
from core.transparency.provenance import ProvenanceTagger
from core.transparency.types import (
    ContentClass,
    DisclosureNotice,
    Modality,
    ProvenanceTag,
)

logger = get_logger(__name__)


class TransparencyService:
    """Facade over disclosure + provenance for Article 50 compliance."""

    def __init__(
        self,
        *,
        disclosure: DisclosureService,
        tagger: ProvenanceTagger,
    ) -> None:
        self._disclosure = disclosure
        self._tagger = tagger

    @property
    def enabled(self) -> bool:
        """Master switch (``TRANSPARENCY_ENABLED``) — gates disclosure and marking."""
        return self._disclosure.enabled

    # -- Art 50(1): disclosure -------------------------------------------------

    def should_disclose(self, *, obvious: bool = False) -> bool:
        return self._disclosure.should_disclose(obvious=obvious)

    def disclosure_notice(self) -> DisclosureNotice:
        return self._disclosure.notice()

    # -- Art 50(2)/(4): content marking ---------------------------------------

    def mark_content(
        self,
        content: str | bytes,
        *,
        content_class: ContentClass = ContentClass.AI_GENERATED,
        modality: Modality = Modality.TEXT,
        model: str | None = None,
    ) -> ProvenanceTag:
        """Produce a provenance tag for AI output and audit the marking."""
        tag = self._tagger.mark(
            content,
            content_class=content_class,
            modality=modality,
            model=model,
        )
        logger.info(
            "AUDIT | TRANSPARENCY | content marked | class=%s modality=%s model=%s signed=%s",
            tag.content_class.value,
            tag.modality.value,
            model or "-",
            tag.signature is not None,
        )
        return tag

    def verify_content(self, tag: ProvenanceTag, content: str | bytes) -> bool:
        return self._tagger.verify(tag, content)

    def provenance_header(self, tag: ProvenanceTag) -> tuple[str, str]:
        """Return the ``(header_name, value)`` pair for an HTTP response."""
        from core.transparency.provenance import PROVENANCE_HEADER

        return PROVENANCE_HEADER, self._tagger.to_header_value(tag)


_service: TransparencyService | None = None


def _build_service() -> TransparencyService:
    from core.config.transparency import get_transparency_config

    config = get_transparency_config()
    secret: SecretStr | None = config.signing_secret
    disclosure = DisclosureService(
        text=config.disclosure_text,
        provider=config.provider_name,
        enabled=config.enabled,
    )
    tagger = ProvenanceTagger(config.claim_generator, signing_secret=secret)
    return TransparencyService(disclosure=disclosure, tagger=tagger)


def get_transparency_service() -> TransparencyService:
    """Get or create the global transparency service from configuration."""
    global _service
    if _service is None:
        _service = _build_service()
    return _service
