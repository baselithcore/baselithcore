"""Backward-compatible shim for the Document Sources plugin package."""

import sys

from core.utils.optional_import import optional_module

_document_sources = optional_module("plugins.document_sources")


class _FallbackDocumentSourceError(Exception):
    """Raised when optional document source configuration is invalid."""


DocumentSourceError = _FallbackDocumentSourceError


def create_document_sources(
    *,
    space_filter: list[str] | None = None,
) -> list[tuple[str, object]]:
    """Keep the source factory contract when the optional plugin is absent."""
    return []


if _document_sources is not None:
    # Preserve the plugin's module identity and complete public surface.
    sys.modules[__name__] = _document_sources

__all__ = ["DocumentSourceError", "create_document_sources"]
