"""Pattern store: the persistent wiki layer of the skill-evolution loop.

Dedup rule (the whole point vs. free-form memory strings): patterns are
keyed by failure fingerprint. Re-observing a known fingerprint merges into
the existing pattern (occurrence count + evidence) instead of inserting a
near-duplicate row.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from typing import Protocol, runtime_checkable

from core.skill_evolution.types import (
    Pattern,
    PatternKind,
    PatternStatus,
)

__all__ = ["InMemoryPatternStore", "PatternStore"]


@runtime_checkable
class PatternStore(Protocol):
    """Persistence contract for wiki patterns."""

    async def upsert(self, pattern: Pattern) -> Pattern:
        """Insert *pattern* or merge it into the one sharing its fingerprint.

        A merge counts as ONE observation (``occurrences + 1``) and appends
        all of the incoming pattern's evidence entries (capped, newest win).

        Returns:
            The stored pattern (the merged existing one on dedup).
        """
        ...

    async def get(self, pattern_id: str) -> Pattern | None:
        """Return the pattern with ``pattern_id``, or None."""
        ...

    async def list_patterns(
        self,
        *,
        kind: PatternKind | None = None,
        status: PatternStatus | None = None,
        limit: int = 50,
    ) -> list[Pattern]:
        """List patterns, most-observed first (then most recently updated)."""
        ...

    async def set_status(self, pattern_id: str, status: PatternStatus) -> bool:
        """Move a pattern through its lifecycle. False when unknown id."""
        ...


class InMemoryPatternStore:
    """Volatile :class:`PatternStore` for tests and Postgres-less deployments.

    NOT tenant-aware (patterns from every tenant share one namespace),
    unlike the Postgres backend — see the tenancy guard in
    :mod:`core.skill_evolution.service`.
    """

    def __init__(self) -> None:
        self._patterns: dict[str, Pattern] = {}
        self._lock = asyncio.Lock()

    async def upsert(self, pattern: Pattern) -> Pattern:
        async with self._lock:
            existing = next(
                (
                    p
                    for p in self._patterns.values()
                    if p.fingerprint == pattern.fingerprint
                ),
                None,
            )
            if existing is None:
                self._patterns[pattern.id] = pattern
                return pattern
            merged = existing.merge_observations(pattern.evidence)
            self._patterns[existing.id] = merged
            return merged

    async def get(self, pattern_id: str) -> Pattern | None:
        return self._patterns.get(pattern_id)

    async def list_patterns(
        self,
        *,
        kind: PatternKind | None = None,
        status: PatternStatus | None = None,
        limit: int = 50,
    ) -> list[Pattern]:
        rows = [
            p
            for p in self._patterns.values()
            if (kind is None or p.kind is kind)
            and (status is None or p.status is status)
        ]
        rows.sort(key=lambda p: (p.occurrences, p.updated_at), reverse=True)
        return rows[:limit]

    async def set_status(self, pattern_id: str, status: PatternStatus) -> bool:
        async with self._lock:
            pattern = self._patterns.get(pattern_id)
            if pattern is None:
                return False
            self._patterns[pattern_id] = pattern.model_copy(
                update={"status": status, "updated_at": datetime.now(UTC)}
            )
            return True
