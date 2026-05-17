"""Unit tests for ``core.plugins.declarative``."""

from __future__ import annotations

from pathlib import Path

import pytest

from core.plugins.declarative import (
    DeclarativeSkillLoader,
    SkillCard,
    SkillLoadError,
    SkillSandboxError,
)


def _write_skill(
    root: Path,
    subdir: str,
    *,
    frontmatter: str | None = None,
    body: str = "# heading\n\nbody text\n",
) -> Path:
    skills_dir = root / subdir
    skills_dir.mkdir(parents=True, exist_ok=True)
    p = skills_dir / "SKILL.md"
    front = (
        frontmatter
        if frontmatter is not None
        else ("name: Sample\ndescription: A sample skill.\n")
    )
    p.write_text(f"---\n{front}---\n{body}", encoding="utf-8")
    return p


class TestDiscovery:
    def test_discover_returns_card_for_each_skill(self, tmp_path: Path) -> None:
        _write_skill(tmp_path, "alpha")
        _write_skill(tmp_path, "beta", frontmatter="name: Beta\ndescription: B.\n")
        cards = DeclarativeSkillLoader([tmp_path]).discover()
        names = {c.name for c in cards}
        assert names == {"Sample", "Beta"}

    def test_discover_returns_empty_when_no_skills(self, tmp_path: Path) -> None:
        cards = DeclarativeSkillLoader([tmp_path]).discover()
        assert cards == []

    def test_discover_handles_nested_paths(self, tmp_path: Path) -> None:
        _write_skill(tmp_path, "a/b/c")
        cards = DeclarativeSkillLoader([tmp_path]).discover()
        assert len(cards) == 1

    def test_discover_sorted_by_name(self, tmp_path: Path) -> None:
        _write_skill(tmp_path, "z", frontmatter="name: Zeta\ndescription: z.\n")
        _write_skill(tmp_path, "a", frontmatter="name: Alpha\ndescription: a.\n")
        cards = DeclarativeSkillLoader([tmp_path]).discover()
        assert [c.name for c in cards] == ["Alpha", "Zeta"]


class TestActivation:
    def test_activate_returns_body_and_card(self, tmp_path: Path) -> None:
        path = _write_skill(tmp_path, "x", body="# Big Body\n\nDetailed text.\n")
        loader = DeclarativeSkillLoader([tmp_path])
        skill = loader.activate(path)
        assert "Big Body" in skill.body
        assert skill.card.name == "Sample"

    def test_activate_outside_root_raises(self, tmp_path: Path) -> None:
        loader = DeclarativeSkillLoader([tmp_path])
        outside = tmp_path.parent / "evil.md"
        with pytest.raises(SkillSandboxError):
            loader.activate(outside)


class TestFrontmatterValidation:
    def test_missing_frontmatter_rejected(self, tmp_path: Path) -> None:
        p = tmp_path / "a" / "SKILL.md"
        p.parent.mkdir()
        p.write_text("# no frontmatter\nbody\n", encoding="utf-8")
        loader = DeclarativeSkillLoader([tmp_path])
        with pytest.raises(SkillLoadError):
            loader.discover()

    def test_unterminated_frontmatter_rejected(self, tmp_path: Path) -> None:
        p = tmp_path / "a" / "SKILL.md"
        p.parent.mkdir()
        p.write_text(
            "---\nname: x\ndescription: y\nbody never closed", encoding="utf-8"
        )
        loader = DeclarativeSkillLoader([tmp_path])
        with pytest.raises(SkillLoadError):
            loader.discover()

    def test_missing_name_rejected(self, tmp_path: Path) -> None:
        _write_skill(tmp_path, "x", frontmatter="description: only.\n")
        with pytest.raises(SkillLoadError):
            DeclarativeSkillLoader([tmp_path]).discover()

    def test_missing_description_rejected(self, tmp_path: Path) -> None:
        _write_skill(tmp_path, "x", frontmatter="name: only\n")
        with pytest.raises(SkillLoadError):
            DeclarativeSkillLoader([tmp_path]).discover()

    def test_description_too_long_rejected(self, tmp_path: Path) -> None:
        long_desc = "x" * 250
        _write_skill(
            tmp_path,
            "x",
            frontmatter=f"name: n\ndescription: {long_desc}\n",
        )
        with pytest.raises(SkillLoadError):
            DeclarativeSkillLoader([tmp_path]).discover()

    def test_invalid_tools_rejected(self, tmp_path: Path) -> None:
        _write_skill(
            tmp_path,
            "x",
            frontmatter="name: n\ndescription: d.\ntools: not_a_list\n",
        )
        with pytest.raises(SkillLoadError):
            DeclarativeSkillLoader([tmp_path]).discover()


class TestExtendedFields:
    def test_version_and_tools_parsed(self, tmp_path: Path) -> None:
        _write_skill(
            tmp_path,
            "x",
            frontmatter=(
                "name: n\n"
                "description: d.\n"
                "version: 1.2.0\n"
                "requires_approval: true\n"
                "tools: [search, read]\n"
            ),
        )
        card = DeclarativeSkillLoader([tmp_path]).discover()[0]
        assert isinstance(card, SkillCard)
        assert card.version == "1.2.0"
        assert card.requires_approval is True
        assert card.tools == ("search", "read")

    def test_defaults_applied(self, tmp_path: Path) -> None:
        _write_skill(tmp_path, "x")
        card = DeclarativeSkillLoader([tmp_path]).discover()[0]
        assert card.version is None
        assert card.requires_approval is False
        assert card.tools == ()


class TestRootValidation:
    def test_no_existing_roots_rejected(self, tmp_path: Path) -> None:
        with pytest.raises(SkillSandboxError):
            DeclarativeSkillLoader([tmp_path / "does_not_exist"])

    def test_non_directory_root_rejected(self, tmp_path: Path) -> None:
        f = tmp_path / "file.txt"
        f.write_text("x")
        with pytest.raises(SkillSandboxError):
            DeclarativeSkillLoader([f])

    def test_multiple_roots_aggregated(self, tmp_path: Path) -> None:
        r1 = tmp_path / "r1"
        r2 = tmp_path / "r2"
        r1.mkdir()
        r2.mkdir()
        _write_skill(r1, "a", frontmatter="name: A\ndescription: a.\n")
        _write_skill(r2, "b", frontmatter="name: B\ndescription: b.\n")
        cards = DeclarativeSkillLoader([r1, r2]).discover()
        assert {c.name for c in cards} == {"A", "B"}
