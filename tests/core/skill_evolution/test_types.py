"""Unit tests for skill-evolution domain types."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from core.skill_evolution.types import (
    MAX_EVIDENCE,
    EvidenceRef,
    Pattern,
    PatternKind,
    PatternStatus,
    SkillImpact,
    SkillProposal,
)


def _pattern(**overrides: object) -> Pattern:
    base: dict[str, object] = {
        "fingerprint": "abc123def456",
        "kind": PatternKind.FAILURE_MODE,
        "title": "Failure: parse",
        "summary": "AssertionError in parser",
    }
    base.update(overrides)
    return Pattern(**base)  # type: ignore[arg-type]


class TestPattern:
    def test_defaults(self) -> None:
        p = _pattern()
        assert p.occurrences == 1
        assert p.status is PatternStatus.CANDIDATE
        assert p.evidence == []
        assert len(p.id) == 32  # uuid4 hex

    def test_merge_observations_bumps_occurrences_and_appends_evidence(self) -> None:
        p = _pattern()
        merged = p.merge_observations(
            [EvidenceRef(run_id="r1", score=0.1), EvidenceRef(run_id="r2")]
        )
        assert merged.occurrences == 2  # one observation, regardless of entries
        assert [e.run_id for e in merged.evidence] == ["r1", "r2"]
        assert merged.id == p.id
        assert merged.updated_at >= p.updated_at

    def test_evidence_capped_keeping_newest(self) -> None:
        p = _pattern()
        for i in range(MAX_EVIDENCE + 5):
            p = p.merge_observations([EvidenceRef(note=f"n{i}")])
        assert len(p.evidence) == MAX_EVIDENCE
        assert p.evidence[-1].note == f"n{MAX_EVIDENCE + 4}"
        assert p.evidence[0].note == "n5"


class TestSkillProposal:
    def test_valid(self) -> None:
        sp = SkillProposal(name="fix-parser", description="d", body="b")
        assert sp.source_pattern_ids == []

    @pytest.mark.parametrize("bad", ["../evil", "UPPER", "-lead", "a b", ""])
    def test_rejects_unsafe_names(self, bad: str) -> None:
        with pytest.raises(ValidationError):
            SkillProposal(name=bad, description="d", body="b")

    def test_rejects_long_description(self) -> None:
        with pytest.raises(ValidationError):
            SkillProposal(name="ok", description="x" * 201, body="b")


class TestSkillImpact:
    def test_mean_score_none_without_outcomes(self) -> None:
        assert SkillImpact(skill_name="s").mean_score is None

    def test_mean_score(self) -> None:
        impact = SkillImpact(skill_name="s", outcomes=2, score_sum=1.5)
        assert impact.mean_score == pytest.approx(0.75)
