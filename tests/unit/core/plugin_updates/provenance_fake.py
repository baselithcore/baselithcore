"""A fake GitHub API for the provenance-trust tests (``httpx.MockTransport``).

It serves the releases list, the repository (its default branch), the
comparison of a commit with that branch, the tag reference and the manifest at
a commit.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import httpx
from pydantic import SecretStr

from core.plugin_updates.provenance import (
    RELEASE_WORKFLOW_AUTHOR,
    RELEASE_WORKFLOW_AUTHOR_ID,
    RELEASE_WORKFLOW_AUTHOR_TYPE,
    check_plugin_provenance,
)
from core.plugin_updates.sources import GitHubReleaseSource

SHA = "a" * 40
OTHER = "b" * 40
API = "https://api.github.com"


def _release(
    tag: str = "v1.2.0",
    *,
    author: str | None = RELEASE_WORKFLOW_AUTHOR,
    author_id: int = RELEASE_WORKFLOW_AUTHOR_ID,
    author_type: str = RELEASE_WORKFLOW_AUTHOR_TYPE,
    target: str = SHA,
    draft: bool = False,
    pre: bool = False,
) -> dict[str, Any]:
    return {
        "tag_name": tag,
        "draft": draft,
        "prerelease": pre,
        "body": "notes",
        "html_url": f"https://github.com/o/r/releases/tag/{tag}",
        "published_at": "2026-09-30T10:00:00Z",
        "target_commitish": target,
        "author": (
            {"login": author, "id": author_id, "type": author_type}
            if author is not None
            else None
        ),
        "assets": [],
    }


def _manifest(version: str = "1.2.0", name: str = "demo", extra: str = "") -> str:
    return f"name: {name}\nversion: {version}\n{extra}"


@dataclass
class FakeGitHub:
    releases: list[dict[str, Any]] = field(default_factory=lambda: [_release()])
    tags: dict[str, dict[str, Any]] = field(
        default_factory=lambda: {"v1.2.0": {"type": "commit", "sha": SHA}}
    )
    annotated: dict[str, dict[str, Any]] = field(default_factory=dict)
    files: dict[tuple[str, str], bytes] = field(
        default_factory=lambda: {("manifest.yaml", SHA): _manifest().encode()}
    )
    releases_status: int = 200
    default_branch: str = "main"
    #: GitHub's compare status of ``<sha>...<default branch>``, per commit.
    compare: dict[str, str] = field(default_factory=lambda: {SHA: "ahead"})
    requests: list[httpx.Request] = field(default_factory=list)

    def __call__(self, req: httpx.Request) -> httpx.Response:
        self.requests.append(req)
        path = req.url.path.removeprefix("/api/v3")
        if path == "/repos/o/r":
            return httpx.Response(200, json={"default_branch": self.default_branch})
        if path.startswith("/repos/o/r/compare/"):
            base, _, head = path.removeprefix("/repos/o/r/compare/").partition("...")
            status = self.compare.get(base) if head == self.default_branch else None
            return httpx.Response(200, json={"status": status}) if status else _nf()
        if path == "/repos/o/r/releases":
            return httpx.Response(self.releases_status, json=self.releases)
        if path.startswith("/repos/o/r/git/ref/tags/"):
            obj = self.tags.get(path.rsplit("/", 1)[1])
            return httpx.Response(200, json={"object": obj}) if obj else _nf()
        if path.startswith("/repos/o/r/git/tags/"):
            obj = self.annotated.get(path.rsplit("/", 1)[1])
            return httpx.Response(200, json={"object": obj}) if obj else _nf()
        if path.startswith("/repos/o/r/contents/"):
            name = path.removeprefix("/repos/o/r/contents/")
            body = self.files.get((name, req.url.params.get("ref", "")))
            return httpx.Response(200, content=body) if body is not None else _nf()
        return _nf()


def _nf() -> httpx.Response:
    return httpx.Response(404, json={"message": "Not Found"})


def _source(fake: FakeGitHub, api: str = API) -> GitHubReleaseSource:
    client = httpx.AsyncClient(transport=httpx.MockTransport(fake))
    return GitHubReleaseSource(api, SecretStr("tok"), client=client)


async def _check(fake: FakeGitHub, installed: str | None = "1.0.0", **kw: Any):
    return await check_plugin_provenance(
        "demo",
        "o/r",
        installed,
        source=_source(fake, kw.pop("api", API)),
        core_version=kw.pop("core_version", "1.50.0"),
    )
