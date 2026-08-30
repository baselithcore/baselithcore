"""Tests for the SkillEvolutionService facade."""

from __future__ import annotations

from pathlib import Path

from core.context import reset_tenant_context, set_tenant_context
from core.events import EventNames, get_event_bus
from core.skill_evolution.gating import SkillGate
from core.skill_evolution.impact import SkillImpactTracker
from core.skill_evolution.maintainer import WikiMaintainer
from core.skill_evolution.proposer import SkillProposer
from core.skill_evolution.service import (
    SkillEvolutionService,
    build_skill_evolution_service,
)
from core.skill_evolution.store import InMemoryPatternStore
from core.skill_evolution.types import PatternStatus
from core.skill_evolution.writer import ManagedSkillWriter

VALID_SKILL = """---
name: avoid-utf8-assumption
description: Never assume UTF-8 when parsing uploads
---

Check encoding before parsing.
"""

FAILURE_EVENT = {
    "score": 0.1,
    "intent": "parse",
    "feedback": "ERROR boom",
    "run_id": "r1",
}


async def _validate_ok(_name: str) -> float:
    return 0.9


def _service(tmp_path: Path) -> tuple[SkillEvolutionService, InMemoryPatternStore]:
    store = InMemoryPatternStore()
    writer = ManagedSkillWriter(tmp_path)

    async def generate(_prompt: str) -> str:
        return VALID_SKILL

    service = SkillEvolutionService(
        store,
        writer,
        maintainer=WikiMaintainer(store),
        proposer=SkillProposer(store, writer, generate=generate, min_occurrences=1),
        gate=SkillGate(writer),
        impact=SkillImpactTracker(),
    )
    return service, store


async def test_evaluation_event_lands_pattern_and_outcome(tmp_path: Path) -> None:
    service, store = _service(tmp_path)
    service.impact.record_activation("some-skill", run_id="r1")
    service.start()
    try:
        await get_event_bus().emit(EventNames.EVALUATION_COMPLETED, dict(FAILURE_EVENT))
    finally:
        service.stop()
    assert len(await store.list_patterns()) == 1
    assert service.impact.stats()["some-skill"].outcomes == 1


async def test_unscored_event_is_skipped_entirely(tmp_path: Path) -> None:
    service, store = _service(tmp_path)
    service.impact.record_activation("some-skill", run_id="r1")
    service.start()
    try:
        await get_event_bus().emit(
            EventNames.EVALUATION_COMPLETED,
            {"score": None, "feedback": "ERROR boom", "run_id": "r1"},
        )
    finally:
        service.stop()
    assert await store.list_patterns() == []
    assert service.impact.stats()["some-skill"].outcomes == 0


async def test_stop_unsubscribes(tmp_path: Path) -> None:
    service, store = _service(tmp_path)
    service.start()
    service.stop()
    await get_event_bus().emit(EventNames.EVALUATION_COMPLETED, dict(FAILURE_EVENT))
    assert await store.list_patterns() == []


async def test_evolve_promotes_only_after_gate_accepts(tmp_path: Path) -> None:
    service, store = _service(tmp_path)
    pattern = await WikiMaintainer(store).distill_evaluation(FAILURE_EVENT)
    assert pattern is not None

    decision = await service.evolve(_validate_ok)
    assert decision is not None and decision.accepted is True
    assert decision.skill_name == "avoid-utf8-assumption"
    assert (tmp_path / "avoid-utf8-assumption" / "SKILL.md").exists()
    stored = await store.get(pattern.id)
    assert stored is not None and stored.status is PatternStatus.PROMOTED


async def test_evolve_rejection_keeps_patterns_candidate(tmp_path: Path) -> None:
    service, store = _service(tmp_path)
    pattern = await WikiMaintainer(store).distill_evaluation(FAILURE_EVENT)
    assert pattern is not None

    async def broken(_name: str) -> float:
        raise RuntimeError("eval down")

    decision = await service.evolve(broken)
    assert decision is not None and decision.accepted is False
    stored = await store.get(pattern.id)
    assert stored is not None and stored.status is PatternStatus.CANDIDATE


async def test_evolve_without_validator_is_refused(tmp_path: Path) -> None:
    service, _store = _service(tmp_path)
    assert await service.evolve() is None
    assert list(tmp_path.iterdir()) == []  # nothing written either


async def test_evolve_without_proposer_returns_none(tmp_path: Path) -> None:
    service = SkillEvolutionService(
        InMemoryPatternStore(), ManagedSkillWriter(tmp_path)
    )
    assert await service.evolve(_validate_ok) is None


async def test_evolve_refused_for_non_default_tenant(tmp_path: Path) -> None:
    service, store = _service(tmp_path)
    assert await WikiMaintainer(store).distill_evaluation(FAILURE_EVENT) is not None
    token = set_tenant_context("acme")
    try:
        assert await service.evolve(_validate_ok) is None
        decision = await service.evolve(_validate_ok, allow_tenant_synthesis=True)
        assert decision is not None
    finally:
        reset_tenant_context(token)


def test_factory_builds_service(tmp_path: Path) -> None:
    service = build_skill_evolution_service(root=tmp_path / "managed")
    assert isinstance(service, SkillEvolutionService)
    assert service.get_stats()["running"] is False
