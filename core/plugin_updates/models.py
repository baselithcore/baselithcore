"""Data models for plugin updates: releases, verdicts and check reports."""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, ConfigDict

from .upgrade_models import PluginInstallGuidance, UpgradeInstructions


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
    LEGACY_RELEASE = "legacy_release"
    FILES_MISMATCH = "files_mismatch"
    SOURCE_ERROR = "source_error"
    #: Provenance mode: the release was not created by the mirror's own
    #: release workflow.
    UNTRUSTED_RELEASE_AUTHOR = "untrusted_release_author"
    #: Provenance mode: the release tag no longer points at the commit the
    #: release was created from.
    TAG_MOVED = "tag_moved"
    #: Provenance mode: the tagged commit is not on the repository's default
    #: branch (a release cut from a side branch, or retargeted to one).
    NOT_ON_DEFAULT_BRANCH = "not_on_default_branch"


#: How a deployment decides that a published plugin release is genuine.
#: ``provenance``: a GitHub release created by the mirror repository's own
#: release workflow, whose manifest at the tagged commit agrees with it.
#: ``signed``: an Ed25519-signed release (``release.json`` plus tarball).
TrustMode = Literal["provenance", "signed"]


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
    #: Login of the account that created the release (``author.login``).
    author: str | None = None
    #: Numeric id of that account (``author.id``).
    author_id: int | None = None
    #: Account type of that account (``author.type``: ``User``, ``Bot``...).
    author_type: str | None = None
    #: The release's ``target_commitish`` as GitHub reports it.
    target_commitish: str | None = None


class ReleaseProvenance(BaseModel):
    """Where a release came from: who published it, from which commit, when.

    ``commit_url`` is the commit's page on the plugin's repository (https,
    built from the repository slug and a validated SHA, never taken from
    the release body).
    """

    model_config = ConfigDict(frozen=True)

    author: str | None = None
    commit_sha: str | None = None
    commit_url: str | None = None
    published_at: datetime | None = None


class UpdateCandidate(BaseModel):
    """One plugin's update status."""

    plugin: str
    installed_version: str | None
    latest: ReleaseInfo | None
    available: bool
    refusal: Refusal | None = None
    detail: str = ""
    #: How the release reaches this deployment; stamped when served, never
    #: cached (an available update only).
    install: PluginInstallGuidance | None = None
    #: The trust mode the release was checked under (None: not checked).
    trust: TrustMode | None = None
    #: Who published the release, from which commit (when known).
    provenance: ReleaseProvenance | None = None


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
    whenever the report is served. ``upgrade_path`` is computed by the check
    from the published releases: the stops, in order, of an upgrade that
    crosses a major version (the latest release of each major on the way, then
    the latest release), empty for a direct upgrade. ``upgrade`` is the
    version-specific instructions for this deployment's installation method,
    stamped when served and never cached. Notice only: nothing is ever
    installed from this data.
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
    upgrade_path: list[str] = []
    upgrade: UpgradeInstructions | None = None


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
    "ReleaseProvenance",
    "SystemUpdate",
    "TrustMode",
    "UpdateCandidate",
    "VerificationResult",
]
