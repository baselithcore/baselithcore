"""Skill evolution: compile agent experience into persistent knowledge.

Experience-driven loop: outcomes are distilled into a deduplicated
pattern store (the wiki layer), patterns are compiled into declarative
``SKILL.md`` skills, and every new skill version passes a validation
gate that can roll the skill back while the wiki persists.
"""

from __future__ import annotations

from core.skill_evolution.gating import SkillGate
from core.skill_evolution.impact import SkillImpactTracker
from core.skill_evolution.maintainer import WikiMaintainer
from core.skill_evolution.proposer import SkillProposer
from core.skill_evolution.service import (
    SkillEvolutionService,
    build_skill_evolution_service,
    make_activation_guard,
)
from core.skill_evolution.store import InMemoryPatternStore, PatternStore
from core.skill_evolution.types import (
    MAX_EVIDENCE,
    SKILL_NAME_PATTERN,
    EvidenceRef,
    GateDecision,
    Pattern,
    PatternKind,
    PatternStatus,
    SkillImpact,
    SkillProposal,
)
from core.skill_evolution.writer import ManagedSkillWriter

__all__ = [
    "MAX_EVIDENCE",
    "SKILL_NAME_PATTERN",
    "EvidenceRef",
    "GateDecision",
    "InMemoryPatternStore",
    "ManagedSkillWriter",
    "Pattern",
    "PatternKind",
    "PatternStatus",
    "PatternStore",
    "SkillEvolutionService",
    "SkillGate",
    "SkillImpact",
    "SkillImpactTracker",
    "SkillProposal",
    "SkillProposer",
    "WikiMaintainer",
    "build_skill_evolution_service",
    "make_activation_guard",
]
