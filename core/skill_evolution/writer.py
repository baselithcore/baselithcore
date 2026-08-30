"""Versioned writer for evolved (managed) declarative skills.

Layout per skill under the managed root::

    <root>/<name>/SKILL.md          # active version
    <root>/<name>/.versions/<n>.md  # archived version n
    <root>/<name>/meta.json         # {"best_score": float|null,
                                    #  "content_sha256": str|null}

The version number is derived from the filesystem (newest archive stem,
plus the active file) — never stored — so a crash between writes cannot
desynchronize a counter from the archives. File writes go through a
temp-file + ``os.replace`` so the active ``SKILL.md`` is always either the
old or the new content, never a torn write.

``meta.json`` holds only what the filesystem cannot express: the gate's
best validation score and the SHA-256 of the accepted content
(:meth:`ManagedSkillWriter.verify` backs the catalog's activation guard,
mirroring the plugin ``integrity_sha256`` model).

Every write is validated by round-tripping through the same frontmatter
parser the catalog uses before anything touches disk. All public methods
are async and off-load the blocking file I/O to a thread.
"""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import json
import os
import re
import tempfile
from pathlib import Path
from typing import Any

import yaml

from core.plugins.declarative import split_frontmatter
from core.skill_evolution.types import SKILL_NAME_PATTERN, SkillProposal

__all__ = ["ManagedSkillWriter"]

_NAME_RE = re.compile(SKILL_NAME_PATTERN)


