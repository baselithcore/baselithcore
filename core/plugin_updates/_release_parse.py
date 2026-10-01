"""Parse one GitHub release object into a :class:`~.models.ReleaseInfo`."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from core.plugins.version import SemanticVersion

from .models import ReleaseInfo


def text(value: Any) -> str | None:
    """Return ``value`` when it is a string, else ``None``."""
    return value if isinstance(value, str) else None


def _account_id(author: Any) -> int | None:
    """``author.id`` when it is a real integer (a bool is not an id)."""
    if not isinstance(author, dict):
        return None
    value = author.get("id")
    return value if type(value) is int else None


def parse_release(plugin: str, rel: Any) -> tuple[SemanticVersion, ReleaseInfo] | None:
    """Map one GitHub release object to a candidate, or ``None`` to skip it."""
    if not isinstance(rel, dict) or rel.get("draft") or rel.get("prerelease"):
        return None
    tag = text(rel.get("tag_name"))
    if tag is None or not tag.startswith("v"):
        return None
    try:
        ver = SemanticVersion(tag[1:])
    except ValueError:
        return None
    if ver.prerelease:
        return None
    version = f"{ver.major}.{ver.minor}.{ver.patch}"
    author = rel.get("author")
    assets: dict[str, str] = {}
    raw_assets = rel.get("assets")
    for asset in raw_assets if isinstance(raw_assets, list) else []:
        if not isinstance(asset, dict):
            continue
        name, url = text(asset.get("name")), text(asset.get("url"))
        if name is not None and url is not None:
            assets[name] = url
    published_raw = text(rel.get("published_at"))
    try:
        published = (
            datetime.fromisoformat(published_raw.replace("Z", "+00:00"))
            if published_raw
            else None
        )
    except ValueError:
        return None
    return ver, ReleaseInfo(
        plugin=plugin,
        version=version,
        tag=tag,
        published_at=published,
        notes=text(rel.get("body")) or "",
        html_url=text(rel.get("html_url")) or "",
        tarball_url=assets.get(f"{plugin}-{version}.tar.gz"),
        release_json_url=assets.get("release.json"),
        author=text(author.get("login")) if isinstance(author, dict) else None,
        author_id=_account_id(author),
        author_type=text(author.get("type")) if isinstance(author, dict) else None,
        target_commitish=text(rel.get("target_commitish")),
    )


__all__ = ["parse_release", "text"]
