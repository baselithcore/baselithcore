"""
AI-interaction disclosure (EU AI Act Article 50(1)).

Providers of AI systems intended to interact directly with natural persons must
ensure those persons are informed they are interacting with an AI system, unless
that is obvious from the context to a reasonably well-informed person.

:class:`DisclosureService` produces a :class:`~core.transparency.types.DisclosureNotice`
that a chat/API surface can attach to its response. The ``obvious`` exemption is
modelled explicitly so a caller can suppress the notice in contexts where AI use
is already unmistakable, while keeping the decision auditable.
"""

from __future__ import annotations

from core.transparency.types import DisclosureNotice

DEFAULT_DISCLOSURE_TEXT = (
    "You are interacting with an AI system. Responses are generated "
    "automatically and may contain errors."
)


class DisclosureService:
    """Builds AI-interaction disclosure notices for end users."""

    def __init__(
        self,
        *,
        text: str = DEFAULT_DISCLOSURE_TEXT,
        provider: str | None = None,
        enabled: bool = True,
    ) -> None:
        self._text = text
        self._provider = provider
        self._enabled = enabled

    @property
    def enabled(self) -> bool:
        return self._enabled

    def should_disclose(self, *, obvious: bool = False) -> bool:
        """Whether a disclosure is required.

        Args:
            obvious: Set ``True`` when AI involvement is already obvious from the
                context (the Art 50(1) exemption), suppressing the notice.
        """
        return self._enabled and not obvious

    def notice(self) -> DisclosureNotice:
        """Build the disclosure notice."""
        return DisclosureNotice(text=self._text, provider=self._provider)