class ManagedSkillWriter:
    """Write, version, verify, and roll back managed ``SKILL.md`` skills."""

    def __init__(self, root: Path) -> None:
        """Create the writer. The root directory is created lazily on the
        first write, never at construction time.

        Args:
            root: Directory that will hold one subdirectory per skill.
        """
        self._root = root

    @property
    def root(self) -> Path:
        """The managed skills root directory."""
        return self._root

    async def write(self, proposal: SkillProposal) -> Path:
        """Persist *proposal* as the next version of its skill.

        Returns:
            Path of the written ``SKILL.md``.

        Raises:
            ValueError: On an unsafe name or a body that breaks the
                frontmatter round-trip (e.g. a body starting with ``---``).
        """
        return await asyncio.to_thread(self._write_sync, proposal)

    async def rollback(self, name: str) -> bool:
        """Restore the most recent archived version of skill *name*.

        Returns:
            True when a previous version was restored; False when the skill
            has no archived history.
        """
        return await asyncio.to_thread(self._rollback_sync, name)

    async def read_meta(self, name: str) -> dict[str, Any]:
        """Return ``{"version", "best_score", "content_sha256"}`` for *name*.

        ``version`` is derived from the filesystem (0 when the skill does
        not exist yet).
        """
        return await asyncio.to_thread(self._read_meta_sync, name)

    async def update_best_score(self, name: str, score: float) -> None:
        """Record the best validation score achieved by skill *name*."""
        await asyncio.to_thread(self._update_best_score_sync, name, score)

    async def verify(self, name: str) -> bool:
        """Check the active ``SKILL.md`` against the recorded content hash.

        Returns:
            True iff the file exists and matches ``content_sha256``. Any
            missing piece (file, meta, hash) is a failed verification.
        """
        return await asyncio.to_thread(self._verify_sync, name)

    def verify_path_sync(self, path: Path) -> bool:
        """Synchronous integrity check for the skill owning *path*.

        Accepts the ``SKILL.md`` path as the catalog sees it; False when
        the path is not a managed active skill file or the hash mismatches.
        Kept synchronous for use inside the catalog's activation guard.
        """
        try:
            resolved = path.resolve()
            root = self._root.resolve()
            relative = resolved.relative_to(root)
        except (ValueError, OSError):
            return False
        if len(relative.parts) != 2 or relative.parts[1] != "SKILL.md":
            return False
        return self._verify_sync(relative.parts[0])

    # ------------------------------------------------------------------
    # Blocking internals (run in a worker thread)
    # ------------------------------------------------------------------

    def _write_sync(self, proposal: SkillProposal) -> Path:
        skill_dir = self._skill_dir(proposal.name)
        next_version = self._current_version(skill_dir) + 1
        rendered = _render(proposal, version=next_version)
        _validate_round_trip(proposal, rendered)

        skill_dir.mkdir(parents=True, exist_ok=True)
        skill_path = skill_dir / "SKILL.md"
        if skill_path.exists():
            versions_dir = skill_dir / ".versions"
            versions_dir.mkdir(exist_ok=True)
            _atomic_write(
                versions_dir / f"{next_version - 1}.md",
                skill_path.read_text(encoding="utf-8"),
            )
        _atomic_write(skill_path, rendered)
        self._record_hash(skill_dir, rendered)
        return skill_path

    def _rollback_sync(self, name: str) -> bool:
        skill_dir = self._skill_dir(name)
        archive = self._newest_archive(skill_dir)
        if archive is None:
            return False
        content = archive.read_text(encoding="utf-8")
        _atomic_write(skill_dir / "SKILL.md", content)
        archive.unlink()
        self._record_hash(skill_dir, content)
        return True

    def _read_meta_sync(self, name: str) -> dict[str, Any]:
        skill_dir = self._skill_dir(name)
        meta = self._read_meta_file(skill_dir)
        meta["version"] = self._current_version(skill_dir)
        return meta

    def _update_best_score_sync(self, name: str, score: float) -> None:
        skill_dir = self._skill_dir(name)
        meta = self._read_meta_file(skill_dir)
        meta["best_score"] = score
        skill_dir.mkdir(parents=True, exist_ok=True)
        self._write_meta_file(skill_dir, meta)

    def _verify_sync(self, name: str) -> bool:
        skill_dir = self._skill_dir(name)
        skill_path = skill_dir / "SKILL.md"
        expected = self._read_meta_file(skill_dir).get("content_sha256")
        if not expected or not skill_path.exists():
            return False
        actual = hashlib.sha256(skill_path.read_bytes()).hexdigest()
        return actual == expected

    def _skill_dir(self, name: str) -> Path:
        if not _NAME_RE.fullmatch(name):
            raise ValueError(f"unsafe managed skill name: {name!r}")
        return self._root / name

    def _record_hash(self, skill_dir: Path, content: str) -> None:
        meta = self._read_meta_file(skill_dir)
        meta["content_sha256"] = hashlib.sha256(content.encode("utf-8")).hexdigest()
        self._write_meta_file(skill_dir, meta)

    @staticmethod
    def _current_version(skill_dir: Path) -> int:
        """Derive the active version from the filesystem.

        0 = no skill; otherwise newest archive stem + 1 (an active file
        with no archives is version 1).
        """
        if not (skill_dir / "SKILL.md").exists():
            return 0
        newest = ManagedSkillWriter._newest_archive(skill_dir)
        return 1 if newest is None else int(newest.stem) + 1

    @staticmethod
    def _newest_archive(skill_dir: Path) -> Path | None:
        versions_dir = skill_dir / ".versions"
        if not versions_dir.is_dir():
            return None
        stems = [p for p in versions_dir.glob("*.md") if p.stem.isdigit()]
        return max(stems, key=lambda p: int(p.stem), default=None)

    @staticmethod
    def _read_meta_file(skill_dir: Path) -> dict[str, Any]:
        meta_path = skill_dir / "meta.json"
        if not meta_path.exists():
            return {"best_score": None, "content_sha256": None}
        data = json.loads(meta_path.read_text(encoding="utf-8"))
        return {
            "best_score": data.get("best_score"),
            "content_sha256": data.get("content_sha256"),
        }

    @staticmethod
    def _write_meta_file(skill_dir: Path, meta: dict[str, Any]) -> None:
        _atomic_write(
            skill_dir / "meta.json",
            json.dumps(
                {k: meta.get(k) for k in ("best_score", "content_sha256")}, indent=2
            ),
        )


def _atomic_write(path: Path, content: str) -> None:
    """Write *content* to *path* via temp file + rename (no torn writes)."""
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(content)
        os.replace(tmp, path)
    except BaseException:
        with contextlib.suppress(OSError):
            os.unlink(tmp)
        raise


def _render(proposal: SkillProposal, *, version: int) -> str:
    """Render frontmatter + body for one skill version."""
    front = yaml.safe_dump(
        {
            "name": proposal.name,
            "description": proposal.description,
            "version": str(version),
        },
        sort_keys=False,
        allow_unicode=True,
    ).strip()
    return f"---\n{front}\n---\n\n{proposal.body.rstrip()}\n"


def _validate_round_trip(proposal: SkillProposal, rendered: str) -> None:
    """Ensure the rendered document parses back to the same card.

    A body opening with ``---`` is rejected outright: this parser survives
    it (the blank line after the frontmatter disambiguates), but other
    frontmatter consumers of the same file format may not.
    """
    if proposal.body.lstrip().startswith("---"):
        raise ValueError("skill body must not start with a '---' line")
    try:
        front, body = split_frontmatter(rendered)
    except Exception as exc:
        raise ValueError(f"rendered skill does not parse: {exc}") from exc
    if front.get("name") != proposal.name or body.strip() != proposal.body.strip():
        raise ValueError("rendered skill round-trip mismatch")
