"""The executor's I/O seams: commands, the schema-init env, release downloads, the probe."""

from __future__ import annotations

import logging
import sys
from pathlib import Path

import httpx
import pytest

from core.config.plugin_update_apply import UpdateApplyConfig
from core.plugin_updates.apply._io import (
    GitHubReleaseFetcher,
    SchemaEnvError,
    http_probe,
    schema_env,
    subprocess_runner,
)
from core.plugin_updates.models import ReleaseInfo
from core.plugin_updates.sources import SourceError


def test_subprocess_runner_returns_the_exit_code(tmp_path: Path) -> None:
    code = "import os,sys; sys.exit(3 if os.environ.get('K') == 'v' else 1)"
    assert (
        subprocess_runner([sys.executable, "-c", code], env={"K": "v"}, timeout=30) == 3
    )


def test_subprocess_runner_uses_no_shell(tmp_path: Path) -> None:
    marker = tmp_path / "pwned"
    argv = [sys.executable, "-c", "import sys; sys.exit(0)", f"; touch {marker}"]
    assert subprocess_runner(argv, timeout=30) == 0
    assert not marker.exists()


def test_subprocess_runner_minus_one_on_missing_binary_and_timeout(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    with caplog.at_level(logging.ERROR):
        assert subprocess_runner([str(tmp_path / "nope")], timeout=5) == -1
        slow = [sys.executable, "-c", "import time; time.sleep(30)", "SECRETARG"]
        assert subprocess_runner(slow, timeout=0.5) == -1
    assert "SECRETARG" not in caplog.text


def test_schema_env_adds_the_owner_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("KEEP_ME", "1")
    owner = tmp_path / "owner.env"
    owner.write_text("POSTGRES_USER=owner\nPOSTGRES_PASSWORD=pw\nEMPTY\n")
    cfg = UpdateApplyConfig(state_dir=tmp_path / "s", schema_env_file=owner)
    env = schema_env(cfg)
    assert env["KEEP_ME"] == "1" and env["POSTGRES_USER"] == "owner"
    assert env["POSTGRES_PASSWORD"] == "pw" and "EMPTY" not in env
    plain = schema_env(UpdateApplyConfig(state_dir=tmp_path / "s"))
    assert "POSTGRES_PASSWORD" not in plain or plain["POSTGRES_PASSWORD"] != "pw"


class _Source:
    def __init__(self, releases: list[ReleaseInfo]) -> None:
        self.releases = {r.tag: r for r in releases}
        self.downloads: list[str] = []
        self.asked: list[tuple[str, str, str]] = []

    async def release_by_tag(
        self, plugin: str, slug: str, tag: str
    ) -> ReleaseInfo | None:
        self.asked.append((plugin, slug, tag))
        return self.releases.get(tag)

    async def download(self, asset_url: str, dest: Path) -> None:
        self.downloads.append(asset_url)
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(asset_url.encode())


def _info(version: str) -> ReleaseInfo:
    return ReleaseInfo(
        plugin="demo",
        version=version,
        tag=f"v{version}",
        published_at=None,
        tarball_url=f"https://api.example/{version}.tgz",
        release_json_url=f"https://api.example/{version}.json",
    )


async def test_fetcher_downloads_the_tagged_release(tmp_path: Path) -> None:
    src = _Source([_info("1.3.0"), _info("1.2.0")])
    fetcher = GitHubReleaseFetcher(src, {"demo": "o/plugin-demo"})  # type: ignore[arg-type]
    assert (
        await fetcher.release_json("demo", "1.2.0") == b"https://api.example/1.2.0.json"
    )
    await fetcher.tarball("demo", "1.2.0", tmp_path / "t.tgz")
    assert (tmp_path / "t.tgz").read_bytes() == b"https://api.example/1.2.0.tgz"
    assert src.asked[0] == ("demo", "o/plugin-demo", "v1.2.0")


async def test_fetcher_refuses_unknown_plugin_or_version(tmp_path: Path) -> None:
    fetcher = GitHubReleaseFetcher(_Source([_info("1.3.0")]), {"demo": "o/r"})  # type: ignore[arg-type]
    with pytest.raises(SourceError) as missing:
        await fetcher.release_json("demo", "1.2.0")
    assert "http" not in str(missing.value)
    with pytest.raises(SourceError):
        await fetcher.release_json("other", "1.3.0")
    bare = ReleaseInfo(plugin="demo", version="1.3.0", tag="v1.3.0", published_at=None)
    with pytest.raises(SourceError):
        await GitHubReleaseFetcher(_Source([bare]), {"demo": "o/r"}).tarball(  # type: ignore[arg-type]
            "demo", "1.3.0", tmp_path / "x"
        )


def test_schema_env_refuses_a_missing_or_unreadable_file(tmp_path: Path) -> None:
    cfg = UpdateApplyConfig(
        state_dir=tmp_path / "s", schema_env_file=tmp_path / "gone.env"
    )
    with pytest.raises(SchemaEnvError) as missing:
        schema_env(cfg)
    assert str(tmp_path) not in str(missing.value) and "gone.env" not in str(
        missing.value
    )
    folder = tmp_path / "dir.env"
    folder.mkdir()
    with pytest.raises(SchemaEnvError):
        schema_env(cfg.model_copy(update={"schema_env_file": folder}))


async def test_http_probe_status_and_unreachable() -> None:
    ok = httpx.AsyncClient(transport=httpx.MockTransport(lambda r: httpx.Response(503)))
    assert await http_probe("http://127.0.0.1:1/health/ready", client=ok) == 503

    def boom(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused")

    down = httpx.AsyncClient(transport=httpx.MockTransport(boom))
    assert await http_probe("http://127.0.0.1:1/health/ready", client=down) == 0
