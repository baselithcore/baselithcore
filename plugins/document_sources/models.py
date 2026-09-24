"""
Document Source Models.

Defines the domain models for document sources and items.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass
class DocumentItem:
    """Represents a source document to be indexed."""

    uid: str
    content: str
    fingerprint: str
    metadata: dict[str, Any]


class DocumentSourceError(Exception):
    """Generic error for document sources."""

    pass
