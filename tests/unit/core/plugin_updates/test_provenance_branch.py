"""Provenance trust: only the default branch's code, released by the Actions bot.

A workflow token on ANY branch can create a bot-authored release, and the
release's ``target_commitish`` is whatever its creator said. These tests pin
that a release is offered only when its tagged commit is on the repository's
default branch, and only when the author is the real ``github-actions[bot]``
account (login, type and — on github.com — id).
"""

from __future__ import annotations

import pytest

from core.plugin_updates.models import Refusal
from core.plugin_updates.provenance import RELEASE_WORKFLOW_AUTHOR

from .provenance_fake import OTHER, SHA, FakeGitHub, _check, _manifest, _release

SIDE = "c" * 40


@pytest.mark.parametrize("status", ["ahead", "identical"])
async def test_a_commit_on_the_default_branch_is_offered(status: str) -> None:
    fake = FakeGitHub(compare={SHA: status})
    assert (await _check(fake)).available
    compares = [r for r in fake.requests if "/compare/" in r.url.path]
    # The tagged commit is compared with the branch the repository reports.
    assert [r.url.path for r in compares] == [f"/repos/o/r/compare/{SHA}...main"]


@pytest.mark.parametrize("status", ["behind", "diverged"])
async def test_a_side_branch_commit_is_refused(status: str) -> None:
    """A modified workflow on a side branch released that branch's commit."""
    fake = FakeGitHub(
        releases=[_release(target="feature/evil")],
        tags={"v1.2.0": {"type": "commit", "sha": SIDE}},
        files={("manifest.yaml", SIDE): _manifest().encode()},
        compare={SIDE: status},
    )
    cand = await _check(fake)
    assert not cand.available
    assert cand.refusal is Refusal.NOT_ON_DEFAULT_BRANCH
    assert SIDE[:12] in cand.detail and "main" in cand.detail
    # Refused before the manifest at that commit is read.
    assert not any("/contents/" in r.url.path for r in fake.requests)


async def test_a_release_retargeted_to_a_side_commit_is_refused() -> None:
    """Tag and target_commitish both moved to a commit main never merged."""
    fake = FakeGitHub(
        releases=[_release(target=OTHER)],
        tags={"v1.2.0": {"type": "commit", "sha": OTHER}},
        files={("manifest.yaml", OTHER): _manifest().encode()},
        compare={OTHER: "diverged"},
    )
    cand = await _check(fake)
    assert cand.refusal is Refusal.NOT_ON_DEFAULT_BRANCH
    assert cand.provenance is not None and cand.provenance.commit_sha == OTHER


async def test_the_default_branch_is_read_from_the_repository() -> None:
    fake = FakeGitHub(default_branch="trunk", compare={SHA: "ahead"})
    assert (await _check(fake)).available
    assert any(r.url.path.endswith(f"{SHA}...trunk") for r in fake.requests)


async def test_an_unknown_comparison_is_a_source_error() -> None:
    cand = await _check(FakeGitHub(compare={}))
    assert cand.refusal is Refusal.SOURCE_ERROR and "404" in cand.detail


@pytest.mark.parametrize("branch", ["", "a..b", "bad branch"])
async def test_an_unusable_default_branch_is_a_source_error(branch: str) -> None:
    fake = FakeGitHub(default_branch=branch)
    cand = await _check(fake)
    assert cand.refusal is Refusal.SOURCE_ERROR
    assert not any("/compare/" in r.url.path for r in fake.requests)


@pytest.mark.parametrize(
    ("author_id", "author_type"),
    [(1, "Bot"), (41898282, "User"), (41898282, "Organization")],
)
async def test_the_bot_login_alone_is_not_enough(
    author_id: int, author_type: str
) -> None:
    fake = FakeGitHub(releases=[_release(author_id=author_id, author_type=author_type)])
    cand = await _check(fake)
    assert cand.refusal is Refusal.UNTRUSTED_RELEASE_AUTHOR
    assert RELEASE_WORKFLOW_AUTHOR in cand.detail and author_type in cand.detail
    assert [r.url.path for r in fake.requests] == ["/repos/o/r/releases"]


async def test_the_bot_id_is_pinned_only_on_github_com() -> None:
    """A GitHub Enterprise Server gives the Actions bot an id of its own."""
    fake = FakeGitHub(releases=[_release(author_id=7)])
    cand = await _check(fake, api="https://ghe.example.com/api/v3")
    assert cand.available
    typed = FakeGitHub(releases=[_release(author_id=7, author_type="User")])
    refused = await _check(typed, api="https://ghe.example.com/api/v3")
    assert refused.refusal is Refusal.UNTRUSTED_RELEASE_AUTHOR


async def test_an_older_release_than_installed_never_reaches_github_compare() -> None:
    fake = FakeGitHub(compare={})
    cand = await _check(fake, installed="1.2.0")
    assert not cand.available and cand.refusal is None
    assert not any("/compare/" in r.url.path for r in fake.requests)
