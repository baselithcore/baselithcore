"""
Utility for generating deterministic KB labels.

Previously in core.vectorstore.labels, now moved to doc_sources.
"""

from __future__ import annotations

import hashlib
import re
from pathlib import Path

# Labels for context-aware document tracking
# Deterministic per document: <slug>-<hash> (no prefix), max 50 char
KB_LABEL_PREFIX = ""
KB_LABEL_MAX_LENGTH = 50

DOC_LABEL_PREFIX = ""
DOC_LABEL_MAX_LENGTH = 50

_SLUG_PATTERN = re.compile(r"[^a-z0-9]+")


def _build_hashed_label(
    prefix: str,
    slug_source: str,
    digest_source: str,
    fallback_slug: str,
    max_length: int,
) -> str:
    slug = _SLUG_PATTERN.sub("-", slug_source.lower()).strip("-") or fallback_slug
    digest = hashlib.sha256(digest_source.encode("utf-8")).hexdigest()[:8]

    prefix_part = f"{prefix}-" if prefix else ""
    hash_part = f"-{digest}" if digest else ""
    available_for_slug = max_length - len(prefix_part) - len(hash_part)

    if len(slug) > available_for_slug:
        slug = slug[:available_for_slug].rstrip("-")

    label = f"{prefix_part}{slug}{hash_part}"
    return label


def build_kb_label(path: Path) -> str:
    """Return deterministic label for a KB document."""
    return _build_hashed_label(
        KB_LABEL_PREFIX,
        path.stem,
        f"kb::{path.stem}",
        fallback_slug="document",
        max_length=KB_LABEL_MAX_LENGTH,
    )


def build_doc_label(name: str, digest_source: str | None = None) -> str:
    """Deterministic label for one-shot analysis documents."""
    base = name or "document"
    source = digest_source or base
    digest = f"analysis::{source}"
    return _build_hashed_label(
        DOC_LABEL_PREFIX,
        base,
        digest,
        fallback_slug="document",
        max_length=DOC_LABEL_MAX_LENGTH,
    )


__all__ = [
    "build_doc_label",
    "build_kb_label",
    "KB_LABEL_PREFIX",
    "KB_LABEL_MAX_LENGTH",
    "DOC_LABEL_PREFIX",
    "DOC_LABEL_MAX_LENGTH",
]
