"""
Declarative SKILL.md loader with progressive disclosure.

Skills live as Markdown files with YAML frontmatter, version-controlled
like source. At startup the loader indexes the catalog (name + description
+ path); the agent only sees the heavy body when it activates a specific
skill. This scales to 50+ skills without context explosion.

The loader sandboxes every read against an allow-list of root directories,
so a hostile catalog or prompt-injection attempt cannot escape into the
filesystem ("model proposes, code disposes").
"""

from __future__ import annotations

import os
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Final

import yaml

SKILL_FILENAME: Final[str] = "SKILL.md"
MAX_DESCRIPTION_CHARS: Final[int] = 200
MAX_NAME_CHARS: Final[int] = 80


def _logger():  # lazy: keep this module import-light
    from core.observability.logging import get_logger

    return get_logger(__name__)


class SkillLoadError(RuntimeError):
    """Raised when a SKILL.md file is malformed or fails validation."""


class SkillSandboxError(RuntimeError):
    """Raised when a path escapes the configured skill roots."""


@dataclass(frozen=True)
class SkillCard:
    """Lightweight catalog entry (no body)."""

    name: str
    description: str
    path: Path
    version: str | None = None
    requires_approval: bool = False
    tools: tuple[str, ...] = ()
    plugin: str | None = None


@dataclass(frozen=True)
class LoadedSkill:
    """Activation payload: catalog card plus the full Markdown body.

    ``scripts``, ``references`` and ``assets`` list the relative filenames
    found under the skill directory's same-named subdirectories at
    activation time (empty when a subdirectory is absent). Every enumerated
    file is sandbox-validated against the loader roots, so a symlink
    escaping the roots fails the activation.
    """

    card: SkillCard
    body: str
    scripts: list[str] = field(default_factory=list)
    references: list[str] = field(default_factory=list)
    assets: list[str] = field(default_factory=list)


def split_frontmatter(text: str) -> tuple[dict[str, object], str]:
    """Return ``(frontmatter, body)`` for a Markdown file.

    Frontmatter is the YAML block between two ``---`` lines at the start.
    Public seam: plugin-level skill systems (e.g. baselithbot) reuse this
    parser so SKILL.md frontmatter semantics stay uniform across the stack.
    """
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        raise SkillLoadError("missing YAML frontmatter (no leading '---')")
    end = None
    for i in range(1, len(lines)):
        if lines[i].strip() == "---":
            end = i
            break
    if end is None:
        raise SkillLoadError("unterminated YAML frontmatter (no closing '---')")
    raw_front = "\n".join(lines[1:end])
    body = "\n".join(lines[end + 1 :]).lstrip("\n")
    parsed = yaml.safe_load(raw_front) or {}
    if not isinstance(parsed, dict):
        raise SkillLoadError(
            f"frontmatter must be a YAML mapping, got {type(parsed).__name__}"
        )
    return parsed, body


def _validate_card_dict(data: dict[str, object], path: Path) -> SkillCard:
    """Build a ``SkillCard`` from a frontmatter mapping. Raises on violations."""
    name = data.get("name")
    description = data.get("description")
    if not isinstance(name, str) or not name.strip():
        raise SkillLoadError(f"{path}: 'name' must be a non-empty string")
    if not isinstance(description, str) or not description.strip():
        raise SkillLoadError(f"{path}: 'description' must be a non-empty string")
    if len(name) > MAX_NAME_CHARS:
        raise SkillLoadError(
            f"{path}: 'name' length {len(name)} exceeds {MAX_NAME_CHARS}"
        )
    if len(description) > MAX_DESCRIPTION_CHARS:
        raise SkillLoadError(
            f"{path}: 'description' length {len(description)} "
            f"exceeds {MAX_DESCRIPTION_CHARS}"
        )
    version = data.get("version")
    if version is not None and not isinstance(version, str):
        raise SkillLoadError(f"{path}: 'version' must be a string if present")
    requires_approval = bool(data.get("requires_approval", False))
    raw_tools = data.get("tools", [])
    if not isinstance(raw_tools, list) or not all(
        isinstance(t, str) for t in raw_tools
    ):
        raise SkillLoadError(f"{path}: 'tools' must be a list of strings")
    return SkillCard(
        name=name,
        description=description,
        path=path,
        version=version,
        requires_approval=requires_approval,
        tools=tuple(raw_tools),
    )


