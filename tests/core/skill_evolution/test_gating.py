"""Unit tests for the skill validation gate (fail-closed)."""

from __future__ import annotations

from pathlib import Path

import pytest

from core.skill_evolution.gating import SkillGate
from core.skill_evolution.types import SkillProposal
from core.skill_evolution.writer import ManagedSkillWriter


def _validator(score: float):
    async def validate(_name: str) -> float:
        return score

    return validate


async def _broken(_name: str) -> float:
    raise RuntimeError("eval infra down")


async def _writer_with_versions(tmp_path: Path, *bodies: str) -> ManagedSkillWriter:
    writer = ManagedSkillWriter(tmp_path)
    for body in bodies:
        await writer.write(SkillProposal(name="fix-parser", description="d", body=body))
    return writer


async def test_first_review_accepts_and_records_best(tmp_path: Path) -> None:
    writer = await _writer_with_versions(tmp_path, "v1")
    gate = SkillGate(writer)
    decision = await gate.review("fix-parser", _validator(0.7))
    assert decision.accepted is True
    assert decision.previous_best is None
    assert decision.rolled_back is False
    assert (await writer.read_meta("fix-parser"))["best_score"] == pytest.approx(0.7)


async def test_lower_score_rejects_and_rolls_back(tmp_path: Path) -> None:
    writer = await _writer_with_versions(tmp_path, "v1", "v2")
    gate = SkillGate(writer)
    await gate.review("fix-parser", _validator(0.7))
    decision = await gate.review("fix-parser", _validator(0.5))
    assert decision.accepted is False
    assert decision.rolled_back is True
    meta = await writer.read_meta("fix-parser")
    assert meta["version"] == 1
    assert meta["best_score"] == pytest.approx(0.7)
    current = (tmp_path / "fix-parser" / "SKILL.md").read_text(encoding="utf-8")
    assert "v1" in current


async def test_higher_score_accepts_and_updates_best(tmp_path: Path) -> None:
    writer = await _writer_with_versions(tmp_path, "v1", "v2")
    gate = SkillGate(writer)
    await gate.review("fix-parser", _validator(0.7))
    decision = await gate.review("fix-parser", _validator(0.9))
    assert decision.accepted is True
    assert decision.previous_best == pytest.approx(0.7)
    meta = await writer.read_meta("fix-parser")
    assert meta["best_score"] == pytest.approx(0.9)
    assert meta["version"] == 2


async def test_raising_validator_rejects_even_on_first_review(tmp_path: Path) -> None:
    writer = await _writer_with_versions(tmp_path, "v1")
    gate = SkillGate(writer)
    decision = await gate.review("fix-parser", _broken)
    assert decision.accepted is False
    assert decision.score == 0.0
    # best score untouched: the bar is not lowered by a broken validator
    assert (await writer.read_meta("fix-parser"))["best_score"] is None


async def test_raising_validator_rolls_back_later_versions(tmp_path: Path) -> None:
    writer = await _writer_with_versions(tmp_path, "v1", "v2")
    gate = SkillGate(writer)
    await gate.review("fix-parser", _validator(0.7))
    decision = await gate.review("fix-parser", _broken)
    assert decision.accepted is False
    assert decision.rolled_back is True
    assert (await writer.read_meta("fix-parser"))["version"] == 1


async def test_review_of_nonexistent_skill_rejects_without_ghost_meta(
    tmp_path: Path,
) -> None:
    writer = ManagedSkillWriter(tmp_path)
    gate = SkillGate(writer)
    decision = await gate.review("ghost", _validator(0.9))
    assert decision.accepted is False
    assert not (tmp_path / "ghost").exists()
