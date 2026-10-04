from __future__ import annotations

from pathlib import Path
from typing import Any, Callable

import httpx
import pytest
from pydantic import SecretStr

from core.config.plugin_updates import PluginUpdateConfig
from core.plugin_updates.sources import GitHubReleaseSource, SourceError, load_sources


def _rel(
    tag: str, *, draft: bool = False, pre: bool = False, assets: bool = True
) -> dict[str, Any]:
    v = tag.removeprefix("v")
    return {
        "tag_name": tag,
        "draft": draft,
        "prerelease": pre,
        "body": f"notes {tag}",
        "html_url": f"https://github.com/o/r/releases/tag/{tag}",
        "published_at": "2026-09-29T10:00:00Z",
        "assets": [
            {"name": f"demo-{v}.tar.gz", "url": f"https://api.github.com/a/{v}/tgz"},
            {"name": "release.json", "url": f"https://api.github.com/a/{v}/json"},
        ]
        if assets
        else [],
    }


def _source(
    handler: Callable[[httpx.Request], httpx.Response],
) -> GitHubReleaseSource:
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    return GitHubReleaseSource(
        "https://api.github.com", SecretStr("tok"), client=client
    )


async def test_latest_release_skips_drafts_prereleases_and_bad_tags() -> None:
    seen: dict[str, str] = {}

    def handler(req: httpx.Request) -> httpx.Response:
        seen["auth"] = req.headers["authorization"]
        return httpx.Response(
            200,
            json=[
                _rel("v2.0.0", draft=True),
                _rel("v1.9.0-rc1", pre=True),
                _rel("v3.0.0-beta.1"),
                _rel("nightly"),
                _rel("v1.3.0"),
                _rel("v1.10.0"),
            ],
        )

    info = await _source(handler).latest_release("demo", "o/r")
    assert info is not None and info.version == "1.10.0" and info.tag == "v1.10.0"
    assert info.tarball_url == "https://api.github.com/a/1.10.0/tgz"
    assert info.release_json_url == "https://api.github.com/a/1.10.0/json"
    assert seen["auth"] == "Bearer tok"


async def test_missing_assets_is_refusal() -> None:
    info = await _source(
        lambda r: httpx.Response(200, json=[_rel("v1.3.0", assets=False)])
    ).latest_release("demo", "o/r")
    assert info is not None and info.tarball_url is None


async def test_no_releases_returns_none() -> None:
    src = _source(lambda r: httpx.Response(200, json=[]))
    assert await src.latest_release("demo", "o/r") is None


async def test_http_error_raises_without_token() -> None:
    with pytest.raises(SourceError) as exc:
        await _source(lambda r: httpx.Response(401, json={})).latest_release(
            "demo", "o/r"
        )
    assert "401" in str(exc.value) and "tok" not in str(exc.value)


async def test_download_streams(tmp_path: Path) -> None:
    def handler(req: httpx.Request) -> httpx.Response:
        assert req.headers["accept"] == "application/octet-stream"
        return httpx.Response(200, content=b"payload")

    dest = tmp_path / "x.tgz"
    await _source(handler).download("https://api.github.com/a/1/tgz", dest)
    assert dest.read_bytes() == b"payload"


async def test_download_error_raises(tmp_path: Path) -> None:
    with pytest.raises(SourceError) as exc:
        await _source(lambda r: httpx.Response(404)).download(
            "https://api.github.com/a/1/tgz", tmp_path / "x"
        )
    assert "404" in str(exc.value) and not (tmp_path / "x").exists()


def test_load_sources_skips_subtrees(tmp_path: Path) -> None:
    f = tmp_path / "m.yaml"
    f.write_text(
        "mirrors:\n  example:\n    repo: git@github.com:o/plugin-example.git\n"
        "  example_daemon:\n    repo: git@github.com:o/d.git\n"
        "    path: plugins/example/daemon\n"
        "  broken:\n    repo: not-a-url\n"
    )
    assert load_sources(f) == {"example": "o/plugin-example"}


def test_config_enabled_needs_existing_sources_file(tmp_path: Path) -> None:
    off = {"core_update_repo": ""}
    assert not PluginUpdateConfig(sources_file=None, **off).enabled
    assert not PluginUpdateConfig(sources_file=tmp_path / "nope.yaml", **off).enabled
    f = tmp_path / "m.yaml"
    f.write_text("mirrors: {}\n")
    assert PluginUpdateConfig(sources_file=f, **off).enabled


