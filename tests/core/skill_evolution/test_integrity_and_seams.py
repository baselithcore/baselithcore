"""Integrity guard, registry skill-root seam, and catalog fail-soft tests."""

from __future__ import annotations

from pathlib import Path

import pytest

from core.plugins.registry import PluginRegistry
from core.plugins.skills_service import SkillService
from core.skill_evolution.service import make_activation_guard
from core.skill_evolution.types import SkillProposal
from core.skill_evolution.writer import ManagedSkillWriter

SKILL_MD = """---
name: fix-parser
description: Fix the parser
version: "1"
---

Do the thing.
"""


class _StubRegistry:
    def get_all_skill_roots(self) -> dict[str, Path]:
        return {}


async def _written_writer(tmp_path: Path) -> ManagedSkillWriter:
    writer = ManagedSkillWriter(tmp_path / "managed")
    await writer.write(
        SkillProposal(name="fix-parser", description="Fix the parser", body="Do it.")
    )
    return writer


async def test_guard_allows_verified_managed_skill(tmp_path: Path) -> None:
    writer = await _written_writer(tmp_path)
    service = SkillService(
        _StubRegistry(),  # type: ignore[arg-type]
        extra_roots=[("managed", writer.root)],
        activation_guard=make_activation_guard(writer),
    )
    result = await service.activate("fix-parser")
    assert result.success


async def test_guard_blocks_tampered_managed_skill(tmp_path: Path) -> None:
    writer = await _written_writer(tmp_path)
    skill_path = writer.root / "fix-parser" / "SKILL.md"
    skill_path.write_text(
        skill_path.read_text(encoding="utf-8") + "\nEVIL INSTRUCTIONS",
        encoding="utf-8",
    )
    service = SkillService(
        _StubRegistry(),  # type: ignore[arg-type]
        extra_roots=[("managed", writer.root)],
        activation_guard=make_activation_guard(writer),
    )
    result = await service.activate("fix-parser")
    assert not result.success
    assert result.error_code == "skill_guard_rejected"


async def test_guard_ignores_non_managed_skills(tmp_path: Path) -> None:
    # A plugin-style root outside the writer's tree passes the guard untouched.
    plugin_root = tmp_path / "plugin-skills"
    (plugin_root / "fix-parser").mkdir(parents=True)
    (plugin_root / "fix-parser" / "SKILL.md").write_text(SKILL_MD, encoding="utf-8")
    writer = ManagedSkillWriter(tmp_path / "managed")
    service = SkillService(
        _StubRegistry(),  # type: ignore[arg-type]
        extra_roots=[("pluginish", plugin_root)],
        activation_guard=make_activation_guard(writer),
    )
    result = await service.activate("fix-parser")
    assert result.success


def test_registry_register_skill_root_flows_into_roots(tmp_path: Path) -> None:
    registry = PluginRegistry()
    managed = tmp_path / "managed"
    registry.register_skill_root("managed", managed)
    # Directory does not exist yet: not listed.
    assert "managed" not in registry.get_all_skill_roots()
    managed.mkdir()
    assert registry.get_all_skill_roots()["managed"] == managed


def test_registry_rejects_colliding_label(tmp_path: Path) -> None:
    registry = PluginRegistry()
    registry._plugin_directories["auth"] = tmp_path  # simulate a known plugin
    with pytest.raises(ValueError):
        registry.register_skill_root("auth", tmp_path / "managed")


def test_one_malformed_skill_does_not_blank_the_root(tmp_path: Path) -> None:
    root = tmp_path / "managed"
    (root / "good").mkdir(parents=True)
    (root / "good" / "SKILL.md").write_text(SKILL_MD, encoding="utf-8")
    (root / "bad").mkdir()
    (root / "bad" / "SKILL.md").write_text("no frontmatter at all", encoding="utf-8")

    service = SkillService(
        _StubRegistry(),  # type: ignore[arg-type]
        extra_roots=[("managed", root)],
    )
    names = [c.name for c in service.catalog()]
    assert names == ["fix-parser"]  # the good one survives the bad neighbor
