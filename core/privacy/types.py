"""
Types for data-subject requests (GDPR access / portability / erasure).

A *subject* is identified by an opaque ``subject_id`` string; each
:class:`~core.privacy.provider.DataProvider` decides how that maps to its own
records (a user id, a conversation id, a tenant id, …). The framework aggregates
across providers — it does not assume a single global identity scheme.
"""

from __future__ import annotations

import time
from typing import Any

from pydantic import BaseModel, Field


class PrivacyError(Exception):
    """Base error for the privacy/DSR subsystem."""


class SubjectExport(BaseModel):
    """The aggregated export bundle for a data subject (right to access)."""

    subject_id: str
    generated_at: float = Field(default_factory=time.time)
    # provider name -> that provider's exported records for the subject.
    data: dict[str, Any] = Field(default_factory=dict)


class ErasureReport(BaseModel):
    """Per-provider record counts removed for a subject (right to erasure)."""

    subject_id: str
    completed_at: float = Field(default_factory=time.time)
    erased: dict[str, int] = Field(default_factory=dict)

    @property
    def total(self) -> int:
        return sum(self.erased.values())


class RetentionReport(BaseModel):
    """Per-provider record counts purged by a retention sweep."""

    older_than_seconds: int
    completed_at: float = Field(default_factory=time.time)
    purged: dict[str, int] = Field(default_factory=dict)

    @property
    def total(self) -> int:
        return sum(self.purged.values())
