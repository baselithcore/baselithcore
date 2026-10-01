"""Provenance trust: a release is offered when its own repository's workflow made it.

Every test runs against a fake GitHub API (``provenance_fake.FakeGitHub``).
"""

from __future__ import annotations

from typing import Any

import pytest

from core.plugin_updates.checker import run_check
from core.plugin_updates.models import Refusal
from core.plugin_updates.provenance import RELEASE_WORKFLOW_AUTHOR

from .provenance_fake import (
    OTHER,
    SHA,
    FakeGitHub,
    _check,
    _manifest,
    _release,
    _source,
)


async def test_workflow_release_with_agreeing_manifest_is_available() -> None:
    fake = FakeGitHub()
    cand = await _check(fake)
    assert cand.available and cand.refusal is None and cand.trust == "provenance"
    assert cand.latest is not None and cand.latest.version == "1.2.0"
    prov = cand.provenance
    assert prov is not None and prov.author == RELEASE_WORKFLOW_AUTHOR
    assert prov.commit_sha == SHA
    assert prov.commit_url == f"https://github.com/o/r/commit/{SHA}"
    assert prov.published_at is not None and prov.published_at.year == 2026
    # The manifest is read at the resolved commit, raw, and nothing is downloaded.
    contents = [r for r in fake.requests if "/contents/" in r.url.path]
    assert contents[0].url.params["ref"] == SHA
    assert contents[0].headers["accept"] == "application/vnd.github.raw+json"
    assert all(r.url.host == "api.github.com" for r in fake.requests)
    assert all(r.headers["authorization"] == "Bearer tok" for r in fake.requests)
    assert not any("/assets/" in r.url.path for r in fake.requests)


async def test_a_hand_made_release_is_refused() -> None:
    fake = FakeGitHub(releases=[_release(author="mallory")])
    cand = await _check(fake)
    assert not cand.available
    assert cand.refusal is Refusal.UNTRUSTED_RELEASE_AUTHOR
    assert "mallory" in cand.detail
    # Refused before the tag or the manifest is even looked at.
    assert [r.url.path for r in fake.requests] == ["/repos/o/r/releases"]


async def test_a_release_without_an_author_is_refused() -> None:
    cand = await _check(FakeGitHub(releases=[_release(author=None)]))
    assert cand.refusal is Refusal.UNTRUSTED_RELEASE_AUTHOR


async def test_a_moved_tag_is_refused() -> None:
    fake = FakeGitHub(tags={"v1.2.0": {"type": "commit", "sha": OTHER}})
    cand = await _check(fake)
    assert cand.refusal is Refusal.TAG_MOVED
    assert cand.provenance is not None and cand.provenance.commit_sha == OTHER


async def test_a_branch_target_is_not_a_moved_tag() -> None:
    fake = FakeGitHub(releases=[_release(target="main")])
    assert (await _check(fake)).available


async def test_an_annotated_tag_is_peeled() -> None:
    fake = FakeGitHub(
        tags={"v1.2.0": {"type": "tag", "sha": "c" * 40}},
        annotated={"c" * 40: {"type": "commit", "sha": SHA}},
    )
    assert (await _check(fake)).available


@pytest.mark.parametrize(
    ("manifest", "refusal"),
    [
        (_manifest(name="other"), Refusal.NAME_MISMATCH),
        (_manifest(version="1.1.0"), Refusal.VERSION_MISMATCH),
        (_manifest(extra="min_core_version: '9.0.0'\n"), Refusal.INCOMPATIBLE_CORE),
        (_manifest(extra="max_core_version: 2\n"), Refusal.MANIFEST_INVALID),
        ("- not\n- a mapping\n", Refusal.MANIFEST_INVALID),
        ("name: [unclosed\n", Refusal.MANIFEST_INVALID),
    ],
)
async def test_the_manifest_at_the_tag_must_agree(
    manifest: str, refusal: Refusal
) -> None:
    fake = FakeGitHub(files={("manifest.yaml", SHA): manifest.encode()})
    cand = await _check(fake)
    assert not cand.available and cand.refusal is refusal