class DeclarativeSkillLoader:
    """Discover and load ``SKILL.md`` files under a set of trusted roots."""

    def __init__(self, roots: Iterable[Path]) -> None:
        resolved: list[Path] = []
        for r in roots:
            if not r.exists():
                continue
            if not r.is_dir():
                raise SkillSandboxError(f"skill root is not a directory: {r}")
            resolved.append(r.resolve())
        if not resolved:
            raise SkillSandboxError("at least one existing skill root is required")
        self._roots: tuple[Path, ...] = tuple(resolved)

    @property
    def roots(self) -> tuple[Path, ...]:
        return self._roots

    def _assert_inside_roots(self, path: Path) -> None:
        """Raise ``SkillSandboxError`` if ``path`` escapes the allow-list."""
        resolved = path.resolve()
        for root in self._roots:
            try:
                resolved.relative_to(root)
                return
            except ValueError:
                continue
        raise SkillSandboxError(f"path {resolved} is outside configured skill roots")

    def discover(self) -> list[SkillCard]:
        """Return catalog entries for every ``SKILL.md`` under the roots.

        Fail-soft per file: one malformed ``SKILL.md`` is skipped with a
        warning instead of disabling every other skill under the same root
        (a torn write or hand edit must not blank the whole catalog).
        Sandbox violations still raise — they are a security signal, not a
        formatting accident.
        """
        cards: list[SkillCard] = []
        for root in self._roots:
            for dirpath, _dirnames, filenames in os.walk(root):
                if SKILL_FILENAME in filenames:
                    p = Path(dirpath) / SKILL_FILENAME
                    try:
                        cards.append(self._load_card(p))
                    except SkillLoadError as exc:
                        _logger().warning("Skipping malformed skill %s: %s", p, exc)
        cards.sort(key=lambda c: c.name)
        return cards

    def _load_card(self, path: Path) -> SkillCard:
        self._assert_inside_roots(path)
        try:
            text = path.read_text(encoding="utf-8")
        except OSError as exc:
            raise SkillLoadError(f"cannot read {path}: {exc}") from exc
        front, _body = split_frontmatter(text)
        return _validate_card_dict(front, path)

    def activate(self, path: Path) -> LoadedSkill:
        """Read both frontmatter and body. Validates the sandbox again.

        Also enumerates the skill's bundled ``scripts/``, ``references/``
        and ``assets/`` subdirectories (empty lists when absent); every
        bundled file is sandbox-validated, so a symlink escaping the roots
        raises :class:`SkillSandboxError`.
        """
        self._assert_inside_roots(path)
        try:
            text = path.read_text(encoding="utf-8")
        except OSError as exc:
            raise SkillLoadError(f"cannot read {path}: {exc}") from exc
        front, body = split_frontmatter(text)
        card = _validate_card_dict(front, path)
        skill_dir = path.parent
        return LoadedSkill(
            card=card,
            body=body,
            scripts=self._enumerate_bundle(skill_dir, "scripts"),
            references=self._enumerate_bundle(skill_dir, "references"),
            assets=self._enumerate_bundle(skill_dir, "assets"),
        )

    def _enumerate_bundle(self, skill_dir: Path, subdir: str) -> list[str]:
        """Relative filenames under ``skill_dir/subdir``, sandbox-validated.

        Returns a sorted list of POSIX-style paths relative to the
        subdirectory. Missing subdirectory ⇒ empty list. A file resolving
        outside the loader roots (symlink escape) raises
        :class:`SkillSandboxError` — a security signal, not a formatting
        accident.
        """
        base = skill_dir / subdir
        if not base.is_dir():
            return []
        names: list[str] = []
        for entry in base.rglob("*"):
            if not entry.is_file():
                continue
            self._assert_inside_roots(entry)
            names.append(entry.relative_to(base).as_posix())
        return sorted(names)
