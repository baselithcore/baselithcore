"""Provenance trust: a plugin release is genuine when its own repository made it.

The default trust mode (``PLUGIN_UPDATE_TRUST=provenance``). Every plugin
repository carries a release workflow that, on each push to its default
branch, tags the pushed commit ``v<version>`` from the plugin manifest and
creates the GitHub Release with the repository's own ``GITHUB_TOKEN``. No
signing key exists anywhere, so none can leak.

A deployment then offers release ``v<X.Y.Z>`` only when:

* it is the latest stable (non-draft, non-prerelease) ``v<semver>`` release
  and is newer than the installed version;
* it was created by a workflow token (``author.login`` is
  :data:`RELEASE_WORKFLOW_AUTHOR`, ``author.type`` is ``Bot`` and, on
  github.com, ``author.id`` is :data:`RELEASE_WORKFLOW_AUTHOR_ID`) — a
  release a person created by hand is refused;
* its tag still points at the commit the release was created from, when the
  release names one;
* that commit is on the repository's default branch (an ancestor of, or equal
  to, its head). Any workflow run with ``contents: write`` — one on a side
  branch included — can create a bot-authored release, and the release's own
  ``target_commitish`` is whatever its creator said; only the default branch
  is what the mirror's reviewers actually merged;
* the plugin manifest *at that commit* declares the same ``name`` and
  ``version``, a stable version, and core bounds that admit the running
  framework (the same rule the plugin loader applies).

What this proves, and no more: the release is the default branch's code at
that version. Anyone who can write to that branch — the mirror's external
developers included — can therefore cause a notice, possibly for code the
monorepo has not gated yet. Nothing is downloaded: this mode is a notice. Installing a release stays a
manual step of the deployment's own procedure (a rebuilt image, a package
upgrade), which is also why the plugin's Python dependencies are not judged
against the running environment here.
"""

from __future__ import annotations

import logging

import yaml

from core.plugins.integrity import MANIFEST_FILENAMES
from core.plugins.version import SemanticVersion, check_plugin_compatibility

from .models import Refusal, ReleaseInfo, ReleaseProvenance, UpdateCandidate
from .sources import GitHubReleaseSource, SourceError, is_commit_sha

logger = logging.getLogger(__name__)

#: The login GitHub gives a release created with a workflow's ``GITHUB_TOKEN``.
RELEASE_WORKFLOW_AUTHOR = "github-actions[bot]"
#: Its account type.
RELEASE_WORKFLOW_AUTHOR_TYPE = "Bot"
#: Its account id on github.com. A GitHub Enterprise Server instance gives
#: the same bot an id of its own, so the id is pinned only on github.com.
RELEASE_WORKFLOW_AUTHOR_ID = 41898282
_GITHUB_COM_API = "https://api.github.com"
#: Compare statuses under which the release commit is on the default branch.
_ON_BRANCH = frozenset({"ahead", "identical"})


def _is_newer(latest: str, installed: str | None) -> bool:
    if installed is None:
        return True
    try:
        return SemanticVersion(latest) > SemanticVersion(installed)
    except ValueError:
        return True


def _candidate(
    plugin: str,
    installed: str | None,
    latest: ReleaseInfo | None,
    provenance: ReleaseProvenance | None,
    *,
    available: bool = False,
    refusal: Refusal | None = None,
    detail: str = "",
) -> UpdateCandidate:
    return UpdateCandidate(
        plugin=plugin,
        installed_version=installed,
        latest=latest,
        available=available,
        refusal=refusal,
        detail=detail,
        trust="provenance",
        provenance=provenance,
    )


def manifest_refusal(
    text: str, *, plugin: str, version: str, core_version: str
) -> tuple[Refusal, str] | None:
    """Judge the manifest published at the release commit; None when it agrees.

    Args:
        text: The manifest file's text (YAML or JSON).
        plugin: The plugin the release is for.
        version: The release's version (from its tag).
        core_version: The version plugin manifests declare bounds against:
            the public core release (``core._core_version.CORE_VERSION``), as
            the loader and the signed path use.
    """
    try:
        data = yaml.safe_load(text)
    except yaml.YAMLError:
        return Refusal.MANIFEST_INVALID, "manifest does not parse"
    if not isinstance(data, dict):
        return Refusal.MANIFEST_INVALID, "manifest is not a mapping"
    if data.get("name") != plugin:
        return Refusal.NAME_MISMATCH, f"manifest name {str(data.get('name'))[:80]!r}"
    declared = str(data.get("version", ""))
    if declared != version:
        return Refusal.VERSION_MISMATCH, f"manifest version {declared[:40]}"
    try:
        parsed = SemanticVersion(declared)
    except ValueError:
        return Refusal.MANIFEST_INVALID, f"invalid version {declared[:40]!r}"
    if parsed.prerelease:
        return Refusal.MANIFEST_INVALID, "prerelease"
    low, high = data.get("min_core_version"), data.get("max_core_version")
    for bound in (low, high):
        if bound is not None and not isinstance(bound, str):
            return Refusal.MANIFEST_INVALID, "core version bound must be a string"
    problems = check_plugin_compatibility(
        core_version=core_version, min_core_version=low, max_core_version=high
    )
    if problems:
        return Refusal.INCOMPATIBLE_CORE, "; ".join(problems)
    return None


