"""
Core Utilities.

Shared utility functions used across multiple core modules.

Re-exports are resolved lazily. ``core.utils.logsafe`` is imported by the
logging shim and by the plugin integrity/signature gates, and an eager
``from core.utils.tokens import ...`` here dragged ``core.observability.logging``
(and numpy) into that path — a circular import for the logger itself.
"""

from typing import TYPE_CHECKING, Any

_EXPORTS: dict[str, str] = {
    "cosine_similarity": "core.utils.similarity",
    "cosine_similarity_many": "core.utils.similarity",
    "estimate_tokens": "core.utils.tokens",
    "sanitize_log_value": "core.utils.logsafe",
    "sniff_image_type": "core.utils.images",
}

__all__ = [
    "cosine_similarity",
    "cosine_similarity_many",
    "estimate_tokens",
    "sanitize_log_value",
    "sniff_image_type",
]

if TYPE_CHECKING:  # pragma: no cover - import-time typing only
    from core.utils.images import sniff_image_type
    from core.utils.logsafe import sanitize_log_value
    from core.utils.similarity import cosine_similarity, cosine_similarity_many
    from core.utils.tokens import estimate_tokens


def __getattr__(name: str) -> Any:
    """Import the owning submodule on first attribute access."""
    module_name = _EXPORTS.get(name)
    if module_name is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    from importlib import import_module

    return getattr(import_module(module_name), name)


def __dir__() -> list[str]:
    return sorted(__all__)
