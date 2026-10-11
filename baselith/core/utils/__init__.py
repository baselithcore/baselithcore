"""
Core Utilities.

Shared utility functions used across multiple core modules.

Re-exports are resolved lazily. ``baselith.core.utils.logsafe`` is imported by the
logging shim and by the plugin integrity/signature gates, and an eager
``from baselith.core.utils.tokens import ...`` here dragged ``baselith.core.observability.logging``
(and numpy) into that path — a circular import for the logger itself.
"""

from typing import TYPE_CHECKING, Any

_EXPORTS: dict[str, str] = {
    "canonical_key": "baselith.core.utils.text_canon",
    "canonicalize": "baselith.core.utils.text_canon",
    "cosine_similarity": "baselith.core.utils.similarity",
    "cosine_similarity_many": "baselith.core.utils.similarity",
    "count_tokens_exact_available": "baselith.core.utils.tokens",
    "estimate_tokens": "baselith.core.utils.tokens",
    "sanitize_log_value": "baselith.core.utils.logsafe",
    "sniff_audio_type": "baselith.core.utils.media",
    "sniff_document_type": "baselith.core.utils.media",
    "sniff_image_type": "baselith.core.utils.images",
}

__all__ = [
    "canonical_key",
    "canonicalize",
    "cosine_similarity",
    "cosine_similarity_many",
    "count_tokens_exact_available",
    "estimate_tokens",
    "sanitize_log_value",
    "sniff_audio_type",
    "sniff_document_type",
    "sniff_image_type",
]

if TYPE_CHECKING:  # pragma: no cover - import-time typing only
    from baselith.core.utils.images import sniff_image_type
    from baselith.core.utils.logsafe import sanitize_log_value
    from baselith.core.utils.media import sniff_audio_type, sniff_document_type
    from baselith.core.utils.similarity import cosine_similarity, cosine_similarity_many
    from baselith.core.utils.text_canon import canonical_key, canonicalize
    from baselith.core.utils.tokens import count_tokens_exact_available, estimate_tokens


def __getattr__(name: str) -> Any:
    """Import the owning submodule on first attribute access."""
    module_name = _EXPORTS.get(name)
    if module_name is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    from importlib import import_module

    return getattr(import_module(module_name), name)


def __dir__() -> list[str]:
    return sorted(__all__)
