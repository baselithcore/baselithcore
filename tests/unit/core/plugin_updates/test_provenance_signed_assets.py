"""Provenance mode: a notice stays a notice; signed assets make it installable."""

from __future__ import annotations

import json
from pathlib import Path

import httpx

from core.plugin_updates.cache import UpdateCache
from core.plugin_updates.checker import run_check
from core.plugin_updates.models import Refusal
from core.plugins.signing import generate_keypair_hex

from .provenance_fake import FakeGitHub, _release, _source
from .test_checker import _signed_tarball


def _with_assets(fake: FakeGitHub, tgz: Path, meta: dict) -> FakeGitHub:
    rel = _release()
    rel["assets"] = [
        {
            "name": "demo-1.2.0.tar.gz",
            "url": "https://api.github.com/repos/o/r/releases/assets/1",
            "size": tgz.stat().st_size,
        },
        {
            "name": "release.json",
            "url": "https://api.github.com/repos/o/r/releases/assets/2",
            "size": 10,
        },
    ]
    fake.releases = [rel]
    blobs = {
        "/repos/o/r/releases/assets/1": tgz.read_bytes(),
        "/repos/o/r/releases/assets/2": json.dumps(meta).encode(),
    }
    inner = fake.__call__

    def serve(req: httpx.Request) -> httpx.Response:
        if req.url.path in blobs:
            return httpx.Response(200, content=blobs[req.url.path])
        return inner(req)

    fake.__call__ = serve  # type: ignore[method-assign]
    return fake


async def _run(tmp_path: Path, fake: FakeGitHub, keys: list[str]):
    source = _source(fake)
    source._client = httpx.AsyncClient(
        transport=httpx.MockTransport(lambda r: fake.__call__(r))
    )
    report = await run_check(
        {"demo": "o/r"},
        {"demo": "1.0.0"},
        source=source,
        cache=UpdateCache(tmp_path / "c"),
        core_version="1.50.0",
        trusted_keys=keys,
        trust="provenance",
    )
    return report.candidates[0]


async def test_provenance_only_release_is_a_notice_not_installable(
    tmp_path: Path,
) -> None:
    cand = await _run(tmp_path, FakeGitHub(), [])
    assert cand.available and cand.trust == "provenance"
    assert cand.signed_assets is not None and not cand.signed_assets.verified
    assert cand.signed_assets.refusal is Refusal.ARTIFACT_MISSING


async def test_signed_assets_on_a_provenance_release_verify_and_cache(
    tmp_path: Path,
) -> None:
    priv, pub = generate_keypair_hex()
    tgz, meta = _signed_tarball(tmp_path, priv)
    cand = await _run(tmp_path, _with_assets(FakeGitHub(), tgz, meta), [pub])
    assert cand.available and cand.signed_assets and cand.signed_assets.verified
    assert UpdateCache(tmp_path / "c").tarball_path("demo", "1.2.0").is_file()


async def test_bad_signature_keeps_the_notice(tmp_path: Path) -> None:
    priv, _ = generate_keypair_hex()
    _, stranger = generate_keypair_hex()
    tgz, meta = _signed_tarball(tmp_path, priv)
    cand = await _run(tmp_path, _with_assets(FakeGitHub(), tgz, meta), [stranger])
    assert cand.available  # the notice survives
    assert cand.signed_assets
    assert cand.signed_assets.refusal is Refusal.SIGNATURE_INVALID
