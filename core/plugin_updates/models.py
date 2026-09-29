"""Data models for signed plugin updates: releases, verdicts and check reports."""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, ConfigDict


class Refusal(StrEnum):
    """Why a release is not offered or not installable."""

    MANIFEST_INVALID = "manifest_invalid"
    NAME_MISMATCH = "name_mismatch"
    VERSION_MISMATCH = "version_mismatch"
    INTEGRITY_MISMATCH = "integrity_mismatch"
    SIGNATURE_INVALID = "signature_invalid"
    NO_TRUSTED_KEYS = "no_trusted_keys"
    NOT_NEWER = "not_newer"
    INCOMPATIBLE_CORE = "incompatible_core"
    NEEDS_ENVIRONMENT_UPDATE = "needs_environment_update"
    ARTIFACT_MISSING = "artifact_missing"
    ARTIFACT_CHECKSUM = "artifact_checksum"
    SOURCE_ERROR = "source_error"


class VerificationResult(BaseModel):
    """Outcome of verifying an unpacked release; ``ok`` when nothing refused it."""

    model_config = ConfigDict(frozen=True)

    refusal: Refusal | None = None
    detail: str = ""

    @property
    def ok(self) -> bool:
        """True when the release passed every check."""
        return self.refusal is None


class ReleaseInfo(BaseModel):
    """A published release of a plugin, as reported by its source."""

    model_config = ConfigDict(frozen=True)

    plugin: str
    version: str
    tag: str
    published_at: datetime | None
    notes: str = ""
    html_url: str = ""
    tarball_url: str | None = None
    release_json_url: str | None = None


class UpdateCandidate(BaseModel):
    """One plugin's update status."""

    plugin: str
    installed_version: str | None
    latest: ReleaseInfo | None
    available: bool
    refusal: Refusal | None = None
    detail: str = ""


class Advisory(BaseModel):
    """A published GitHub Security Advisory for one vulnerable version range."""

    model_config = ConfigDict(frozen=True)

    ghsa_id: str
    severity: str  # "critical" | "high" | "medium" | "low" | "unknown"
    summary: str
    html_url: str
    vulnerable_range: str
    patched_versions: str


class SystemUpdate(BaseModel):
    """Status of the running core against the public core project's releases.

    ``installed_version`` is the public core release the running tree
    corresponds to (``core._core_version.CORE_VERSION``), never a downstream
    distribution's own version, and ``repo`` is the public core repository.
    ``upgrade_guide_url`` is the operator's upgrade instructions
    (``SYSTEM_UPGRADE_GUIDE_URL``), stamped from the current configuration
    whenever the report is served. Notice only: nothing is ever installed from
    this data.
    """

    component: str = "core"
    repo: str
    installed_version: str
    latest: ReleaseInfo | None = None
    available: bool = False
    behind: int = 0
    major: bool = False
    security: bool = False
    severity: str | None = None
    advisories: list[Advisory] = []
    error: str | None = None
    upgrade_guide_url: str | None = None


class CheckReport(BaseModel):
    """Result of a full update check across plugins and the framework."""

    checked_at: datetime
    candidates: list[UpdateCandidate]
    error: str | None = None
    system: SystemUpdate | None = None


__all__ = [
    "Advisory",
    "CheckReport",
    "Refusal",
    "ReleaseInfo",
    "SystemUpdate",
    "UpdateCandidate",
    "VerificationResult",
]
