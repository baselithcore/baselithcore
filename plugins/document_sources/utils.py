"""
Document Source Utilities.

Helper functions for path normalization, file extension checks, etc.
"""

from __future__ import annotations

import hashlib
from core.observability.logging import get_logger
from pathlib import Path
from typing import Optional

_MISSING_DEPENDENCIES: set[str] = set()

logger = get_logger(__name__)


def strip_front_matter(text: str) -> str:
    """Rimuove eventuali blocchi YAML front matter dai documenti Markdown."""

    if not text.startswith("---"):
        return text

    lines = text.splitlines()
    closing_idx: Optional[int] = None
    for idx, line in enumerate(lines[1:], start=1):
        if line.strip() == "---":
            closing_idx = idx
            break

    if closing_idx is None:
        return text

    remainder = lines[closing_idx + 1 :]
    return "\n".join(remainder)


def normalize_text(text: str) -> str:
    """Normalizza testo rimuovendo ritorni a capo inconsistenti e spazi extra."""

    return text.replace("\r\n", "\n").replace("\r", "\n").strip()


def warn_missing_dependency(name: str, feature: str) -> None:
    """Stampa un avviso la prima volta che una dipendenza opzionale manca."""

    if name in _MISSING_DEPENDENCIES:
        return
    logger.warning(f"Optional dependency missing: '{name}' (required for {feature})")
    _MISSING_DEPENDENCIES.add(name)


def compute_fingerprint(path: Path, content: str) -> str:
    """Genera un fingerprint considerando contenuto e timestamp del file."""

    digest = hashlib.sha256(content.encode("utf-8")).hexdigest()
    try:
        stamp = str(path.stat().st_mtime_ns)
    except OSError:
        stamp = "0"
    return f"{stamp}:{digest}"


def compute_remote_fingerprint(source_id: str, content: str) -> str:
    """Fingerprint per contenuti remoti, basato su URL canonico + hash."""

    normalized = (source_id or "").strip() or "remote"
    digest = hashlib.sha256(content.encode("utf-8")).hexdigest()
    return f"{normalized}:{digest}"
