"""The executor's I/O seams: commands, the schema-init environment, releases, the probe.

Each seam is a small protocol so tests swap in fakes. Commands run from an
argv list without a shell; the schema owner's credentials only ever travel in
the ``schema-init`` subprocess environment and are never logged.
"""

from __future__ import annotations

import io
import logging
import os
import subprocess  # nosec B404 - argv lists only, never a shell
import tempfile
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Protocol

import httpx
from dotenv import dotenv_values

from core.config.plugin_update_apply import UpdateApplyConfig

from ..models import ReleaseInfo
from ..sources import GitHubReleaseSource, SourceError

logger = logging.getLogger(__name__)

_PROBE_TIMEOUT = 5.0


class CommandRunner(Protocol):
    """Runs one command; returns its exit code (``-1`` when it could not finish)."""

    def __call__(
        self,
        argv: Sequence[str],
        *,
        env: Mapping[str, str] | None = None,
        timeout: float,
    ) -> int: ...


class ReleaseFetcher(Protocol):
    """Downloads one release's signed ``release.json`` and its tarball."""

    async def release_json(self, plugin: str, version: str) -> bytes: ...

    async def tarball(self, plugin: str, version: str, dest: Path) -> None: ...


def subprocess_runner(
    argv: Sequence[str], *, env: Mapping[str, str] | None = None, timeout: float
) -> int:
    """Run ``argv`` without a shell; the exit code, or -1 when it could not finish.

    Only the program name is logged on failure: arguments and the
    environment may carry credentials.
    """
    try:
        done = subprocess.run(  # nosec B603 - argv list, shell=False
            list(argv),
            env=dict(env) if env is not None else None,
            timeout=timeout,
            check=False,
            stdin=subprocess.DEVNULL,
            shell=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        logger.error(
            "plugin_updater_command_failed program=%s error=%s",
            Path(argv[0]).name if argv else "",
            type(exc).__name__,
        )
        return -1
    return done.returncode


class SchemaEnvError(Exception):
    """The configured schema owner env file cannot be read (message holds no path)."""


def schema_env(config: UpdateApplyConfig) -> dict[str, str]:
    """The environment of ``schema-init``: ours plus the owner credentials file.

    Raises:
        SchemaEnvError: ``schema_env_file`` is configured but missing or
            unreadable; never fall back to the runtime credentials silently.
    """
    env = dict(os.environ)
    if config.schema_env_file is not None:
        try:
            text = config.schema_env_file.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError) as exc:
            raise SchemaEnvError(
                "the schema owner env file (UPDATE_APPLY_SCHEMA_ENV_FILE) "
                f"cannot be read: {type(exc).__name__}"
            ) from None
        values = dotenv_values(stream=io.StringIO(text))
        env.update({k: v for k, v in values.items() if v is not None})
    return env


class GitHubReleaseFetcher:
    """Finds the release tagged ``v<version>`` (one ``/releases/tags`` call) and downloads it."""

    def __init__(self, source: GitHubReleaseSource, sources: Mapping[str, str]) -> None:
        """Create a fetcher.

        Args:
            source: The GitHub client (its ``max_bytes`` bounds every download).
            sources: Plugin name to ``owner/repo`` slug.
        """
        self._source = source
        self._sources = dict(sources)

    async def _release(self, plugin: str, version: str) -> ReleaseInfo:
        slug = self._sources.get(plugin)
        if slug is None:
            raise SourceError("no release source for the plugin")
        tag = f"v{version}"
        info = await self._source.release_by_tag(plugin, slug, tag)
        if info is None:
            raise SourceError(f"release {tag} not found")
        return info

    async def release_json(self, plugin: str, version: str) -> bytes:
        """The raw ``release.json`` of ``plugin`` ``version``.

        Raises:
            SourceError: No source, no such release, no asset, or the download failed.
        """
        info = await self._release(plugin, version)
        if not info.release_json_url:
            raise SourceError("release has no release.json asset")
        with tempfile.TemporaryDirectory(prefix="plugin-release-") as tmp:
            dest = Path(tmp) / "release.json"
            await self._source.download(info.release_json_url, dest)
            return dest.read_bytes()

    async def tarball(self, plugin: str, version: str, dest: Path) -> None:
        """Download the tarball of ``plugin`` ``version`` to ``dest``.

        Raises:
            SourceError: As :meth:`release_json`.
        """
        info = await self._release(plugin, version)
        if not info.tarball_url:
            raise SourceError("release has no tarball asset")
        await self._source.download(info.tarball_url, dest)


async def http_probe(url: str, *, client: httpx.AsyncClient | None = None) -> int:
    """GET ``url`` (5 s timeout); the HTTP status, or 0 when unreachable."""
    owned = client is None
    http = client or httpx.AsyncClient(timeout=_PROBE_TIMEOUT, follow_redirects=False)
    try:
        response = await http.get(url, timeout=_PROBE_TIMEOUT)
    except httpx.HTTPError:
        return 0
    finally:
        if owned:
            await http.aclose()
    return response.status_code


__all__ = [
    "CommandRunner",
    "GitHubReleaseFetcher",
    "ReleaseFetcher",
    "SchemaEnvError",
    "http_probe",
    "schema_env",
    "subprocess_runner",
]
