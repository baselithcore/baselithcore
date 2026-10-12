"""Plugin updates: models, the trust modes and the release verifier."""

from .announce import AnnouncementGate, build_announcement_gate
from .cache import UpdateCache
from .checker import check_plugin, installed_versions, run_check
from .github import repo_slug
from .models import (
    Advisory,
    CheckReport,
    Refusal,
    ReleaseInfo,
    ReleaseProvenance,
    SystemUpdate,
    TrustMode,
    UpdateCandidate,
    VerificationResult,
)
from .provenance import RELEASE_WORKFLOW_AUTHOR, check_plugin_provenance
from .release_manifest import (
    file_digests,
    files_mismatch,
    sign_release_manifest,
    verify_release_manifest,
)
from .service import (
    PluginUpdateService,
    get_plugin_update_service,
    set_plugin_update_service,
)
from .system import affects, check_system
from .verifier import verify_release

__all__ = [
    "Advisory",
    "AnnouncementGate",
    "CheckReport",
    "PluginUpdateService",
    "RELEASE_WORKFLOW_AUTHOR",
    "Refusal",
    "ReleaseInfo",
    "ReleaseProvenance",
    "SystemUpdate",
    "TrustMode",
    "UpdateCache",
    "UpdateCandidate",
    "VerificationResult",
    "affects",
    "build_announcement_gate",
    "check_plugin",
    "check_plugin_provenance",
    "check_system",
    "file_digests",
    "files_mismatch",
    "get_plugin_update_service",
    "installed_versions",
    "repo_slug",
    "run_check",
    "set_plugin_update_service",
    "sign_release_manifest",
    "verify_release",
    "verify_release_manifest",
]