def test_config_interval_floor() -> None:
    with pytest.raises(ValueError):
        PluginUpdateConfig(check_interval_seconds=10)


async def test_download_refuses_foreign_host_without_request(tmp_path: Path) -> None:
    calls: list[httpx.Request] = []

    def handler(req: httpx.Request) -> httpx.Response:
        calls.append(req)
        return httpx.Response(200, content=b"x")

    src = _source(handler)
    for url in ("https://evil.example/a", "http://api.github.com/a", "not a url"):
        with pytest.raises(SourceError) as exc:
            await src.download(url, tmp_path / "x")
        assert "tok" not in str(exc.value)
    assert calls == []


async def test_download_redirect_to_other_host_drops_authorization(
    tmp_path: Path,
) -> None:
    seen: list[tuple[str, str | None]] = []

    def handler(req: httpx.Request) -> httpx.Response:
        seen.append((req.url.host, req.headers.get("authorization")))
        if req.url.host == "api.github.com":
            return httpx.Response(302, headers={"location": "https://cdn.example/f"})
        return httpx.Response(200, content=b"data")

    dest = tmp_path / "x"
    await _source(handler).download("https://api.github.com/a/1/tgz", dest)
    assert seen == [("api.github.com", "Bearer tok"), ("cdn.example", None)]
    assert dest.read_bytes() == b"data"


@pytest.mark.parametrize(
    "response",
    [
        httpx.Response(200, content=b"not json"),
        httpx.Response(200, json={"message": "nope"}),
        httpx.Response(200, json="text"),
    ],
)
async def test_malformed_body_raises_source_error(response: httpx.Response) -> None:
    with pytest.raises(SourceError) as exc:
        await _source(lambda r: response).latest_release("demo", "o/r")
    assert "tok" not in str(exc.value)


async def test_malformed_entries_are_skipped() -> None:
    bad_date = _rel("v9.0.0")
    bad_date["published_at"] = "yesterday-ish"
    bad_types = _rel("v8.0.0")
    bad_types["assets"] = [None, {"name": 3, "url": []}, "x"]
    bad_types["body"] = 5
    bad_tag = _rel("v7.0.0")
    bad_tag["tag_name"] = 7
    body = ["junk", None, 4, bad_date, bad_tag, bad_types, _rel("v1.2.0")]
    info = await _source(lambda r: httpx.Response(200, json=body)).latest_release(
        "demo", "o/r"
    )
    assert info is not None and info.version == "8.0.0"
    assert info.tarball_url is None and info.notes == ""


async def test_list_releases_newest_first_semver_only() -> None:
    def handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json=[
                _rel("v1.0.0"),
                _rel("v1.2.0"),
                _rel("v1.1.0", pre=True),
                _rel("nightly"),
            ],
        )

    rels = await _source(handler).list_releases("demo", "o/r")
    assert [r.version for r in rels] == ["1.2.0", "1.0.0"]
    latest = await _source(handler).latest_release("demo", "o/r")
    assert latest is not None and latest.version == "1.2.0"


def test_config_core_repo_semantics(tmp_path: Path) -> None:
    only_system = PluginUpdateConfig(sources_file=None, core_update_repo="o/r")
    assert only_system.enabled and not only_system.plugin_checks_enabled
    assert PluginUpdateConfig().core_update_repo == "baselithcore/baselithcore"
    off = PluginUpdateConfig(sources_file=None, core_update_repo="")
    assert not off.enabled


def _capped(
    handler: Callable[[httpx.Request], httpx.Response], max_bytes: int
) -> GitHubReleaseSource:
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    return GitHubReleaseSource(
        "https://api.github.com", SecretStr("tok"), client=client, max_bytes=max_bytes
    )


async def test_download_refuses_a_declared_oversize_asset(tmp_path: Path) -> None:
    def handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(200, headers={"content-length": "11"}, content=b"x" * 11)

    dest = tmp_path / "x.tgz"
    with pytest.raises(SourceError, match="larger than 10 bytes"):
        await _capped(handler, 10).download("https://api.github.com/a/1/tgz", dest)
    assert not dest.exists()


async def test_download_stops_streaming_past_the_cap(tmp_path: Path) -> None:
    async def body():  # no content-length: only the streamed count can catch it
        for _ in range(4):
            yield b"x" * 8

    def handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=body())

    dest = tmp_path / "x.tgz"
    with pytest.raises(SourceError, match="larger than 20 bytes"):
        await _capped(handler, 20).download("https://api.github.com/a/1/tgz", dest)
    assert not dest.exists()


