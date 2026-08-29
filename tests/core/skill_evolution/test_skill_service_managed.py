"""SkillService managed extra roots + activation hook tests."""

from __future__ import annotations

from pathlib import Path

from core.plugins.skills_service import SkillService

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


def _managed_root(tmp_path: Path) -> Path:
    root = tmp_path / "managed"
    (root / "fix-parser").mkdir(parents=True)
    (root / "fix-parser" / "SKILL.md").write_text(SKILL_MD, encoding="utf-8")
    return root


def test_extra_root_enters_catalog(tmp_path: Path) -> None:
    service = SkillService(
        _StubRegistry(),  # type: ignore[arg-type]
        extra_roots=[("managed", _managed_root(tmp_path))],
    )
    cards = service.catalog()
    assert [c.name for c in cards] == ["fix-parser"]
    assert cards[0].plugin == "managed"


def test_missing_extra_root_is_skipped(tmp_path: Path) -> None:
    service = SkillService(
        _StubRegistry(),  # type: ignore[arg-type]
        extra_roots=[("managed", tmp_path / "does-not-exist")],
    )
    assert service.catalog() == []


async def test_on_activate_hook_fires_once(tmp_path: Path) -> None:
    seen: list[str] = []
    service = SkillService(
        _StubRegistry(),  # type: ignore[arg-type]
        extra_roots=[("managed", _managed_root(tmp_path))],
        on_activate=seen.append,
    )
    result = await service.activate("fix-parser")
    assert result.success
    assert seen == ["fix-parser"]


async def test_raising_hook_does_not_break_activation(tmp_path: Path) -> None:
    def boom(_name: str) -> None:
        raise RuntimeError("tracker down")

    service = SkillService(
        _StubRegistry(),  # type: ignore[arg-type]
        extra_roots=[("managed", _managed_root(tmp_path))],
        on_activate=boom,
    )
    result = await service.activate("fix-parser")
    assert result.success


async def test_hook_not_fired_on_unknown_skill(tmp_path: Path) -> None:
    seen: list[str] = []
    service = SkillService(
        _StubRegistry(),  # type: ignore[arg-type]
        extra_roots=[("managed", _managed_root(tmp_path))],
        on_activate=seen.append,
    )
    result = await service.activate("nope")
    assert not result.success
    assert seen == []
