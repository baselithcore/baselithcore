"""The core host driver's steps: verify the manifest, back up, switch commits, migrate.

Split out of :mod:`.core_host` (the state machine) so each half stays small.
Every function is blocking or async on injected seams only; none writes a run
and none logs a path, a URL or an environment value. A step that fails
returns a failure code and a detail for the run, never raises (except where
its docstring says so).
"""

from __future__ import annotations

import logging
import sys
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path

from baselith.core.config.plugin_update_apply import UpdateApplyConfig

from ...distribution_manifest import (
    DistributionManifest,
    DistributionRefused,
    load_distribution_manifest,
)
from ...release_manifest import ReleaseManifestError
from ...sources import safe_error
from .._io import CommandRunner
from ._core_guards import tree_is_dirty
from ._fetch import DistributionFetcher
from ._git import (
    GitError,
    GitRunner,
    checkout_detached,
    fetch_commit,
    head_sha,
    lock_sha256,
    porcelain,
)

logger = logging.getLogger(__name__)

_MIGRATE_TIMEOUT = 1800.0
_SCHEMA_INIT_TIMEOUT = 600.0
#: The run message of a migration that failed after the checkout.
MIGRATION_UNTOUCHED = "nothing changed on the running API; the schema may have advanced"


def DB_MIGRATE_ARGV() -> list[str]:
    """argv of ``baselith db migrate`` in this interpreter."""
    return [sys.executable, "-m", "baselith.core.cli", "db", "migrate"]


def SCHEMA_INIT_ALL_ARGV() -> list[str]:
    """argv of ``baselith plugin schema-init`` (every enabled plugin)."""
    return [sys.executable, "-m", "baselith.core.cli", "plugin", "schema-init"]


@dataclass(frozen=True)
class StepFailure:
    """Why a step failed: the run's failure code and a path-free detail."""

    code: str
    detail: str = ""


async def verified_manifest(
    fetcher: DistributionFetcher,
    keys: Sequence[str],
    *,
    name: str,
    version: str,
    pinned_commit: str | None,
) -> DistributionManifest | StepFailure:
    """The verified manifest of ``version``, held to the commit pinned at request time.

    ``pinned_commit`` is None only before a run exists (a request verifying
    what it is about to pin).

    A refused manifest fails with its refusal code; an unreachable release
    or a malformed file is ``verification_failed``; a valid manifest naming
    another commit is ``commit_mismatch``.
    """
    try:
        raw = await fetcher.manifest(version)
    except Exception as exc:  # untrusted network: never crash the run
        return StepFailure("verification_failed", safe_error(exc))
    try:
        manifest = load_distribution_manifest(
            raw, keys, expected_name=name, expected_version=version
        )
    except DistributionRefused as exc:
        return StepFailure(exc.refusal.value, exc.detail)
    except ReleaseManifestError as exc:
        return StepFailure("verification_failed", str(exc))
    if pinned_commit is not None and manifest.commit_sha != pinned_commit.lower():
        return StepFailure(
            "commit_mismatch", "the release names another commit than the one requested"
        )
    return manifest


def host_state(
    git: GitRunner, repo_root: Path, ignored: Sequence[str]
) -> tuple[str | None, bool | None]:
    """``(uv.lock sha256, tree dirty)`` of the checkout; ``None`` where unreadable."""
    try:
        lock: str | None = lock_sha256(repo_root)
    except OSError:
        lock = None
    try:
        dirty: bool | None = tree_is_dirty(porcelain(git, repo_root), ignored)
    except GitError:
        dirty = None
    return lock, dirty


def current_head(git: GitRunner, repo_root: Path) -> str | None:
    """HEAD of the checkout, or None when it cannot be read."""
    try:
        return head_sha(git, repo_root)
    except GitError:
        return None


def run_backup(runner: CommandRunner, config: UpdateApplyConfig) -> StepFailure | None:
    """Run ``UPDATE_APPLY_BACKUP_COMMAND``; ``backup_failed`` on a non-zero exit."""
    if not config.backup_configured:
        return StepFailure("backup_unconfigured", "no backup command configured")
    rc = runner(
        list(config.backup_command), timeout=float(config.backup_timeout_seconds)
    )
    if rc != 0:
        return StepFailure("backup_failed", f"the backup command exited {rc}")
    return None


def switch_to(
    git: GitRunner, repo_root: Path, remote_url: str | None, sha: str
) -> StepFailure | None:
    """Make ``sha`` present and check it out detached (``checkout_failed`` otherwise)."""
    if not fetch_commit(git, repo_root, remote_url, sha):
        return StepFailure("checkout_failed", "the pinned commit could not be fetched")
    if not checkout_detached(git, repo_root, sha):
        return StepFailure(
            "checkout_failed", "the pinned commit could not be checked out"
        )
    return None


def restore(git: GitRunner, repo_root: Path, sha: str | None) -> bool:
    """Put HEAD back on ``sha`` (a no-op when it is there); False when it cannot."""
    if sha is None:
        return False
    if current_head(git, repo_root) == sha:
        return True
    return checkout_detached(git, repo_root, sha)


def lock_matches(repo_root: Path, expected: str) -> bool:
    """The checked-out ``uv.lock`` hashes to ``expected`` (unreadable: False)."""
    try:
        return lock_sha256(repo_root) == expected
    except OSError:
        return False


def migrate(
    runner: CommandRunner,
    env: dict[str, str],
    *,
    schema_init: bool,
    argv: Callable[[], list[str]] | None = None,
) -> StepFailure | None:
    """``baselith db migrate``, then ``plugin schema-init``, with the owner's env.

    The environment holds the schema owner's credentials: it is handed to
    the two subprocesses and never logged.
    """
    rc = runner((argv or DB_MIGRATE_ARGV)(), env=env, timeout=_MIGRATE_TIMEOUT)
    if rc != 0:
        return StepFailure(
            "migration_failed", f"db migrate exited {rc}; {MIGRATION_UNTOUCHED}"
        )
    if schema_init:
        rc = runner(SCHEMA_INIT_ALL_ARGV(), env=env, timeout=_SCHEMA_INIT_TIMEOUT)
        if rc != 0:
            return StepFailure(
                "migration_failed", f"schema-init exited {rc}; {MIGRATION_UNTOUCHED}"
            )
    return None


__all__ = [
    "DB_MIGRATE_ARGV",
    "MIGRATION_UNTOUCHED",
    "SCHEMA_INIT_ALL_ARGV",
    "StepFailure",
    "current_head",
    "host_state",
    "lock_matches",
    "migrate",
    "restore",
    "run_backup",
    "switch_to",
    "verified_manifest",
]
