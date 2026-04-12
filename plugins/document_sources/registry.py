"""
Document Source Registry.

Central registry for document source types and reader registration.
"""

from __future__ import annotations

from typing import Callable, List, Optional, Sequence, Tuple

from .models import DocumentSourceError

SourceFactory = Callable[[], object]

_REGISTRY: List[Tuple[str, SourceFactory]] = []


def register_source(name: str, factory: SourceFactory) -> None:
    """
    Registers a factory for a document source.

    If the name is already registered, it is overwritten, maintaining the registration order.
    """

    normalized_name = (name or "").strip()
    if not normalized_name:
        raise ValueError("Source name cannot be empty.")
    if not callable(factory):
        raise TypeError("Source factory must be callable.")

    for idx, (existing_name, _) in enumerate(_REGISTRY):
        if existing_name == normalized_name:
            _REGISTRY[idx] = (normalized_name, factory)
            break
    else:
        _REGISTRY.append((normalized_name, factory))


def registered_sources() -> Sequence[str]:
    """Returns the list of registered source names (stable order)."""

    return [name for name, _ in _REGISTRY]


def create_document_sources(
    *, space_filter: Optional[List[str]] = None
) -> List[Tuple[str, object]]:
    """
    Instantiates the registered document sources.

    Args:
        space_filter: optional list of names to include (case sensitive).

    Returns:
        List of tuples (name, source instance).
    """

    allowed = {name.strip() for name in space_filter or [] if name and name.strip()}
    sources: List[Tuple[str, object]] = []

    for name, factory in _REGISTRY:
        if allowed and name not in allowed:
            continue
        try:
            instance = factory()
        except DocumentSourceError:
            raise
        except Exception as exc:  # pragma: no cover - errori runtime factory
            raise DocumentSourceError(
                f"Unable to initialize source '{name}': {exc}"
            ) from exc

        if instance is None:
            continue
        sources.append((name, instance))

    return sources
