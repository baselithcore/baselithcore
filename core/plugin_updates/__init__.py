"""Signed plugin updates: models and the release verifier."""

from .cache import UpdateCache
from .checker import check_plugin, installed_versions, run_check
from .github import repo_slug
from .models import (
    Advisory,
    CheckReport,
    Refusal,
    ReleaseInfo,
    SystemUpdate,
    UpdateCandidate,
    VerificationResult,
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
    "CheckReport",
    "PluginUpdateService",
    "Refusal",
    "ReleaseInfo",
    "SystemUpdate",
    "UpdateCache",
    "UpdateCandidate",
    "VerificationResult",
    "affects",
    "check_plugin",
    "check_system",
    "get_plugin_update_service",
    "installed_versions",
    "repo_slug",
    "run_check",
    "set_plugin_update_service",
    "verify_release",
]
