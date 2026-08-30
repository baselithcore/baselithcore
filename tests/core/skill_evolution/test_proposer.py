"""Unit tests for SkillProposer (LLM mocked as async callables)."""

from __future__ import annotations

from pathlib import Path

from core.skill_evolution.proposer import SkillProposer
from core.skill_evolution.store import InMemoryPatternStore
from core.skill_evolution.types import Pattern, PatternKind, PatternStatus
from core.skill_evolution.writer import ManagedSkillWriter

VALID_SKILL = """---
name: avoid-utf8-assumption
description: Never assume UTF-8 when parsing uploads
---

Check encoding before parsing.
"""


def _pattern(fingerprint: str) -> Pattern:
    return Pattern(
        fingerprint=fingerprint,
        kind=PatternKind.FAILURE_MODE,
        title=f"Failure {fingerprint}",
        summary="parser assumed UTF-8",
    )


async def _seed(store: InMemoryPatternStore, times: int, fingerprint: str) -> Pattern:
    last = await store.upsert(_pattern(fingerprint))
    for _ in range(times - 1):
        last = await store.upsert(_pattern(fingerprint))
    return last


async def test_no_proposal_below_occurrence_threshold(tmp_path: Path) -> None:
    calls: list[str] = []

    async def generate(prompt: str) -> str:
        calls.append(prompt)
        return VALID_SKILL

    store = InMemoryPatternStore()
    await _seed(store, times=1, fingerprint="fp1")
    proposer = SkillProposer(
        store, ManagedSkillWriter(tmp_path), generate=generate, min_occurrences=2
    )
    assert await proposer.propose() is None
    assert calls == []


async def test_valid_generation_writes_skill_without_promoting(tmp_path: Path) -> None:
    async def generate(prompt: str) -> str:
        assert "parser assumed UTF-8" in prompt
        return VALID_SKILL

    store = InMemoryPatternStore()
    seeded = await _seed(store, times=2, fingerprint="fp1")
    writer = ManagedSkillWriter(tmp_path)
    proposer = SkillProposer(store, writer, generate=generate, min_occurrences=2)

    proposal = await proposer.propose()
    assert proposal is not None
    assert proposal.name == "avoid-utf8-assumption"
    assert proposal.source_pattern_ids == [seeded.id]
    assert (tmp_path / "avoid-utf8-assumption" / "SKILL.md").exists()
    # Promotion is the gate's job (after acceptance), never the proposer's.
    stored = await store.get(seeded.id)
    assert stored is not None and stored.status is PatternStatus.CANDIDATE


async def test_garbage_generation_writes_nothing(tmp_path: Path) -> None:
    async def generate(_prompt: str) -> str:
        return "no frontmatter here"

    store = InMemoryPatternStore()
    seeded = await _seed(store, times=2, fingerprint="fp1")
    proposer = SkillProposer(
        store, ManagedSkillWriter(tmp_path), generate=generate, min_occurrences=2
    )
    assert await proposer.propose() is None
    assert list(tmp_path.iterdir()) == []
    stored = await store.get(seeded.id)
    assert stored is not None and stored.status is PatternStatus.CANDIDATE


async def test_invalid_generated_name_rejected(tmp_path: Path) -> None:
    async def generate(_prompt: str) -> str:
        return '---\nname: "Invalid Name"\ndescription: d\n---\n\nbody\n'

    store = InMemoryPatternStore()
    await _seed(store, times=2, fingerprint="fp1")
    proposer = SkillProposer(
        store, ManagedSkillWriter(tmp_path), generate=generate, min_occurrences=2
    )
    assert await proposer.propose() is None
    assert list(tmp_path.iterdir()) == []


async def test_generate_exception_is_fail_soft(tmp_path: Path) -> None:
    async def generate(_prompt: str) -> str:
        raise RuntimeError("llm down")

    store = InMemoryPatternStore()
    await _seed(store, times=2, fingerprint="fp1")
    proposer = SkillProposer(
        store, ManagedSkillWriter(tmp_path), generate=generate, min_occurrences=2
    )
    assert await proposer.propose() is None
