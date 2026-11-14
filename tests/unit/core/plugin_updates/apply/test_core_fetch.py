"""GitHubDistributionFetcher: the signed manifest of release v<version>."""

from __future__ import annotations

from typing import Any

import httpx
import pytest
from pydantic import SecretStr

from baselith.core.plugin_updates.apply.drivers._fetch import GitHubDistributionFetcher
from baselith.core.plugin_updates.sources import (
    MAX_TEXT_FILE_BYTES,
    GitHubReleaseSource,
    SourceError,
)

API = "https://api.github.com"
ASSET = f"{API}/repos/acme/dist/releases/assets/9"


def _release(tag: str, *, asset: bool = True) -> dict[str, Any]:
    assets = [{"name": "distribution-release.json", "url": ASSET}] if asset else []
    return {
        "tag_name": tag,
        "draft": False,
        "prerelease": False,
        "published_at": "2026-11-14T10:00:00Z",
        "assets": [{"name": "notes.txt", "url": f"{API}/x"}, *assets],
    }


def _fetcher(handler) -> GitHubDistributionFetcher:  # type: ignore[no-untyped-def]
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    source = GitHubReleaseSource(API, SecretStr("tok"), client=client)
    return GitHubDistributionFetcher(source, "acme/dist")


async def test_manifest_downloaded_by_tag() -> None:
    seen: list[str] = []

    def handler(req: httpx.Request) -> httpx.Response:
        seen.append(req.url.path)
        if req.url.path == "/repos/acme/dist/releases/tags/v1.15.0":
            return httpx.Response(200, json=_release("v1.15.0"))
        if str(req.url) == ASSET:
            assert req.headers["accept"] == "application/octet-stream"
            return httpx.Response(200, content=b'{"kind": "distribution"}')
        return httpx.Response(404)

    raw = await _fetcher(handler).manifest("1.15.0")
    assert raw == b'{"kind": "distribution"}'
    assert seen == [
        "/repos/acme/dist/releases/tags/v1.15.0",
        "/repos/acme/dist/releases/assets/9",
    ]


async def test_oversized_manifest_refused() -> None:
    def handler(req: httpx.Request) -> httpx.Response:
        if req.url.path.endswith("/tags/v1.15.0"):
            return httpx.Response(200, json=_release("v1.15.0"))
        return httpx.Response(200, content=b"x" * (MAX_TEXT_FILE_BYTES + 1))

    with pytest.raises(SourceError, match="larger than"):
        await _fetcher(handler).manifest("1.15.0")


async def test_missing_asset_is_a_clear_error() -> None:
    def handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=_release("v1.15.0", asset=False))

    with pytest.raises(SourceError, match="no distribution-release.json asset"):
        await _fetcher(handler).manifest("1.15.0")


async def test_missing_release_is_a_clear_error() -> None:
    with pytest.raises(SourceError, match="release v1.15.0 not found"):
        await _fetcher(lambda req: httpx.Response(404)).manifest("1.15.0")


async def test_no_repository_configured() -> None:
    source = GitHubReleaseSource(API, None)
    with pytest.raises(SourceError, match="no distribution repository"):
        await GitHubDistributionFetcher(source, "").manifest("1.15.0")
