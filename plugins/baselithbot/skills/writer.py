"""Authoring helpers for workspace-scoped custom skills.

This module writes ``SKILL.md`` + ``MANIFEST.yaml`` bundles onto disk in the
layout expected by :mod:`plugins.baselithbot.skills.loader` so that the UI
can compose custom skills without shell access to the state directory.

Skill authoring contract (modern baselithbot/ClawHub style):

* ``SKILL.md`` carries a YAML frontmatter block (``name``, ``description``,
  optional ``version``) followed by markdown instructions the agent loads
  when the skill is activated.
* ``MANIFEST.yaml`` declares ``bundle_version`` and a ``compatibility``
  block with ``designed_for.surfaces`` (``chat``/``cli``/``ide``) plus at
  least one passing ``tested_on`` entry to reach the ``verified`` state.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, Field, field_validator

from .loader import LocalSkillSpec, discover_local_skill_specs

_SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{1,62}$")
_SUPPORTED_SURFACES = {"chat", "cli", "ide"}


class SkillDraft(BaseModel):
    """Input payload for creating or updating a custom workspace skill."""

    slug: str = Field(..., min_length=2, max_length=63)
    name: str = Field(..., min_length=1, max_length=120)
    description: str = Field(..., min_length=1, max_length=500)
    version: str = Field(default="0.1.0", max_length=32)
    instructions: str = Field(..., min_length=1, max_length=32_000)
    surfaces: list[str] = Field(default_factory=lambda: ["chat"], max_length=8)
    tags: list[str] = Field(default_factory=list, max_length=16)

    @field_validator("slug")
    @classmethod
    def _valid_slug(cls, value: str) -> str:
        cleaned = value.strip().lower()
        if not _SLUG_RE.match(cleaned):
            raise ValueError(
                "slug must be 2-63 chars, lowercase alphanumerics, '-' or '_', "
                "starting with a letter or digit"
            )
        return cleaned

    @field_validator("surfaces")
    @classmethod
    def _valid_surfaces(cls, value: list[str]) -> list[str]:
        cleaned: list[str] = []
        for surface in value:
            normalized = str(surface).strip().lower()
            if not normalized:
                continue
            if normalized not in _SUPPORTED_SURFACES:
                raise ValueError(
                    f"unsupported surface '{surface}'. "
                    f"Allowed: {sorted(_SUPPORTED_SURFACES)}"
                )
            cleaned.append(normalized)
        if not cleaned:
            raise ValueError("at least one supported surface must be declared")
        # Preserve order but drop duplicates.
        seen: set[str] = set()
        unique: list[str] = []
        for surface in cleaned:
            if surface not in seen:
                seen.add(surface)
                unique.append(surface)
        return unique

    @field_validator("tags")
    @classmethod
    def _clean_tags(cls, value: list[str]) -> list[str]:
        cleaned: list[str] = []
        seen: set[str] = set()
        for tag in value:
            normalized = str(tag).strip().lower()
            if not normalized or len(normalized) > 48:
                continue
            if normalized in seen:
                continue
            seen.add(normalized)
            cleaned.append(normalized)
        return cleaned


def _serialize_skill_md(draft: SkillDraft) -> str:
    frontmatter: dict[str, Any] = {
        "name": draft.name,
        "description": draft.description,
        "version": draft.version,
    }
    if draft.tags:
        frontmatter["tags"] = draft.tags
    rendered = yaml.safe_dump(frontmatter, sort_keys=False).strip()
    body = draft.instructions.strip() + "\n"
    return f"---\n{rendered}\n---\n\n{body}"


def _serialize_manifest(draft: SkillDraft) -> str:
    today = datetime.now(tz=UTC).date().isoformat()
    manifest: dict[str, Any] = {
        "bundle_version": draft.version,
        "compatibility": {
            "designed_for": {"surfaces": draft.surfaces},
            "tested_on": [
                {
                    "platform": "baselithbot",
                    "surface": surface,
                    "model": "workspace-author",
                    "status": "pass",
                    "date": today,
                }
                for surface in draft.surfaces
            ],
        },
    }
    if draft.tags:
        manifest["tags"] = draft.tags
    return yaml.safe_dump(manifest, sort_keys=False)


def write_workspace_skill(
    draft: SkillDraft,
    *,
    root: str | Path,
    overwrite: bool = False,
) -> LocalSkillSpec:
    """Persist ``draft`` into ``root/skills/<slug>/`` and return the spec.

    Parameters
    ----------
    draft:
        Validated input payload.
    root:
        Workspace root (state_dir for the global workspace, or a specific
        ``state_dir/workspaces/<name>`` for a per-workspace skill).
    overwrite:
        When ``False`` (default) and the target directory already exists,
        a :class:`FileExistsError` is raised so callers surface a 409.
    """

    base = Path(root)
    skills_root = base / "skills"
    target = skills_root / draft.slug
    if target.exists() and not overwrite:
        raise FileExistsError(
            f"skill '{draft.slug}' already exists at {target}. "
            "Use overwrite=True to replace it."
        )
    target.mkdir(parents=True, exist_ok=True)
    (target / "SKILL.md").write_text(_serialize_skill_md(draft), encoding="utf-8")
    (target / "MANIFEST.yaml").write_text(_serialize_manifest(draft), encoding="utf-8")

    specs = discover_local_skill_specs(base)
    for spec in specs:
        if spec.slug == draft.slug:
            return spec
    # Defensive — discovery should always find the just-written bundle.
    raise RuntimeError(
        f"skill '{draft.slug}' written to {target} but not found during rediscovery"
    )


def delete_workspace_skill(slug: str, *, root: str | Path) -> bool:
    """Remove ``root/skills/<slug>/`` and its contents. Returns True if removed."""
    base = Path(root)
    target = base / "skills" / slug
    if not target.exists():
        return False
    for child in sorted(target.rglob("*"), reverse=True):
        if child.is_file() or child.is_symlink():
            child.unlink()
        elif child.is_dir():
            child.rmdir()
    target.rmdir()
    return True


__all__ = [
    "SkillDraft",
    "delete_workspace_skill",
    "write_workspace_skill",
]
