"""Release sources: the mirror registry and the GitHub releases API."""

from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Any
from urllib.parse import quote

import httpx
import yaml
from pydantic import SecretStr

from ._advisories import parse_advisory
from ._release_parse import parse_release as _parse_release
from ._release_parse import text as _text
from .github import repo_slug
from .models import Advisory, ReleaseInfo

logger = logging.getLogger(__name__)

_API_VERSION = "2022-11-28"
_TIMEOUT = httpx.Timeout(30.0)
#: Download cap when the caller sets none (``PLUGIN_UPDATE_MAX_ARTIFACT_MB``).
DEFAULT_MAX_ARTIFACT_BYTES = 200 * 1024 * 1024

#: Largest text file (a plugin manifest) read through the contents API.
MAX_TEXT_FILE_BYTES = 1024 * 1024
_COMMIT_SHA = re.compile(r"^[0-9a-fA-F]{40}$")
#: Branch names put into a compare URL (git ref characters, no ``..``).
_BRANCH = re.compile(r"^[A-Za-z0-9._/-]{1,255}$")

#: Repos already logged as publishing no advisories (once per process).
_NO_ADVISORIES_LOGGED: set[str] = set()


def is_commit_sha(value: str | None) -> bool:
    """Whether ``value`` is a full 40-hex git commit id."""
    return bool(value) and _COMMIT_SHA.match(value or "") is not None


def _discard(path: Path) -> None:
    """Remove a partial download, ignoring a file that is already gone."""
    path.unlink(missing_ok=True)


class AdvisoriesNotPublished(Exception):
    """The advisories endpoint answered 404: nothing is known, not "none".

    A 404 is also what a token without the advisories permission, a renamed
    repo or a transient GitHub fault looks like, so it is a distinct signal
    (not a :class:`SourceError`, no error text) and never proof that a known
    security notice is gone.
    """


class SourceError(Exception):
    """A release source could not be queried or downloaded from.

    ``status`` is the HTTP status when the failure was an HTTP status error,
    else ``None``. It never carries a token.
    """

    def __init__(self, message: str = "", status: int | None = None) -> None:
        super().__init__(message)
        self.status = status


def safe_error(exc: Exception) -> str:
    """Error text safe to persist and serve: type name, message only for SourceError."""
    name = type(exc).__name__
    if isinstance(exc, SourceError) and str(exc):
        return f"{name}: {exc}"
    return name


def load_sources(path: Path) -> dict[str, str]:
    """Read the mirror registry into a ``plugin name -> owner/repo`` map.

    Entries with a ``path`` key are subtrees inside a plugin, not plugins, and
    entries whose repo is not a GitHub URL are skipped with a warning.

    Args:
        path: The mirror registry YAML (``mirrors:`` mapping).

    Returns:
        Plugin name to GitHub ``owner/repo`` slug.
    """
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    mirrors = data.get("mirrors") or {}
    sources: dict[str, str] = {}
    for name, entry in mirrors.items():
        if not isinstance(entry, dict) or "path" in entry:
            continue
        try:
            sources[str(name)] = repo_slug(str(entry.get("repo", "")))
        except ValueError:
            logger.warning(
                "plugin update source %r skipped: repo is not a GitHub URL", name
            )
    return sources