def _trusted_author(latest: ReleaseInfo, source: GitHubReleaseSource) -> bool:
    """Whether the release was created by the Actions workflow token."""
    if latest.author != RELEASE_WORKFLOW_AUTHOR:
        return False
    if latest.author_type != RELEASE_WORKFLOW_AUTHOR_TYPE:
        return False
    if source.api_url == _GITHUB_COM_API:
        return latest.author_id == RELEASE_WORKFLOW_AUTHOR_ID
    return True


async def _branch_refusal(
    source: GitHubReleaseSource, slug: str, sha: str
) -> str | None:
    """Why ``sha`` is not on the default branch; None when it is."""
    branch = await source.default_branch(slug)
    status = await source.compare_status(slug, sha, branch)
    if status in _ON_BRANCH:
        return None
    return f"commit {sha[:12]} is not on {branch[:80]} ({status[:20]})"


async def _manifest_text(
    source: GitHubReleaseSource, slug: str, sha: str
) -> str | None:
    """The first manifest present at ``sha``, in the loader's lookup order."""
    for name in MANIFEST_FILENAMES:
        text = await source.file_at(slug, name, sha)
        if text is not None:
            return text
    return None


async def _check(
    plugin: str,
    slug: str,
    installed: str | None,
    source: GitHubReleaseSource,
    core_version: str,
) -> UpdateCandidate:
    latest = await source.latest_release(plugin, slug)
    if latest is None:
        return _candidate(plugin, installed, None, None)
    provenance = ReleaseProvenance(
        author=latest.author, published_at=latest.published_at
    )
    if not _is_newer(latest.version, installed):
        return _candidate(plugin, installed, latest, provenance)
    if not _trusted_author(latest, source):
        shown = (latest.author or "unknown")[:80]
        if latest.author == RELEASE_WORKFLOW_AUTHOR:
            kind = (latest.author_type or "unknown")[:20]
            shown = f"{shown} (type {kind}, id {latest.author_id})"
        return _candidate(
            plugin,
            installed,
            latest,
            provenance,
            refusal=Refusal.UNTRUSTED_RELEASE_AUTHOR,
            detail=f"created by {shown}",
        )
    sha = await source.tag_commit(slug, latest.tag)
    provenance = provenance.model_copy(
        update={"commit_sha": sha, "commit_url": source.commit_url(slug, sha)}
    )
    target = latest.target_commitish
    if is_commit_sha(target) and (target or "").lower() != sha:
        return _candidate(
            plugin,
            installed,
            latest,
            provenance,
            refusal=Refusal.TAG_MOVED,
            detail=f"released from {(target or '')[:12]}, tag at {sha[:12]}",
        )
    off_branch = await _branch_refusal(source, slug, sha)
    if off_branch is not None:
        return _candidate(
            plugin,
            installed,
            latest,
            provenance,
            refusal=Refusal.NOT_ON_DEFAULT_BRANCH,
            detail=off_branch,
        )
    text = await _manifest_text(source, slug, sha)
    if text is None:
        return _candidate(
            plugin,
            installed,
            latest,
            provenance,
            refusal=Refusal.MANIFEST_INVALID,
            detail="no manifest at the release commit",
        )
    refused = manifest_refusal(
        text, plugin=plugin, version=latest.version, core_version=core_version
    )
    if refused is not None:
        return _candidate(
            plugin,
            installed,
            latest,
            provenance,
            refusal=refused[0],
            detail=refused[1],
        )
    return _candidate(plugin, installed, latest, provenance, available=True)


async def check_plugin_provenance(
    plugin: str,
    slug: str,
    installed: str | None,
    *,
    source: GitHubReleaseSource,
    core_version: str,
) -> UpdateCandidate:
    """Check one plugin under provenance trust; never raises.

    A failure to reach GitHub becomes a ``source_error`` refusal, any other
    failure one naming only the exception type.
    """
    try:
        return await _check(plugin, slug, installed, source, core_version)
    except SourceError as exc:
        return _candidate(
            plugin, installed, None, None, refusal=Refusal.SOURCE_ERROR, detail=str(exc)
        )
    except Exception as exc:  # contain everything; the type name carries no secret
        logger.warning("plugin_update_check_failed: %s: %s", plugin, type(exc).__name__)
        return _candidate(
            plugin,
            installed,
            None,
            None,
            refusal=Refusal.SOURCE_ERROR,
            detail=f"unexpected {type(exc).__name__}",
        )


__all__ = [
    "RELEASE_WORKFLOW_AUTHOR",
    "RELEASE_WORKFLOW_AUTHOR_ID",
    "RELEASE_WORKFLOW_AUTHOR_TYPE",
    "check_plugin_provenance",
    "manifest_refusal",
]