async def test_a_prerelease_manifest_version_is_refused() -> None:
    fake = FakeGitHub(
        releases=[_release("v1.2.0")],
        files={("manifest.yaml", SHA): _manifest(version="1.2.0-rc1").encode()},
    )
    assert (await _check(fake)).refusal is Refusal.VERSION_MISMATCH


async def test_the_loader_lookup_order_finds_a_json_manifest() -> None:
    body = b'{"name": "demo", "version": "1.2.0"}'
    fake = FakeGitHub(files={("manifest.json", SHA): body})
    assert (await _check(fake)).available


async def test_no_manifest_at_the_commit_is_refused() -> None:
    cand = await _check(FakeGitHub(files={}))
    assert cand.refusal is Refusal.MANIFEST_INVALID
    assert "no manifest" in cand.detail


async def test_not_newer_is_quietly_up_to_date() -> None:
    fake = FakeGitHub()
    cand = await _check(fake, installed="1.2.0")
    assert not cand.available and cand.refusal is None
    assert [r.url.path for r in fake.requests] == ["/repos/o/r/releases"]


async def test_drafts_and_prereleases_are_never_candidates() -> None:
    fake = FakeGitHub(
        releases=[_release("v2.0.0", draft=True), _release("v1.9.0", pre=True)]
    )
    cand = await _check(fake)
    assert cand.latest is None and not cand.available and cand.refusal is None


async def test_github_unreachable_is_a_source_error() -> None:
    cand = await _check(FakeGitHub(releases_status=502))
    assert cand.refusal is Refusal.SOURCE_ERROR and "502" in cand.detail
    assert "tok" not in cand.detail


async def test_an_oversized_manifest_is_a_source_error() -> None:
    big = _manifest().encode() + b"#" * (1024 * 1024)
    cand = await _check(FakeGitHub(files={("manifest.yaml", SHA): big}))
    assert cand.refusal is Refusal.SOURCE_ERROR


async def test_a_missing_tag_is_a_source_error() -> None:
    cand = await _check(FakeGitHub(tags={}))
    assert cand.refusal is Refusal.SOURCE_ERROR and "404" in cand.detail


@pytest.mark.parametrize(
    ("api", "expected"),
    [
        ("https://ghe.example.com/api/v3", f"https://ghe.example.com/o/r/commit/{SHA}"),
        ("http://127.0.0.1:9000", None),
        ("https://proxy.example.com", None),
    ],
)
async def test_commit_link_only_on_a_known_web_host(
    api: str, expected: str | None
) -> None:
    cand = await _check(FakeGitHub(), api=api)
    assert cand.available and cand.provenance is not None
    assert cand.provenance.commit_url == expected


async def test_run_check_selects_the_trust_mode(tmp_path: Any) -> None:
    from core.plugin_updates.cache import UpdateCache

    fake = FakeGitHub()
    report = await run_check(
        {"demo": "o/r"},
        {"demo": "1.0.0"},
        source=_source(fake),
        cache=UpdateCache(tmp_path),
        core_version="1.50.0",
        trusted_keys=[],
        trust="provenance",
    )
    [cand] = report.candidates
    assert cand.available and cand.trust == "provenance"

    signed = await run_check(
        {"demo": "o/r"},
        {"demo": "1.0.0"},
        source=_source(FakeGitHub()),
        cache=UpdateCache(tmp_path),
        core_version="1.50.0",
        trusted_keys=[],
    )
    [cand] = signed.candidates
    # The signed mode wants release.json + tarball, which this release lacks.
    assert not cand.available and cand.trust == "signed"
    assert cand.refusal is Refusal.ARTIFACT_MISSING
    assert cand.provenance is not None and cand.provenance.commit_sha == SHA