async def test_download_at_the_cap_is_accepted(tmp_path: Path) -> None:
    dest = tmp_path / "x.tgz"
    await _capped(lambda r: httpx.Response(200, content=b"x" * 10), 10).download(
        "https://api.github.com/a/1/tgz", dest
    )
    assert dest.read_bytes() == b"x" * 10


async def test_empty_token_sends_no_authorization() -> None:
    seen: list[str | None] = []

    def handler(req: httpx.Request) -> httpx.Response:
        seen.append(req.headers.get("authorization"))
        return httpx.Response(200, json=[])

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    src = GitHubReleaseSource("https://api.github.com", SecretStr(""), client=client)
    await src.list_releases("demo", "o/r")
    assert seen == [None]


def test_config_artifact_cap_default_and_floor() -> None:
    assert PluginUpdateConfig().max_artifact_mb == 200
    with pytest.raises(ValueError):
        PluginUpdateConfig(max_artifact_mb=0)


@pytest.mark.parametrize(
    "url",
    [
        "https://api.github.com",
        "https://ghe.example/api/v3",
        "http://127.0.0.1:8765",
        "http://localhost:9000",
        "http://[::1]:9000",
    ],
)
def test_config_api_url_accepted(url: str) -> None:
    assert PluginUpdateConfig(github_api_url=url).github_api_url == url


@pytest.mark.parametrize(
    "url",
    ["http://api.github.com", "http://10.0.0.5", "ftp://127.0.0.1", "api.github.com"],
)
def test_config_api_url_must_be_https_off_loopback(url: str) -> None:
    with pytest.raises(ValueError, match="github_api_url"):
        PluginUpdateConfig(github_api_url=url)


async def test_release_by_tag_asks_for_one_tag() -> None:
    seen: list[str] = []

    def handler(req: httpx.Request) -> httpx.Response:
        seen.append(req.url.path)
        if req.url.path.endswith("/v1.2.0"):
            return httpx.Response(200, json=_rel("v1.2.0"))
        if req.url.path.endswith("/v1.3.0-rc1"):
            return httpx.Response(200, json=_rel("v1.3.0-rc1", pre=True))
        return httpx.Response(404, json={"message": "Not Found"})

    src = _source(handler)
    info = await src.release_by_tag("demo", "o/r", "v1.2.0")
    assert info is not None and info.version == "1.2.0" and info.tarball_url
    assert seen == ["/repos/o/r/releases/tags/v1.2.0"]
    assert await src.release_by_tag("demo", "o/r", "v9.9.9") is None
    assert await src.release_by_tag("demo", "o/r", "v1.3.0-rc1") is None


async def test_release_by_tag_raises_on_server_error() -> None:
    src = _source(lambda req: httpx.Response(500))
    with pytest.raises(SourceError):
        await src.release_by_tag("demo", "o/r", "v1.2.0")


# ── Metadata responses are capped like downloads ─────────────────────────────


async def test_json_metadata_over_the_cap_is_refused() -> None:
    from core.plugin_updates.sources import MAX_TEXT_FILE_BYTES, SourceError

    big = b"[" + b"1," * (MAX_TEXT_FILE_BYTES // 2) + b"1]"
    src = _source(lambda r: httpx.Response(200, content=big))
    with pytest.raises(SourceError, match="larger than"):
        await src.latest_release("demo", "o/r")


async def test_declared_oversize_metadata_is_refused_before_the_body() -> None:
    from core.plugin_updates.sources import MAX_TEXT_FILE_BYTES, SourceError

    src = _source(
        lambda r: httpx.Response(
            200, content=b"[]", headers={"Content-Length": str(MAX_TEXT_FILE_BYTES + 1)}
        )
    )
    with pytest.raises(SourceError, match="larger than"):
        await src.latest_release("demo", "o/r")


async def test_download_accepts_a_tighter_per_call_cap(tmp_path: Path) -> None:
    from core.plugin_updates.sources import SourceError

    src = _source(lambda r: httpx.Response(200, content=b"x" * 100))
    with pytest.raises(SourceError, match="larger than 10"):
        await src.download(
            "https://api.github.com/a/1/json", tmp_path / "f", max_bytes=10
        )
    assert not (tmp_path / "f").exists()
