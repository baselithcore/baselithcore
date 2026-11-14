"""``baselith update apply core`` and ``baselith update rollback core``.

Both create a run for the host updater and return; the updater installs,
restarts, health-checks and rolls back on its own (``baselith plugin-updater
status`` follows it). A host shell is trusted like ``plugin-updater request``:
the run is created pre-approved and audited as ``cli:<user>``.

``apply`` installs only the latest known release of the distribution (the
public core in the public core): it downloads and verifies the release's
signed ``distribution-release.json`` (read-only), prints the pre-flight with
that verdict, refuses on any ``block`` item, asks for confirmation unless
``--yes``, then pins the run to the commit the manifest names. ``rollback``
returns to the commit the newest successful core update replaced.
"""

from __future__ import annotations

import asyncio
import logging
import os
import pwd
from collections.abc import Callable

from baselith.core import _version
from baselith.core.cli.commands._update_preflight import render_preflight
from baselith.core.cli.commands._update_report import load_served_report
from baselith.core.config.plugin_update_apply import (
    UpdateApplyConfig,
    get_update_apply_config,
)
from baselith.core.config.plugin_updates import get_plugin_update_config
from baselith.core.plugin_updates._core_state import (
    default_fetcher,
    verified_core_state,
)
from baselith.core.plugin_updates.apply.drivers._fetch import DistributionFetcher
from baselith.core.plugin_updates.apply.drivers._git import (
    GitError,
    checkout_root,
    head_sha,
    subprocess_git,
)
from baselith.core.plugin_updates.apply.models import CORE_CLAIM, CORE_SUBJECT, RunKind
from baselith.core.plugin_updates.apply.store import RunConflict, RunStore
from baselith.core.plugin_updates.distribution import distribution_name
from baselith.core.plugin_updates.models import CheckReport, SystemUpdate
from baselith.core.plugin_updates.preflight import PreflightLevel
from baselith.core.plugin_updates.service import PluginUpdateService

logger = logging.getLogger(__name__)

Prompt = Callable[[str], str]


def operator() -> str:
    """The OS account running the command (not ``$USER``, which the caller sets)."""
    try:
        return f"cli:{pwd.getpwuid(os.getuid()).pw_name}"
    except KeyError:
        return f"cli:uid-{os.getuid()}"


def _audit(op: str, run_id: str, actor: str) -> None:
    logger.warning(
        "AUDIT | PLUGIN_UPDATE | %s run=%s plugin=%s by=%s",
        op,
        run_id,
        CORE_CLAIM,
        actor,
    )


def _confirmed(question: str, yes: bool, prompt: Prompt) -> bool:
    if yes:
        return True
    try:
        answer = prompt(f"{question} [y/N] ")
    except EOFError:
        return False
    return answer.strip().lower() in ("y", "yes")


def _core_notice(report: CheckReport | None) -> SystemUpdate | None:
    if report is None:
        return None
    return report.distribution if distribution_name() else report.system


def _create(
    store: RunStore,
    config: UpdateApplyConfig,
    *,
    kind: RunKind,
    to_version: str | None,
    commit: str,
) -> int:
    actor = operator()
    try:
        run = store.create(
            kind=kind,
            plugin=CORE_CLAIM,
            subject=CORE_SUBJECT,
            from_version=str(_version.__version__),
            to_version=to_version,
            tarball_sha256=commit,
            requested_by=actor,
            approval_required=False,
            approval_ttl_seconds=config.approval_ttl_seconds,
        )
    except RunConflict as exc:
        print(f"refused: run {exc.active_run_id} is active for the core")
        return 1
    _audit("request" if kind is RunKind.UPDATE else "rollback", run.id, actor)
    print(run.id)
    return 0


def run_apply_core(
    version: str,
    *,
    yes: bool,
    prompt: Prompt = input,
    fetcher: DistributionFetcher | None = None,
    store: RunStore | None = None,
) -> int:
    """Request a core install of ``version``; 0 when the run was created."""
    config = get_update_apply_config()
    notice = _core_notice(load_served_report())
    latest = notice.latest.version if notice is not None and notice.latest else None
    if latest != version:
        known = latest or "none"
        print(
            f"refused: {version} is not the latest known release ({known}); "
            "run `baselith update check` first"
        )
        return 1
    update_config = get_plugin_update_config()
    state = asyncio.run(
        verified_core_state(
            version, config=config, fetcher=fetcher or default_fetcher(update_config)
        )
    )
    service = PluginUpdateService(update_config)
    items = service.preflight(CORE_SUBJECT, version, core=state)
    print("\n".join(render_preflight(items)))
    if any(i.level is PreflightLevel.BLOCK for i in items) or state.release is None:
        print("refused: the pre-flight blocks this install")
        return 1
    commit = state.release.commit_sha
    if not _confirmed(f"Install {version} (commit {commit[:12]}) now?", yes, prompt):
        print("nothing requested")
        return 1
    runs = store or RunStore(config.state_dir)
    return _create(runs, config, kind=RunKind.UPDATE, to_version=version, commit=commit)


def run_rollback_core(
    *, yes: bool, prompt: Prompt = input, store: RunStore | None = None
) -> int:
    """Request a return to the commit the newest successful core update replaced."""
    config = get_update_apply_config()
    runs = store or RunStore(config.state_dir)
    last = runs.last_succeeded_update(CORE_CLAIM)
    if last is None or not last.previous_target:
        print("refused: the core has no successful one-click update to roll back")
        return 1
    try:
        live = head_sha(subprocess_git, checkout_root())
    except GitError:  # an unreadable checkout: let the updater refuse it
        live = None
    if live == last.previous_target:
        print("the core already runs its pre-update commit; nothing to roll back")
        return 0
    target = last.previous_target
    question = f"Roll the core back to {last.from_version} (commit {target[:12]})?"
    if not _confirmed(question, yes, prompt):
        print("nothing requested")
        return 1
    return _create(
        runs, config, kind=RunKind.ROLLBACK, to_version=last.from_version, commit=target
    )


__all__ = ["operator", "run_apply_core", "run_rollback_core"]
