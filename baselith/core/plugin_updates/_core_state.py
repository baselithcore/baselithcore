"""The live :class:`~._core_preflight.CoreState` of this host, and the verified release.

:func:`local_core_state` reads only the checkout (``uv.lock``, ``git
status``): what a read-only pre-flight may do. :func:`verified_core_state`
also downloads the release's ``distribution-release.json`` and verifies it
against the trust store, exactly as the updater will: the CLI's ``update apply
core`` and a console's install request call it before creating a run, and pin
the run to the commit it names.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

from baselith.core import _version
from baselith.core.config.plugin_update_apply import UpdateApplyConfig
from baselith.core.config.plugin_updates import PluginUpdateConfig

from ._core_preflight import CoreState
from .apply.drivers._core_guards import CoreRelease
from .apply.drivers._core_steps import StepFailure, host_state, verified_manifest
from .apply.drivers._fetch import DistributionFetcher, GitHubDistributionFetcher
from .apply.drivers._git import GitRunner, checkout_root, subprocess_git
from .distribution import distribution_name
from .distribution_manifest import PUBLIC_CORE_NAME, distribution_trust_scope
from .sources import GitHubReleaseSource


def release_name() -> str:
    """The name a core release is signed for: the distribution's, else the public core's."""
    return distribution_name() or PUBLIC_CORE_NAME


def distribution_keys(name: str) -> list[str]:
    """The usable keys that may sign ``name``'s releases (blocking).

    Read from the trust store under the scope ``distribution:<name>``, so a
    key scoped to plugins (even one called like the distribution) never
    covers a core release; an unscoped key covers everything, as for plugins.
    """
    from baselith.core.plugins.signing import load_trust_roots

    return load_trust_roots(distribution_trust_scope(name))


def release_repo(update_config: PluginUpdateConfig) -> str:
    """``owner/repo`` whose releases carry the core's signed manifest."""
    repo = (
        update_config.distribution_update_repo
        if distribution_name()
        else update_config.core_update_repo
    )
    return repo.strip()


def default_fetcher(update_config: PluginUpdateConfig) -> DistributionFetcher:
    """The GitHub fetcher of :func:`release_repo`, capped like every metadata asset."""
    source = GitHubReleaseSource(
        update_config.github_api_url, update_config.github_token
    )
    return GitHubDistributionFetcher(source, release_repo(update_config))


def local_core_state(
    config: UpdateApplyConfig,
    *,
    git: GitRunner = subprocess_git,
    root: Path | None = None,
) -> CoreState:
    """The checkout's state; the release is left unchecked (no network)."""
    lock, dirty = host_state(git, root or checkout_root(), config.ignored_paths)
    return CoreState(
        installed_version=str(_version.__version__),
        installed_lock_sha256=lock,
        tree_dirty=dirty,
        release_checked=False,
    )


async def verified_core_release(
    version: str,
    *,
    fetcher: DistributionFetcher,
    keys: list[str] | None = None,
    name: str | None = None,
) -> tuple[CoreRelease | None, str]:
    """``(release, "")`` for a verified manifest of ``version``, else ``(None, why)``."""
    signed_for = name or release_name()
    if keys is None:
        keys = await asyncio.to_thread(distribution_keys, signed_for)
    found = await verified_manifest(
        fetcher, keys, name=signed_for, version=version, pinned_commit=None
    )
    if isinstance(found, StepFailure):
        detail = f"{found.code}: {found.detail}" if found.detail else found.code
        return None, detail
    return CoreRelease.of(found), ""


async def verified_core_state(
    version: str,
    *,
    config: UpdateApplyConfig,
    fetcher: DistributionFetcher,
    keys: list[str] | None = None,
    git: GitRunner = subprocess_git,
    root: Path | None = None,
) -> CoreState:
    """The checkout's state plus the verified release of ``version`` (or why not)."""
    state = await asyncio.to_thread(local_core_state, config, git=git, root=root)
    release, detail = await verified_core_release(version, fetcher=fetcher, keys=keys)
    return state.model_copy(
        update={"release": release, "release_detail": detail, "release_checked": True}
    )


__all__ = [
    "default_fetcher",
    "distribution_keys",
    "local_core_state",
    "release_name",
    "release_repo",
    "verified_core_release",
    "verified_core_state",
]
