"""Skill proposer: compiles recurring wiki patterns into SKILL.md drafts.

The LLM is an injected async callable (prompt -> full ``SKILL.md`` text),
never a hard dependency: callers wire it to the model service, tests mock
it. Malformed generations are dropped without touching disk or pattern
state — "model proposes, code disposes".

The proposer only WRITES the draft; it never changes pattern status.
Promotion of the source patterns happens after the gate accepts the skill
(see :meth:`core.skill_evolution.service.SkillEvolutionService.evolve`).
A rejected proposal keeps its knowledge, but its source patterns go on a
cooldown (:meth:`SkillProposer.record_rejection`) so the same broken
synthesis is not re-proposed — and re-validated at real eval cost — on
every cycle.
"""

from __future__ import annotations

import time
from collections.abc import Awaitable, Callable

from core.observability.logging import get_logger
from core.plugins.declarative import split_frontmatter
from core.skill_evolution.store import PatternStore
from core.skill_evolution.types import (
    Pattern,
    PatternStatus,
    SkillProposal,
)
from core.skill_evolution.writer import ManagedSkillWriter

logger = get_logger(__name__)

__all__ = ["SkillProposer"]

_INSTRUCTIONS = """\
You are compiling recurring agent execution patterns into one reusable skill.

Write a complete SKILL.md document and output NOTHING else:
- YAML frontmatter between two '---' lines with exactly two keys:
  'name' (lowercase kebab-case, max 80 chars) and
  'description' (one sentence, max 200 chars).
- Then a Markdown body with concrete, actionable instructions that prevent
  the failure modes and apply the strategies listed below.
"""


class SkillProposer:
    """Select ripe patterns and synthesize a managed skill from them."""

    def __init__(
        self,
        store: PatternStore,
        writer: ManagedSkillWriter,
        *,
        generate: Callable[[str], Awaitable[str]],
        min_occurrences: int = 2,
        max_patterns: int = 5,
        rejection_cooldown_seconds: float = 86_400.0,
        now: Callable[[], float] = time.time,
    ) -> None:
        """Initialize the proposer.

        Args:
            store: Wiki pattern store to read candidates from.
            writer: Managed-skill writer receiving accepted drafts.
            generate: Async LLM callable ``prompt -> SKILL.md text``.
            min_occurrences: A pattern must recur at least this many times
                before it is worth compiling into a skill.
            max_patterns: Cap on patterns folded into one proposal.
            rejection_cooldown_seconds: How long a pattern that fed a
                gate-rejected proposal is excluded from new proposals.
                Without a cooldown the same broken synthesis is re-proposed
                (and re-validated, at real eval cost) forever.
            now: Clock override (tests).
        """
        self._store = store
        self._writer = writer
        self._generate = generate
        self._min_occurrences = min_occurrences
        self._max_patterns = max_patterns
        self._rejection_cooldown = rejection_cooldown_seconds
        self._now = now
        #: pattern id -> rejection timestamp (in-process rejected-edit buffer)
        self._rejected_at: dict[str, float] = {}

    def record_rejection(self, pattern_ids: list[str]) -> None:
        """Put the source patterns of a rejected proposal on cooldown."""
        stamp = self._now()
        for pattern_id in pattern_ids:
            self._rejected_at[pattern_id] = stamp

    def _cooling_down(self, pattern_id: str) -> bool:
        rejected_at = self._rejected_at.get(pattern_id)
        if rejected_at is None:
            return False
        if self._now() - rejected_at >= self._rejection_cooldown:
            del self._rejected_at[pattern_id]
            return False
        return True

    async def propose(self) -> SkillProposal | None:
        """Run one propose step: select patterns, generate, validate, write.

        Returns:
            The written proposal (source pattern ids attached, statuses
            untouched), or None when no pattern is ripe or the generation
            is malformed/unwritable — nothing is written in that case.
        """
        candidates = await self._select_candidates()
        if not candidates:
            return None

        prompt = f"{_INSTRUCTIONS}\n{self._render_context(candidates)}"
        try:
            generated = await self._generate(prompt)
        except Exception as exc:
            logger.warning("Skill generation failed: %s", exc)
            return None

        proposal = self._parse(generated, candidates)
        if proposal is None:
            return None

        try:
            await self._writer.write(proposal)
        except (ValueError, OSError) as exc:
            logger.warning("Generated skill rejected by writer: %s", exc)
            return None
        return proposal

    async def _select_candidates(self) -> list[Pattern]:
        patterns = await self._store.list_patterns(
            status=PatternStatus.CANDIDATE, limit=self._max_patterns
        )
        return [
            p
            for p in patterns
            if p.occurrences >= self._min_occurrences and not self._cooling_down(p.id)
        ]

    def _render_context(self, patterns: list[Pattern]) -> str:
        """Render the selected patterns as the generation context block."""
        blocks = [
            f"### Pattern ({p.kind.value}, observed {p.occurrences}x)\n"
            f"{p.title}\n{p.summary}"
            for p in patterns
        ]
        return "\n\n".join(blocks)

    def _parse(self, generated: str, sources: list[Pattern]) -> SkillProposal | None:
        """Parse and validate a generated SKILL.md; None when malformed."""
        try:
            front, body = split_frontmatter(generated.strip())
            return SkillProposal(
                name=str(front.get("name", "")),
                description=str(front.get("description", "")),
                body=body,
                source_pattern_ids=[p.id for p in sources],
            )
        except Exception as exc:
            logger.warning("Generated skill is malformed, dropping: %s", exc)
            return None
