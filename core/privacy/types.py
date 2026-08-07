"""
Types for data-subject requests (GDPR access / portability / erasure).

A *subject* is identified by an opaque ``subject_id`` string; each
:class:`~core.privacy.provider.DataProvider` decides how that maps to its own
records (a user id, a conversation id, a tenant id, …). The framework aggregates
across providers — it does not assume a single global identity scheme.
"""

from __future__ import annotations

import time
from enum import Enum
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


class RectificationReport(BaseModel):
    """Per-provider record counts corrected for a subject (Art. 16)."""

    subject_id: str
    completed_at: float = Field(default_factory=time.time)
    corrections: dict[str, Any] = Field(default_factory=dict)
    rectified: dict[str, int] = Field(default_factory=dict)
    # Providers that hold data but cannot rectify it — Art. 19 obliges the
    # controller to communicate the rectification to each recipient, so a
    # provider that silently cannot comply must be visible, not omitted.
    unsupported: list[str] = Field(default_factory=list)

    @property
    def total(self) -> int:
        return sum(self.rectified.values())


class RestrictionReport(BaseModel):
    """Per-provider record counts restricted or released (Art. 18)."""

    subject_id: str
    restricted: bool
    completed_at: float = Field(default_factory=time.time)
    affected: dict[str, int] = Field(default_factory=dict)
    unsupported: list[str] = Field(default_factory=list)

    @property
    def total(self) -> int:
        return sum(self.affected.values())


class ObjectionOutcome(str, Enum):
    """How an Art. 21 objection was resolved.

    Art. 21(1) lets the controller continue if it demonstrates compelling
    legitimate grounds overriding the subject's interests. Art. 21(2)/(3) admits
    no such override for direct marketing: the objection is absolute.
    """

    UPHELD = "upheld"
    OVERRIDDEN = "overridden"
    PENDING = "pending"


class ObjectionRecord(BaseModel):
    """A recorded Art. 21 objection to processing and its resolution."""

    subject_id: str
    #: What the subject objected to (the processing purpose or activity).
    processing: str = ""
    direct_marketing: bool = False
    outcome: ObjectionOutcome = ObjectionOutcome.PENDING
    #: Art. 21(1): the compelling legitimate grounds, when the objection is
    #: overridden. Required — an override with no stated grounds is not one.
    override_grounds: str | None = None
    received_at: float = Field(default_factory=time.time)
    resolved_at: float | None = None
    restriction: RestrictionReport | None = None