class GitHubReleaseSource:
    """Finds and downloads the latest release of a plugin's GitHub mirror."""

    def __init__(
        self,
        api_url: str,
        token: SecretStr | None,
        client: httpx.AsyncClient | None = None,
        *,
        max_bytes: int = DEFAULT_MAX_ARTIFACT_BYTES,
    ) -> None:
        """Create a source.

        Args:
            api_url: GitHub API base URL.
            token: Optional token sent as a bearer credential (an empty one is
                not sent).
            client: Optional shared HTTP client (tests inject a mock transport).
            max_bytes: Largest artifact :meth:`download` accepts. Downloads
                happen before any signature check, so this bounds what an
                unverified release can make the process write.
        """
        self._api_url = api_url.rstrip("/")
        self._token = token
        self._client = client
        self.max_bytes = max_bytes

    @property
    def api_url(self) -> str:
        """The GitHub API base URL, without a trailing slash."""
        return self._api_url

    def _headers(self, accept: str) -> dict[str, str]:
        headers = {"Accept": accept, "X-GitHub-Api-Version": _API_VERSION}
        if self._token is not None and self._token.get_secret_value():
            headers["Authorization"] = f"Bearer {self._token.get_secret_value()}"
        return headers

    def _too_large(self) -> str:
        return f"asset larger than {self.max_bytes} bytes"

    async def _get(self, url: str, accept: str) -> httpx.Response:
        client = self._client or httpx.AsyncClient(
            timeout=_TIMEOUT, follow_redirects=True
        )
        try:
            response = await client.get(
                url, headers=self._headers(accept), follow_redirects=True
            )
        except httpx.HTTPError as exc:
            raise SourceError(f"request failed: {type(exc).__name__}") from exc
        finally:
            if self._client is None:
                await client.aclose()
        if not response.is_success:
            raise SourceError(
                f"GitHub returned HTTP {response.status_code}", response.status_code
            )
        return response

    async def _json_list(self, url: str, what: str) -> list[Any]:
        response = await self._get(url, "application/vnd.github+json")
        try:
            body: Any = response.json()
        except ValueError as exc:
            raise SourceError(f"GitHub returned a non-JSON {what} response") from exc
        if not isinstance(body, list):
            raise SourceError(f"GitHub returned an unexpected {what} response")
        return body

    async def list_releases(self, plugin: str, slug: str) -> list[ReleaseInfo]:
        """Return the stable ``v<semver>`` releases, newest first.

        Args:
            plugin: Plugin name (selects the ``<plugin>-<version>.tar.gz`` asset).
            slug: ``owner/repo`` of the mirror.

        Drafts, prereleases and non-semver tags are excluded. A single
        malformed release entry (not an object, non-string fields, an
        unparseable date) is skipped rather than failing the whole check.

        Raises:
            SourceError: On a transport failure, a non-2xx response, or a
                response that is not a JSON list of releases.
        """
        releases = await self._json_list(
            f"{self._api_url}/repos/{slug}/releases?per_page=100", "releases"
        )
        parsed = [p for rel in releases if (p := _parse_release(plugin, rel))]
        parsed.sort(key=lambda pair: pair[0], reverse=True)
        return [info for _, info in parsed]

    async def release_by_tag(
        self, plugin: str, slug: str, tag: str
    ) -> ReleaseInfo | None:
        """Return the stable release tagged ``tag`` (``GET /releases/tags/<tag>``).

        ``None`` when there is no such release (404) or it is a draft, a
        prerelease or not ``v<semver>``.

        Raises:
            SourceError: On a transport failure, another non-2xx response, or
                a response that is not a JSON object.
        """
        try:
            rel = await self._json_object(
                f"{self._api_url}/repos/{slug}/releases/tags/{quote(tag, safe='')}",
                "release",
            )
        except SourceError as exc:
            if exc.status == 404:
                return None
            raise
        parsed = _parse_release(plugin, rel)
        return parsed[1] if parsed is not None and parsed[1].tag == tag else None

    async def latest_release(self, plugin: str, slug: str) -> ReleaseInfo | None:
        """Return the highest stable ``v<semver>`` release, or ``None``.

        Raises:
            SourceError: As :meth:`list_releases`.
        """
        releases = await self.list_releases(plugin, slug)
        return releases[0] if releases else None

    async def security_advisories(self, slug: str) -> list[Advisory]:
        """Return the repo's published security advisories.

        One :class:`Advisory` per (advisory, vulnerability) pair; malformed
        entries are skipped.

        A repo that publishes no advisories (GitHub answers 404: private
        repositories have none) raises :class:`AdvisoriesNotPublished`, which
        is not an empty list: only a 2xx fetch may say "no advisories". It is
        logged once per process.

        Raises:
            AdvisoriesNotPublished: On a 404.
            SourceError: On a transport failure, a non-2xx response other than
                404 (a token without the advisories scope answers 403), or a
                body that is not a JSON list.
        """
        try:
            entries = await self._json_list(
                f"{self._api_url}/repos/{slug}/security-advisories"
                "?state=published&per_page=100",
                "advisories",
            )
        except SourceError as exc:
            if exc.status != 404:
                raise
            if slug not in _NO_ADVISORIES_LOGGED:
                _NO_ADVISORIES_LOGGED.add(slug)
                logger.info("%s publishes no security advisories", slug)
            raise AdvisoriesNotPublished(slug) from exc
        return [adv for entry in entries for adv in parse_advisory(entry)]

    async def _json_object(self, url: str, what: str) -> dict[str, Any]:
        response = await self._get(url, "application/vnd.github+json")
        try:
            body: Any = response.json()
        except ValueError as exc:
            raise SourceError(f"GitHub returned a non-JSON {what} response") from exc
        if not isinstance(body, dict):
            raise SourceError(f"GitHub returned an unexpected {what} response")
        return body

    async def tag_commit(self, slug: str, tag: str) -> str:
        """Return the commit SHA ``tag`` points at, peeling an annotated tag.

        Args:
            slug: ``owner/repo`` of the mirror.
            tag: A ``v<semver>`` release tag (validated by the caller).

        Raises:
            SourceError: On a transport failure, a non-2xx response (a missing
                tag answers 404), or a reference that does not resolve to a
                commit.
        """
        obj = (
            await self._json_object(
                f"{self._api_url}/repos/{slug}/git/ref/tags/{tag}", "tag"
            )
        ).get("object")
        if isinstance(obj, dict) and obj.get("type") == "tag":
            peeled = await self._json_object(
                f"{self._api_url}/repos/{slug}/git/tags/{_text(obj.get('sha'))}",
                "tag",
            )
            obj = peeled.get("object")
        sha = _text(obj.get("sha")) if isinstance(obj, dict) else None
        if not isinstance(obj, dict) or obj.get("type") != "commit" or not sha:
            raise SourceError(f"tag {tag} does not point at a commit")
        if not is_commit_sha(sha):
            raise SourceError(f"tag {tag} resolved to a malformed commit id")
        return sha.lower()

    async def default_branch(self, slug: str) -> str:
        """Return the repository's default branch name.

        Raises:
            SourceError: On a transport failure, a non-2xx response, or a
                missing or malformed branch name.
        """
        repo = await self._json_object(f"{self._api_url}/repos/{slug}", "repository")
        branch = _text(repo.get("default_branch"))
        if not branch or not _BRANCH.match(branch) or ".." in branch:
            raise SourceError("repository reports no usable default branch")
        return branch

    async def compare_status(self, slug: str, base: str, head: str) -> str:
        """GitHub's ``status`` of ``base...head``: ahead, behind, identical, diverged.

        ``ahead`` or ``identical`` means ``base`` is an ancestor of ``head``.

        Raises:
            SourceError: On a transport failure, a non-2xx response, or a
                response without a status.
        """
        body = await self._json_object(
            f"{self._api_url}/repos/{slug}/compare/{base}...{head}?per_page=1",
            "compare",
        )
        status = _text(body.get("status"))
        if not status:
            raise SourceError("GitHub returned a comparison without a status")
        return status

    async def file_at(self, slug: str, path: str, ref: str) -> str | None:
        """Return a text file of the mirror at ``ref``, or None when absent.

        Read through the contents API on the API host (the token never leaves
        it), capped at :data:`MAX_TEXT_FILE_BYTES`.

        Raises:
            SourceError: On a transport failure, a non-2xx response other than
                404, a file over the cap, or bytes that are not UTF-8.
        """
        try:
            response = await self._get(
                f"{self._api_url}/repos/{slug}/contents/{path}?ref={ref}",
                "application/vnd.github.raw+json",
            )
        except SourceError as exc:
            if exc.status == 404:
                return None
            raise
        if len(response.content) > MAX_TEXT_FILE_BYTES:
            raise SourceError(f"{path} is larger than {MAX_TEXT_FILE_BYTES} bytes")
        try:
            return response.content.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise SourceError(f"{path} is not UTF-8 text") from exc

    def commit_url(self, slug: str, sha: str) -> str | None:
        """The https page of commit ``sha`` on the web host of this API, if known.

        ``api.github.com`` maps to ``github.com`` and a GitHub Enterprise
        ``https://<host>/api/v3`` to ``https://<host>``; any other API URL (a
        local fake) has no known web host and gets no link.
        """
        if not is_commit_sha(sha):
            return None
        api = httpx.URL(self._api_url)
        if api.scheme != "https" or not api.host:
            return None
        if api.host == "api.github.com":
            base = "https://github.com"
        elif api.path.rstrip("/") == "/api/v3":
            port = f":{api.port}" if api.port else ""
            base = f"https://{api.host}{port}"
        else:
            return None
        return f"{base}/{slug}/commit/{sha.lower()}"

    async def download(self, asset_url: str, dest: Path) -> None:
        """Stream a release asset to ``dest``.

        Args:
            asset_url: The API asset URL from :class:`ReleaseInfo`.
            dest: Destination file; parents are created, and nothing is left
                behind on failure.

        Raises:
            SourceError: If ``asset_url`` is not on the API host's scheme and
                host (the token is never sent elsewhere; no request is made),
                on a transport failure or non-2xx response, or when the asset
                is larger than ``max_bytes`` (declared or streamed; the partial
                file is removed).
        """
        target, api = httpx.URL(asset_url), httpx.URL(self._api_url)
        if (target.scheme, target.host, target.port) != (
            api.scheme,
            api.host,
            api.port,
        ):
            raise SourceError("asset URL is not on the GitHub API host")
        client = self._client or httpx.AsyncClient(
            timeout=_TIMEOUT, follow_redirects=True
        )
        try:
            async with client.stream(
                "GET",
                asset_url,
                headers=self._headers("application/octet-stream"),
                follow_redirects=True,
            ) as response:
                if not response.is_success:
                    raise SourceError(
                        f"GitHub returned HTTP {response.status_code}",
                        response.status_code,
                    )
                declared = response.headers.get("content-length", "")
                if declared.isdigit() and int(declared) > self.max_bytes:
                    raise SourceError(self._too_large())
                dest.parent.mkdir(parents=True, exist_ok=True)
                try:
                    with dest.open("wb") as fh:
                        written = 0
                        async for chunk in response.aiter_bytes():
                            written += len(chunk)
                            if written > self.max_bytes:
                                raise SourceError(self._too_large())
                            fh.write(chunk)
                except BaseException:
                    _discard(dest)
                    raise
        except httpx.HTTPError as exc:
            raise SourceError(f"download failed: {type(exc).__name__}") from exc
        finally:
            if self._client is None:
                await client.aclose()


__all__ = [
    "AdvisoriesNotPublished",
    "DEFAULT_MAX_ARTIFACT_BYTES",
    "GitHubReleaseSource",
    "MAX_TEXT_FILE_BYTES",
    "SourceError",
    "is_commit_sha",
    "load_sources",
    "safe_error",
]
