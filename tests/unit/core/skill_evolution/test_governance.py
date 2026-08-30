"""Tests for governed self-modification: audit, fitness, cooldown, human gate."""

from __future__ import annotations

import pytest

from core.observability import audit as audit_module
from core.observability.audit import AuditEventType
from core.skill_evolution.gating import SkillGate
from core.skill_evolution.types import FitnessVector, Pattern, PatternKind
from core.skill_evolution.writer import ManagedSkillWriter

pytestmark = [pytest.mark.unit]


class _RecordingSink:
    def __init__(self) -> None:
        self.events: list = []

    async def write(self, event) -> None:
        self.events.append(event)


@pytest.fixture
def audit_sink(monkeypatch):
    sink = _RecordingSink()
    logger = audit_module.AuditLogger(sinks=[sink])
    monkeypatch.setattr(audit_module, "get_audit_logger", lambda: logger)
    return sink


@pytest.fixture
def writer(tmp_path):
    return ManagedSkillWriter(tmp_path)


async def _write_skill(writer: ManagedSkillWriter, name: str = "test-skill"):
    from core.skill_evolution.types import SkillProposal

    proposal = SkillProposal(
        name=name, description="a test skill", body="Do the thing."
    )
    await writer.write(proposal)
    return proposal


class TestGateAudit:
    async def test_accept_emits_self_modify_apply(self, writer, audit_sink):
        await _write_skill(writer)
        gate = SkillGate(writer)

        async def validate(name: str) -> float:
            return 0.9

        decision = await gate.review("test-skill", validate)
        assert decision.accepted
        applied = [
            e
            for e in audit_sink.events
            if e.event_type == AuditEventType.SELF_MODIFY_APPLY
        ]
        assert len(applied) == 1
        assert applied[0].resource == "test-skill"
        assert applied[0].details["score"] == 0.9

    async def test_reject_emits_self_modify_reject(self, writer, audit_sink):
        await _write_skill(writer)
        gate = SkillGate(writer)

        async def validate(name: str) -> float:
            raise RuntimeError("eval infra down")

        decision = await gate.review("test-skill", validate)
        assert not decision.accepted
        rejected = [
            e
            for e in audit_sink.events
            if e.event_type == AuditEventType.SELF_MODIFY_REJECT
        ]
        assert len(rejected) == 1
        assert rejected[0].success is False


class TestFitnessVector:
    def test_scalarization_penalizes_latency_and_cost(self):
        fast = FitnessVector(quality=0.9, latency_s=0.5, cost_usd=0.01)
        slow = FitnessVector(quality=0.9, latency_s=30.0, cost_usd=0.50)
        assert fast.scalarize() > slow.scalarize()

    def test_quality_only_matches_float_path(self):
        assert FitnessVector(quality=0.8).scalarize() == pytest.approx(0.8)

    async def test_gate_accepts_fitness_vector_validator(self, writer, audit_sink):
        await _write_skill(writer)
        gate = SkillGate(writer)

        async def validate(name: str) -> FitnessVector:
            return FitnessVector(quality=0.9, latency_s=1.0, cost_usd=0.01)

        decision = await gate.review("test-skill", validate)
        assert decision.accepted
        assert 0.0 < decision.score < 0.9  # penalized below raw quality


class TestRejectionCooldown:
    def _pattern(self, fingerprint: str) -> Pattern:
        return Pattern(
            fingerprint=fingerprint,
            kind=PatternKind.STRATEGY,
            title="t",
            summary="s",
            occurrences=5,
        )

    async def test_rejected_patterns_skipped_until_cooldown(self, writer):
        from core.skill_evolution.proposer import SkillProposer
        from core.skill_evolution.store import InMemoryPatternStore

        store = InMemoryPatternStore()
        pattern = await store.upsert(self._pattern("fp-1"))

        async def generate(prompt: str) -> str:
            return "---\nname: gen-skill\ndescription: g\n---\nbody"

        clock = [0.0]
        proposer = SkillProposer(
            store,
            writer,
            generate=generate,
            min_occurrences=1,
            rejection_cooldown_seconds=100.0,
            now=lambda: clock[0],
        )
        proposer.record_rejection([pattern.id])
        assert await proposer.propose() is None  # cooling down

        clock[0] = 101.0
        assert await proposer.propose() is not None  # cooldown expired


class TestHumanGate:
    async def test_self_modify_category_requires_approval_supervised(self):
        from core.orchestration.autonomy import AutonomyLevel, AutonomyPolicy

        policy = AutonomyPolicy(level=AutonomyLevel.SUPERVISED)
        assert policy.requires_approval("self_modify") is True

        autonomous = AutonomyPolicy(level=AutonomyLevel.FULLY_AUTONOMOUS)
        assert autonomous.requires_approval("self_modify") is False

    async def test_evolve_denied_without_approval_channel(self, writer, tmp_path):
        from core.orchestration.autonomy import AutonomyLevel, AutonomyPolicy
        from core.skill_evolution.gating import SkillGate
        from core.skill_evolution.proposer import SkillProposer
        from core.skill_evolution.service import SkillEvolutionService
        from core.skill_evolution.store import InMemoryPatternStore

        store = InMemoryPatternStore()
        await store.upsert(
            Pattern(
                fingerprint="fp-x",
                kind=PatternKind.STRATEGY,
                title="t",
                summary="s",
                occurrences=5,
            )
        )

        async def generate(prompt: str) -> str:
            return "---\nname: gen-skill\ndescription: g\n---\nbody"

        async def validate(name: str) -> float:
            return 0.95

        service = SkillEvolutionService(
            store,
            writer,
            proposer=SkillProposer(store, writer, generate=generate, min_occurrences=1),
            gate=SkillGate(writer),
        )
        decision = await service.evolve(
            validate,
            autonomy_policy=AutonomyPolicy(level=AutonomyLevel.SUPERVISED),
        )
        # Approval required, no channel available: fail closed — the
        # eval-accepted version does not stand. (rolled_back mirrors the
        # writer result: a first version has no archive to restore.)
        assert decision is not None
        assert decision.accepted is False
