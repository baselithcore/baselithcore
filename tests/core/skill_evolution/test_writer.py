"""Unit tests for the versioned managed-skill writer."""

from __future__ import annotations

from pathlib import Path

import pytest

from core.plugins.declarative import split_frontmatter
from core.skill_evolution.types import SkillProposal
from core.skill_evolution.writer import ManagedSkillWriter


def _proposal(body: str = "Do the thing.", name: str = "fix-parser") -> SkillProposal:
    return SkillProposal(name=name, description="Fix parser", body=body)


async def test_first_write_creates_valid_skill_v1(tmp_path: Path) -> None:
    writer = ManagedSkillWriter(tmp_path)
    path = await writer.write(_proposal())
    assert path == tmp_path / "fix-parser" / "SKILL.md"
    front, body = split_frontmatter(path.read_text(encoding="utf-8"))
    assert front["name"] == "fix-parser"
    assert front["version"] == "1"
    assert body.strip() == "Do the thing."
    assert (await writer.read_meta("fix-parser"))["version"] == 1


async def test_construction_does_not_touch_disk(tmp_path: Path) -> None:
    root = tmp_path / "never-created"
    ManagedSkillWriter(root)
    assert not root.exists()


async def test_second_write_archives_and_bumps(tmp_path: Path) -> None:
    writer = ManagedSkillWriter(tmp_path)
    await writer.write(_proposal("v1 body"))
    await writer.write(_proposal("v2 body"))
    assert (await writer.read_meta("fix-parser"))["version"] == 2
    archived = tmp_path / "fix-parser" / ".versions" / "1.md"
    assert "v1 body" in archived.read_text(encoding="utf-8")
    current = (tmp_path / "fix-parser" / "SKILL.md").read_text(encoding="utf-8")
    assert "v2 body" in current


async def test_rollback_restores_previous_version(tmp_path: Path) -> None:
    writer = ManagedSkillWriter(tmp_path)
    await writer.write(_proposal("v1 body"))
    await writer.write(_proposal("v2 body"))
    assert await writer.rollback("fix-parser") is True
    current = (tmp_path / "fix-parser" / "SKILL.md").read_text(encoding="utf-8")
    assert "v1 body" in current
    assert (await writer.read_meta("fix-parser"))["version"] == 1
    assert await writer.rollback("fix-parser") is False  # no more history


async def test_version_survives_meta_loss(tmp_path: Path) -> None:
    writer = ManagedSkillWriter(tmp_path)
    await writer.write(_proposal("v1 body"))
    await writer.write(_proposal("v2 body"))
    (tmp_path / "fix-parser" / "meta.json").unlink()
    # version is derived from the filesystem, not from meta.json
    assert (await writer.read_meta("fix-parser"))["version"] == 2
    assert await writer.rollback("fix-parser") is True


async def test_best_score_round_trip(tmp_path: Path) -> None:
    writer = ManagedSkillWriter(tmp_path)
    await writer.write(_proposal())
    assert (await writer.read_meta("fix-parser"))["best_score"] is None
    await writer.update_best_score("fix-parser", 0.8)
    assert (await writer.read_meta("fix-parser"))["best_score"] == pytest.approx(0.8)


async def test_yaml_keyword_and_numeric_names_round_trip(tmp_path: Path) -> None:
    writer = ManagedSkillWriter(tmp_path)
    for name in ("on", "no", "007"):
        path = await writer.write(_proposal(name=name))
        front, _ = split_frontmatter(path.read_text(encoding="utf-8"))
        assert front["name"] == name  # yaml.safe_dump quotes keywords/digits


async def test_body_starting_with_dashes_is_rejected(tmp_path: Path) -> None:
    writer = ManagedSkillWriter(tmp_path)
    bad = SkillProposal(name="evil", description="d", body="---\nname: hijack\n---\nx")
    with pytest.raises(ValueError):
        await writer.write(bad)
    assert not (tmp_path / "evil" / "SKILL.md").exists()


async def test_unsafe_name_rejected_defensively(tmp_path: Path) -> None:
    writer = ManagedSkillWriter(tmp_path)
    proposal = _proposal().model_copy(update={"name": "../escape"})
    with pytest.raises(ValueError):
        await writer.write(proposal)
    with pytest.raises(ValueError):
        await writer.rollback("../escape")


async def test_verify_detects_tampering(tmp_path: Path) -> None:
    writer = ManagedSkillWriter(tmp_path)
    path = await writer.write(_proposal())
    assert await writer.verify("fix-parser") is True
    assert writer.verify_path_sync(path) is True

    path.write_text(path.read_text(encoding="utf-8") + "\nEVIL", encoding="utf-8")
    assert await writer.verify("fix-parser") is False
    assert writer.verify_path_sync(path) is False


async def test_verify_after_rollback(tmp_path: Path) -> None:
    writer = ManagedSkillWriter(tmp_path)
    await writer.write(_proposal("v1 body"))
    await writer.write(_proposal("v2 body"))
    await writer.rollback("fix-parser")
    assert await writer.verify("fix-parser") is True


async def test_verify_unknown_skill_is_false(tmp_path: Path) -> None:
    writer = ManagedSkillWriter(tmp_path)
    assert await writer.verify("ghost") is False
    assert writer.verify_path_sync(tmp_path / "outside.md") is False
